"""
netbox_dashboard_ingest.py
==========================
Pulls a snapshot of devices, interfaces, and optics from all NetBox sites and
writes it to Postgres. Drives the live dashboard.

Sites are discovered dynamically via site_list GraphQL query. If discovery
fails or returns no results, falls back to _FALLBACK_SITE_DH_MAP (Ellendale
DH201-204). All sites are queried in parallel (up to 5 workers).

Per site with locations: for each data-hall location slug, three paginated
GraphQL lists (device_list, interface_list, inventory_item_list) using
`pagination: {start, limit}` (see Source_count_Netbox._graphql_paginated) so
large sites are not truncated at NetBox's 1000-row cap. Then three paginated
site-wide lists for devices/interfaces/optics not already covered by those
per-location passes. Sites with no NetBox locations ingest via the site-wide
lists only.

Run modes:
    - As a module: ingest_snapshot() discovers sites, queries in parallel,
      bulk-inserts, marks the snapshot 'ok' or 'failed'.
    - As a script: python netbox_dashboard_ingest.py runs one ingestion and
      prints the summary.

Reuses from Source_count_Netbox: `_graphql_paginated`, `_graphql_with_retry`
(transient NetBox errors), `_iface_label`, and `_test_netbox_reachable`.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from atlas_data_loader import managed_connection
from Source_count_Netbox import (
    _graphql_paginated,
    _graphql_with_retry,
    _iface_label,
    _test_netbox_reachable,
)


# ---------------------------------------------------------------------------
# NetBox 4 slug -> legacy enum normalization
# ---------------------------------------------------------------------------
# NetBox 4.x GraphQL returns interface.type as a kebab-case slug
# (e.g. "1000base-t", "100gbase-x-qsfp28", "400gbase-x-qsfpdd"). The
# INTERFACE_TYPE_LABELS dict in Source_count_Netbox.py is keyed on the
# legacy TYPE_X_Y enum form. Convert before lookup.
_SLUG_ENUM_ALIASES = {
    "TYPE_400GBASE_X_QSFPDD": "TYPE_400GBASE_X_QSFP_DD",
    "TYPE_800GBASE_X_QSFPDD": "TYPE_800GBASE_X_QSFP_DD",
    "TYPE_400GE_QSFPDD":      "TYPE_400GE_QSFP_DD",
    "TYPE_800GE_QSFPDD":      "TYPE_800GE_QSFP_DD",
}


def _slug_to_enum(slug: str) -> str:
    """Convert a NetBox 4 interface type slug to the legacy TYPE_X_Y form."""
    if not slug:
        return "TYPE_UNKNOWN"
    if slug.startswith("TYPE_"):
        return slug
    enum = "TYPE_" + slug.upper().replace("-", "_")
    return _SLUG_ENUM_ALIASES.get(enum, enum)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Introspection guard
# ---------------------------------------------------------------------------

_INTROSPECTION_QUERY = """
{
  __schema {
    queryType {
      fields {
        name
      }
    }
  }
}
"""


def _validate_inventory_item_list() -> None:
    """Confirm NetBox schema exposes inventory_item_list before building anything.

    Raises RuntimeError (with available field list) if the field is absent so
    callers get a clear diagnostic instead of a silent empty result.
    """
    try:
        body = _graphql_with_retry(_INTROSPECTION_QUERY)
    except RuntimeError as exc:
        raise RuntimeError(f"GraphQL introspection failed: {exc}") from exc

    if "errors" in body:
        raise RuntimeError(f"GraphQL introspection errors: {body['errors']}")

    query_type = ((body.get("data") or {}).get("__schema") or {}).get("queryType") or {}
    fields = {f["name"] for f in (query_type.get("fields") or [])}

    if "inventory_item_list" not in fields:
        raise RuntimeError(
            "inventory_item_list not found in NetBox GraphQL schema — "
            "optic ingestion is not supported on this NetBox version. "
            f"Available root query fields: {sorted(fields)}"
        )
    log.info("Introspection OK: inventory_item_list is available")


# ---------------------------------------------------------------------------
# Config / fallback
# ---------------------------------------------------------------------------

# Known-good fallback used if site discovery fails.
# Verified against NetBox 2026-04-27; Ellendale DH201-204.
_FALLBACK_SITE_DH_MAP: Dict[str, List[str]] = {
    "us-central-08a": ["data-hall-202", "data-hall-204"],   # Heron
    "us-central-08b": ["data-hall-201", "data-hall-203"],   # Phoenix
}

# Backward-compat alias — netbox_dashboard_routes.py references ingest.SITE_DH_MAP.
SITE_DH_MAP = _FALLBACK_SITE_DH_MAP

ACTIVE_STATUSES = {"active", "provisioned"}


# ---------------------------------------------------------------------------
# Site discovery
# ---------------------------------------------------------------------------

_SITE_LIST_QUERY = """
{
  site_list {
    name
    slug
    locations {
      slug
    }
  }
}
"""


def _discover_sites() -> Dict[str, List[str]]:
    """Query NetBox for all sites and their location slugs.

    Returns {site_slug: [location_slug, ...]} for sites that have at least
    one location. Falls back to _FALLBACK_SITE_DH_MAP on empty result or error.
    """
    try:
        body = _graphql_with_retry(_SITE_LIST_QUERY)
    except RuntimeError as exc:
        log.warning(
            "Site discovery failed, using Ellendale-specific fallback map: %s", exc
        )
        return dict(_FALLBACK_SITE_DH_MAP)

    if "errors" in body:
        log.warning(
            "Site discovery errors, using Ellendale-specific fallback map: %s",
            body["errors"],
        )
        return dict(_FALLBACK_SITE_DH_MAP)

    result: Dict[str, List[str]] = {}
    for site in (body.get("data") or {}).get("site_list") or []:
        slug = site.get("slug") or ""
        if not slug:
            continue
        loc_slugs = [
            loc["slug"]
            for loc in (site.get("locations") or [])
            if loc.get("slug")
        ]
        # Include sites with zero locations — _query_site relies on site-wide
        # queries when location_slugs is empty.
        result[slug] = loc_slugs

    if not result:
        log.warning(
            "Site discovery returned no sites, using Ellendale-specific fallback map"
        )
        return dict(_FALLBACK_SITE_DH_MAP)

    total_locs = sum(len(v) for v in result.values())
    for slug, locs in sorted(result.items()):
        log.info("NetBox ingest site %r -> %d location(s)", slug, len(locs))
    log.info("Discovered %d sites with %d total locations", len(result), total_locs)
    return result


# ---------------------------------------------------------------------------
# Optic name-pattern filter
# ---------------------------------------------------------------------------

_OPTIC_NAME_PATTERNS = ("sfp", "qsfp", "osfp", "transceiver", "xcvr", "optic")


def _is_optic(name: str) -> bool:
    lower = name.lower()
    return any(p in lower for p in _OPTIC_NAME_PATTERNS)


# ---------------------------------------------------------------------------
# GraphQL — per-location queries (paginated lists per location)
# ---------------------------------------------------------------------------

def _build_location_devices_query(site_slug: str, location_slug: str, start: int, limit: int) -> str:
    safe_site = site_slug.replace('"', '\\"')
    safe_loc = location_slug.replace('"', '\\"')
    return f"""
    {{
      device_list(
        filters: {{
          site: {{ slug: {{ exact: "{safe_site}" }} }}
          location: {{ slug: {{ exact: "{safe_loc}" }} }}
        }}
        pagination: {{ start: {start}, limit: {limit} }}
      ) {{
        id
        name
        serial
        status
        device_type {{ display }}
        location {{ slug }}
        rack {{ name }}
        position
      }}
    }}
    """


def _build_location_interfaces_query(site_slug: str, location_slug: str, start: int, limit: int) -> str:
    safe_site = site_slug.replace('"', '\\"')
    safe_loc = location_slug.replace('"', '\\"')
    return f"""
    {{
      interface_list(
        filters: {{
          device: {{
            site: {{ slug: {{ exact: "{safe_site}" }} }}
            location: {{ slug: {{ exact: "{safe_loc}" }} }}
          }}
          NOT: {{ type: {{ exact: TYPE_VIRTUAL }} }}
        }}
        pagination: {{ start: {start}, limit: {limit} }}
      ) {{
        id
        name
        type
        device {{
          name
          status
          location {{ slug }}
          rack {{ name }}
          position
        }}
      }}
    }}
    """


def _build_location_optics_query(site_slug: str, location_slug: str, start: int, limit: int) -> str:
    safe_site = site_slug.replace('"', '\\"')
    safe_loc = location_slug.replace('"', '\\"')
    return f"""
    {{
      inventory_item_list(
        filters: {{
          device: {{
            site: {{ slug: {{ exact: "{safe_site}" }} }}
            location: {{ slug: {{ exact: "{safe_loc}" }} }}
          }}
        }}
        pagination: {{ start: {start}, limit: {limit} }}
      ) {{
        id
        name
        part_id
        serial
        description
        manufacturer {{ name }}
        device {{
          name
          location {{ slug }}
        }}
      }}
    }}
    """


def _query_site(
    site_slug: str,
    location_slugs: List[str],
    active_only: bool = True,
) -> Tuple[List[tuple], List[tuple], List[tuple]]:
    """Query one site per-location with paginated GraphQL lists.

    For each location slug: paginated device_list, interface_list, and
    inventory_item_list. Then paginated site-wide passes for rows not already
    covered by those per-location lists (typically devices without a modeled
    location in the known_locs set).

    Returns (device_rows, interface_rows, optic_rows).
    """
    log.info("Querying site %s (%d locations)", site_slug, len(location_slugs))
    known_locs = set(location_slugs)

    device_rows: List[tuple] = []
    interface_rows: List[tuple] = []
    optic_rows: List[tuple] = []

    # --- per-location queries ---
    for loc_slug in location_slugs:
        dev_list = _graphql_paginated(
            "device_list",
            lambda s, l, _site=site_slug, _loc=loc_slug: _build_location_devices_query(_site, _loc, s, l),
        )
        iface_list = _graphql_paginated(
            "interface_list",
            lambda s, l, _site=site_slug, _loc=loc_slug: _build_location_interfaces_query(_site, _loc, s, l),
        )
        optic_list = _graphql_paginated(
            "inventory_item_list",
            lambda s, l, _site=site_slug, _loc=loc_slug: _build_location_optics_query(_site, _loc, s, l),
        )

        for d in dev_list:
            status = d.get("status") or ""
            if active_only and status not in ACTIVE_STATUSES:
                continue
            raw_pos = d.get("position")
            try:
                pos = int(float(raw_pos)) if raw_pos is not None and str(raw_pos).strip() else None
            except (TypeError, ValueError):
                pos = None
            device_rows.append((
                site_slug,
                (d.get("location") or {}).get("slug") or "",
                (d.get("rack") or {}).get("name") or "",
                pos,
                d.get("name") or "",
                (d.get("device_type") or {}).get("display") or "Unknown",
                d.get("serial") or "",
                status,
            ))

        for i in iface_list:
            raw_type = i.get("type") or ""
            type_enum = _slug_to_enum(raw_type)
            if type_enum == "TYPE_VIRTUAL":
                continue
            dev = i.get("device") or {}
            dev_status = dev.get("status") or ""
            if active_only and dev_status not in ACTIVE_STATUSES:
                continue
            raw_pos = dev.get("position")
            try:
                pos = int(float(raw_pos)) if raw_pos is not None and str(raw_pos).strip() else None
            except (TypeError, ValueError):
                pos = None
            category, label = _iface_label(type_enum)
            interface_rows.append((
                site_slug,
                (dev.get("location") or {}).get("slug") or "",
                (dev.get("rack") or {}).get("name") or "",
                pos,
                dev.get("name") or "",
                i.get("name") or "",
                type_enum,
                label,
                category,
                dev_status,
            ))

        for item in optic_list:
            name = item.get("name") or ""
            if not _is_optic(name):
                continue
            dev = item.get("device") or {}
            optic_rows.append((
                site_slug,
                (dev.get("location") or {}).get("slug") or "",
                dev.get("name") or "",
                name,
                item.get("part_id") or "",
                item.get("serial") or "",
                (item.get("manufacturer") or {}).get("name") or "",
                item.get("description") or "",
            ))

    # --- unassigned pass: site-level query, skip devices whose location is
    #     already captured by the per-location queries above ---
    safe = site_slug.replace('"', '\\"')

    def _build_site_devices(start: int, limit: int, _safe=safe) -> str:
        return f"""
        {{
          device_list(
            filters: {{ site: {{ slug: {{ exact: "{_safe}" }} }} }}
            pagination: {{ start: {start}, limit: {limit} }}
          ) {{
            id
            name
            serial
            status
            device_type {{ display }}
            location {{ slug }}
            rack {{ name }}
            position
          }}
        }}
        """

    def _build_site_interfaces(start: int, limit: int, _safe=safe) -> str:
        return f"""
        {{
          interface_list(
            filters: {{
              device: {{ site: {{ slug: {{ exact: "{_safe}" }} }} }}
              NOT: {{ type: {{ exact: TYPE_VIRTUAL }} }}
            }}
            pagination: {{ start: {start}, limit: {limit} }}
          ) {{
            id
            name
            type
            device {{
              name
              status
              location {{ slug }}
              rack {{ name }}
              position
            }}
          }}
        }}
        """

    def _build_site_optics(start: int, limit: int, _safe=safe) -> str:
        return f"""
        {{
          inventory_item_list(
            filters: {{ device: {{ site: {{ slug: {{ exact: "{_safe}" }} }} }} }}
            pagination: {{ start: {start}, limit: {limit} }}
          ) {{
            id
            name
            part_id
            serial
            description
            manufacturer {{ name }}
            device {{
              name
              location {{ slug }}
            }}
          }}
        }}
        """

    udev_list = _graphql_paginated("device_list", _build_site_devices)
    uiface_list = _graphql_paginated("interface_list", _build_site_interfaces)
    uoptic_list = _graphql_paginated("inventory_item_list", _build_site_optics)

    for d in udev_list:
        loc_slug = (d.get("location") or {}).get("slug") or ""
        if loc_slug in known_locs:
            continue  # already captured per-location
        status = d.get("status") or ""
        if active_only and status not in ACTIVE_STATUSES:
            continue
        raw_pos = d.get("position")
        try:
            pos = int(float(raw_pos)) if raw_pos is not None and str(raw_pos).strip() else None
        except (TypeError, ValueError):
            pos = None
        # Keep the device's real location slug. This pass only runs for rows
        # not covered by per-location queries; blank slug here was mis-bucketing
        # devices into a synthetic "" DH on the dashboard.
        device_rows.append((
            site_slug,
            loc_slug,
            (d.get("rack") or {}).get("name") or "",
            pos,
            d.get("name") or "",
            (d.get("device_type") or {}).get("display") or "Unknown",
            d.get("serial") or "",
            status,
        ))

    for i in uiface_list:
        raw_type = i.get("type") or ""
        type_enum = _slug_to_enum(raw_type)
        if type_enum == "TYPE_VIRTUAL":
            continue
        dev = i.get("device") or {}
        dev_loc = (dev.get("location") or {}).get("slug") or ""
        if dev_loc in known_locs:
            continue  # already captured per-location
        dev_status = dev.get("status") or ""
        if active_only and dev_status not in ACTIVE_STATUSES:
            continue
        raw_pos = dev.get("position")
        try:
            pos = int(float(raw_pos)) if raw_pos is not None and str(raw_pos).strip() else None
        except (TypeError, ValueError):
            pos = None
        category, label = _iface_label(type_enum)
        interface_rows.append((
            site_slug,
            dev_loc,
            (dev.get("rack") or {}).get("name") or "",
            pos,
            dev.get("name") or "",
            i.get("name") or "",
            type_enum,
            label,
            category,
            dev_status,
        ))

    for item in uoptic_list:
        name = item.get("name") or ""
        if not _is_optic(name):
            continue
        dev = item.get("device") or {}
        dev_loc = (dev.get("location") or {}).get("slug") or ""
        if dev_loc in known_locs:
            continue  # already captured per-location
        optic_rows.append((
            site_slug,
            dev_loc,
            dev.get("name") or "",
            name,
            item.get("part_id") or "",
            item.get("serial") or "",
            (item.get("manufacturer") or {}).get("name") or "",
            item.get("description") or "",
        ))

    log.info("  %s: %d devices, %d interfaces, %d optics (all locations)",
             site_slug, len(device_rows), len(interface_rows), len(optic_rows))
    return device_rows, interface_rows, optic_rows


# ---------------------------------------------------------------------------
# Postgres writes
# ---------------------------------------------------------------------------

DEVICE_COLS = (
    "snapshot_id", "site", "location_slug", "rack", "position",
    "name", "model", "serial", "status",
)

INTERFACE_COLS = (
    "snapshot_id", "site", "location_slug", "rack", "position",
    "device_name", "interface_name", "type_enum", "type_label",
    "type_category", "device_status",
)

OPTIC_COLS = (
    "snapshot_id", "site", "location_slug", "device_name",
    "name", "part_id", "serial", "manufacturer", "description",
)


def _open_snapshot(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO netbox_snapshots (started_at, status) "
            "VALUES (now(), 'running') RETURNING id"
        )
        return cur.fetchone()[0]


def _close_snapshot(
    conn,
    snapshot_id: int,
    status: str,
    device_count: int,
    interface_count: int,
    optic_count: int,
    site_count: int,
    sites_failed: int,
    sites_json: str,
    duration_ms: int,
    error_message: Optional[str] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE netbox_snapshots
            SET finished_at = now(),
                status = %s,
                device_count = %s,
                interface_count = %s,
                optic_count = %s,
                site_count = %s,
                sites_failed = %s,
                sites_json = %s::jsonb,
                duration_ms = %s,
                error_message = %s
            WHERE id = %s
            """,
            (status, device_count, interface_count, optic_count,
             site_count, sites_failed, sites_json,
             duration_ms, error_message, snapshot_id),
        )


def _bulk_insert(conn, table: str, columns: Tuple[str, ...], rows: List[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        col_list = ", ".join(columns)
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {table} ({col_list}) VALUES %s",
            rows,
            page_size=500,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_snapshot(active_only: bool = True) -> Dict[str, object]:
    """
    Discover all NetBox sites and run a full parallel snapshot.
    Returns a summary dict suitable for logging or surfacing in the manual-refresh API.

    Always opens a snapshot row, even on failure, so the dashboard has a
    failure record to point at.
    """
    if not os.getenv("NETBOX_API_TOKEN", "").strip():
        raise RuntimeError("NETBOX_API_TOKEN is not set")

    if not _test_netbox_reachable():
        raise RuntimeError("NetBox is not reachable")

    _validate_inventory_item_list()
    site_map = _discover_sites()
    log.info(
        "NetBox ingest will query %d site(s): %s",
        len(site_map),
        ", ".join(sorted(site_map.keys())),
    )

    started = time.monotonic()

    # --- parallel per-site queries (outside DB transaction) ---
    def _ingest_site(slug: str):
        try:
            return slug, _query_site(slug, site_map[slug], active_only=active_only)
        except Exception as exc:
            log.error("Failed to ingest site %s: %s", slug, exc)
            return slug, None

    site_results: Dict[str, Tuple] = {}
    failed_sites: List[str] = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_ingest_site, slug): slug for slug in site_map}
        for future in as_completed(futures):
            slug, result = future.result()
            if result is None:
                failed_sites.append(slug)
            else:
                site_results[slug] = result

    # --- flatten results ---
    all_device_rows: List[tuple] = []
    all_interface_rows: List[tuple] = []
    all_optic_rows: List[tuple] = []
    sites_summary = []
    for slug, (devs, ifaces, optics) in sorted(site_results.items()):
        all_device_rows.extend(devs)
        all_interface_rows.extend(ifaces)
        all_optic_rows.extend(optics)
        sites_summary.append({
            "slug": slug,
            "devices": len(devs),
            "interfaces": len(ifaces),
            "optics": len(optics),
        })

    device_total = len(all_device_rows)
    interface_total = len(all_interface_rows)
    optic_total = len(all_optic_rows)
    snapshot_id: Optional[int] = None

    # --- DB writes ---
    with managed_connection() as conn:
        try:
            snapshot_id = _open_snapshot(conn)
            conn.commit()

            _bulk_insert(conn, "netbox_devices",    DEVICE_COLS,    [(snapshot_id, *r) for r in all_device_rows])
            _bulk_insert(conn, "netbox_interfaces", INTERFACE_COLS, [(snapshot_id, *r) for r in all_interface_rows])
            _bulk_insert(conn, "netbox_optics",     OPTIC_COLS,     [(snapshot_id, *r) for r in all_optic_rows])

            duration_ms = int((time.monotonic() - started) * 1000)
            _close_snapshot(
                conn, snapshot_id, "ok",
                device_total, interface_total, optic_total,
                len(site_results), len(failed_sites),
                json.dumps(sites_summary),
                duration_ms,
            )
            conn.commit()

            return {
                "snapshot_id": snapshot_id,
                "status": "ok",
                "device_count": device_total,
                "interface_count": interface_total,
                "optic_count": optic_total,
                "site_count": len(site_results),
                "sites_failed": failed_sites,
                "duration_ms": duration_ms,
            }

        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, psycopg2.Error) as exc:
            conn.rollback()
            duration_ms = int((time.monotonic() - started) * 1000)
            if snapshot_id is not None:
                try:
                    _close_snapshot(
                        conn, snapshot_id, "failed",
                        device_total, interface_total, optic_total,
                        len(site_results), len(failed_sites),
                        json.dumps(sites_summary),
                        duration_ms,
                        error_message=str(exc)[:1000],
                    )
                    conn.commit()
                except psycopg2.Error:
                    log.exception("Failed to mark snapshot as failed")
                    conn.rollback()
            log.exception("NetBox snapshot ingestion failed")
            raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = ingest_snapshot()
    print(json.dumps(summary, indent=2, default=str))
