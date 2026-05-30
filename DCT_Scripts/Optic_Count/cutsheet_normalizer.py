"""
cutsheet_normalizer.py

Preprocesses raw cutsheet DataFrames into two clean, normalized structures
before feeding them to the LLM:

1. Device Inventory  - One row per physical device, deduped by (dns_name, loc_cab_ru, model).
2. Connection Table  - Adjacency list of A-device <-> Z-device links with optic/port/breakout info.

Also tags each data row with its cutsheet section (e.g. "TIER-1 TO TIER-0 GG1 A1")
so the LLM can reason about network topology tiers.

NOTE: All column name resolution and model/status normalization is delegated to
cutsheet_profiles.py (Canon constants, canonicalize(), normalize_model(), etc.).
This file does NOT maintain its own alias dictionaries.
"""

import logging
import os

import pandas as pd
from typing import Dict, List, Any, Optional, Tuple

from cutsheet_profiles import (
    Canon,
    STATUS_NORMALIZATION,
    canonicalize,
    normalize_model,
    normalize_status,
)

log = logging.getLogger(__name__)


# Optional Cython-compiled inner loop. Built by setup.py in the Docker
# builder stage; ATLAS_NORMALIZER_FORCE_PY=1 disables it for benchmarks.
_FAST_PROCESS_ROWS = None
if not os.getenv("ATLAS_NORMALIZER_FORCE_PY"):
    try:
        from cutsheet_normalizer_fast import process_rows as _FAST_PROCESS_ROWS  # type: ignore
        log.info("cutsheet_normalizer: using Cython fast path")
    except ImportError:
        log.info("cutsheet_normalizer: fast extension not built, using pure Python")


# ---------------------------------------------------------------------------
# Cell-level helpers
# ---------------------------------------------------------------------------

def _normalize_cell(value) -> str:
    """Return cleaned string or empty string for null-ish values."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.casefold() in {"nan", "-", "none", "null", ""}:
        return ""
    return text


def _normalize_dns(raw) -> str:
    """Lowercase and strip whitespace for consistent DNS name comparison."""
    if pd.isna(raw) or not str(raw).strip():
        return ""
    text = str(raw).strip()
    if text.casefold() in {"nan", "-", "none", "null", ""}:
        return ""
    return text.lower()


def _normalize_model_cell(raw) -> str:
    """Resolve model aliases via cutsheet_profiles single source of truth."""
    if pd.isna(raw) or not str(raw).strip():
        return ""
    text = str(raw).strip()
    if text.casefold() in {"nan", "-", "none", "null", ""}:
        return ""
    return normalize_model(text)


# ---------------------------------------------------------------------------
# Section header detection and tagging
# ---------------------------------------------------------------------------

def _is_section_header_mask(df: pd.DataFrame) -> pd.Series:
    """Vectorized boolean mask: True for rows that are section headers.

    Section header rows have a STATUS value but no A-LOC:CAB:RU and no
    A-SIDE DEVICE NAME.  Uses Canon constants so this works regardless of
    which cutsheet profile was detected.

    Rows whose STATUS matches a known real status (from STATUS_NORMALIZATION)
    are excluded even when location/device are empty -- those are incomplete
    data rows, not section headers.
    """
    status_vals = (
        df.get(Canon.STATUS, pd.Series("", index=df.index))
        .fillna("").astype(str).str.strip()
    )
    a_loc_vals = (
        df.get(Canon.A_LOC_CAB_RU, pd.Series("", index=df.index))
        .fillna("").astype(str).str.strip()
    )
    a_dns_vals = (
        df.get(Canon.A_DEVICE, pd.Series("", index=df.index))
        .fillna("").astype(str).str.strip()
    )
    # A row is a candidate section header if STATUS is filled but both
    # A-LOC and A-DEVICE are empty.
    candidate = (status_vals != "") & (a_loc_vals == "") & (a_dns_vals == "")

    # Exclude rows where the STATUS value is a known real status
    # (lowercase lookup into STATUS_NORMALIZATION keys or canonical values).
    _known_canonical = set(STATUS_NORMALIZATION.values())
    is_known_status = status_vals.str.lower().isin(STATUS_NORMALIZATION) | status_vals.isin(_known_canonical)

    return candidate & ~is_known_status


# ---------------------------------------------------------------------------
# Breakout port grouping
# ---------------------------------------------------------------------------

def _parse_breakout_key(loc_cab_ru: str, port: str) -> str:
    """
    Build a breakout group key from device location + parent port.
    Multiple sub-connections (1:1:1, 1:1:2) under the same
    (loc_cab_ru, port) share one physical optic.
    """
    return f"{loc_cab_ru}|{port}"


# ---------------------------------------------------------------------------
# Core normalization
# ---------------------------------------------------------------------------

def normalize_cutsheet(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Main entry point. Takes a raw cutsheet DataFrame and returns:
    {
        "devices": [...],          # Unique physical devices
        "connections": [...],      # A-to-Z link adjacency list
        "sections": [...],         # Unique section labels found
        "stats": {...},            # Summary counts
    }

    Step 1: canonicalize() renames columns to Canon.* names and normalizes
            status/model values so all downstream code uses one vocabulary.
    """
    df = df.copy()

    # --- Canonicalize columns, status, and models in one shot ---
    df, profile_used = canonicalize(df, sheet_type="cutsheet")
    if profile_used:
        log.info("Normalizer detected profile: %s", profile_used.name)

    # --- Vectorized section tagging ---
    header_mask = _is_section_header_mask(df)
    status_vals = (
        df.get(Canon.STATUS, pd.Series("", index=df.index))
        .fillna("").astype(str).str.strip()
    )
    section_markers = pd.Series(pd.NA, index=df.index, dtype=object)
    section_markers[header_mask] = status_vals[header_mask]
    df["_section"] = section_markers.ffill().fillna("UNKNOWN")

    # Filter out section header rows and blank rows
    data_rows = df[~header_mask].copy()

    # Keep rows where at least one side has a location
    a_loc_present = data_rows.get(Canon.A_LOC_CAB_RU, pd.Series(dtype=str)).notna()
    z_loc_present = data_rows.get(Canon.Z_LOC_CAB_RU, pd.Series(dtype=str)).notna()
    data_rows = data_rows[a_loc_present | z_loc_present]

    records = data_rows.to_dict('records')

    if _FAST_PROCESS_ROWS is not None:
        a_cols = {
            "device": Canon.A_DEVICE, "loc": Canon.A_LOC_CAB_RU,
            "model": Canon.A_MODEL, "locode": Canon.A_LOCODE,
            "port": Canon.A_PORT, "optic": Canon.A_OPTIC,
        }
        z_cols = {
            "device": Canon.Z_DEVICE, "loc": Canon.Z_LOC_CAB_RU,
            "model": Canon.Z_MODEL, "locode": Canon.Z_LOCODE,
            "port": Canon.Z_PORT, "optic": Canon.Z_OPTIC,
        }
        aux_cols = {
            "status": Canon.STATUS,
            "cable": Canon.CABLE_ID,
            "a_brk_loc_space": "A-BREAKOUT LOC:CAB:RU",
            "a_brk_loc_nl": "A-BREAKOUT\nLOC:CAB:RU",
            "a_brk_slot_space": "A-BREAKOUT SLOT:PORT",
            "a_brk_slot_nl": "A-BREAKOUT\nSLOT:PORT",
            "z_brk_loc_space": "Z-BREAKOUT LOC:CAB:RU",
            "z_brk_loc_nl": "Z-BREAKOUT\nLOC:CAB:RU",
            "z_brk_slot_space": "Z-BREAKOUT SLOT:PORT",
            "z_brk_slot_nl": "Z-BREAKOUT\nSLOT:PORT",
        }
        devices, connections = _FAST_PROCESS_ROWS(
            records, a_cols, z_cols, aux_cols, normalize_model, _parse_breakout_key
        )
    else:
        devices = {}       # key: (dns_name, loc_cab_ru, model) -> device dict
        connections = []    # list of connection dicts
        breakout_seen = set()  # track breakout groups to avoid double-counting optics

        for row in records:
            section = row.get("_section", "UNKNOWN")
            status = _normalize_cell(row.get(Canon.STATUS))

            # --- Extract A-side and Z-side devices ---
            a_device = _extract_device(row, "A")
            z_device = _extract_device(row, "Z")

            # --- Register devices (dedup by identity tuple) ---
            if a_device:
                _register_device(devices, a_device, section, "A")
            if z_device:
                _register_device(devices, z_device, section, "Z")

            # --- Build connection record ---
            conn = _build_connection(row, a_device, z_device, section, status, breakout_seen)
            if conn:
                connections.append(conn)

    # Build final device list sorted by location
    device_list = sorted(devices.values(), key=lambda d: (d["loc_cab_ru"], d["dns_name"]))

    # Unique sections
    section_list = [s for s in df["_section"].unique() if s != "UNKNOWN"]

    return {
        "devices": device_list,
        "connections": connections,
        "sections": section_list,
        "stats": {
            "total_devices": len(device_list),
            "total_connections": len(connections),
            "total_sections": len(section_list),
            "raw_rows": len(df),
            "data_rows": len(data_rows),
            "section_header_rows": len(df) - len(data_rows),
        },
    }


# ---------------------------------------------------------------------------
# Device / connection extraction (all using Canon column names)
# ---------------------------------------------------------------------------

# Mapping from side prefix to Canon column accessors
_SIDE_COLS = {
    "A": {
        "device": Canon.A_DEVICE,
        "loc": Canon.A_LOC_CAB_RU,
        "model": Canon.A_MODEL,
        "locode": Canon.A_LOCODE,
    },
    "Z": {
        "device": Canon.Z_DEVICE,
        "loc": Canon.Z_LOC_CAB_RU,
        "model": Canon.Z_MODEL,
        "locode": Canon.Z_LOCODE,
    },
}


def _extract_device(row, side: str) -> Optional[Dict[str, str]]:
    """Pull device identity from one side of a cutsheet row."""
    cols = _SIDE_COLS[side]
    dns_name = _normalize_dns(row.get(cols["device"]))
    loc = _normalize_cell(row.get(cols["loc"]))
    model = _normalize_model_cell(row.get(cols["model"]))

    if not loc and not dns_name:
        return None

    return {
        "dns_name": dns_name,
        "loc_cab_ru": loc,
        "model": model,
        "locode": _normalize_cell(row.get(cols["locode"])),
    }


def _device_key(device: Dict[str, str]) -> Tuple[str, str, str]:
    """Unique identity for a physical device."""
    return (device["dns_name"], device["loc_cab_ru"], device["model"])


def _register_device(devices: dict, device: Dict[str, str], section: str, side: str):
    """Add or update a device in the inventory."""
    key = _device_key(device)
    if key not in devices:
        devices[key] = {
            "dns_name": device["dns_name"],
            "loc_cab_ru": device["loc_cab_ru"],
            "model": device["model"],
            "locode": device["locode"],
            "sections": set(),
            "seen_as": set(),
            "connection_count": 0,
        }
    entry = devices[key]
    entry["sections"].add(section)
    entry["seen_as"].add(side)
    entry["connection_count"] += 1


def _build_connection(row, a_device, z_device, section, status, breakout_seen) -> Optional[Dict]:
    """Build a single connection record from a cutsheet row."""
    if not a_device and not z_device:
        return None

    a_port = _normalize_cell(row.get(Canon.A_PORT))
    z_port = _normalize_cell(row.get(Canon.Z_PORT))
    a_optic = _normalize_cell(row.get(Canon.A_OPTIC))
    z_optic = _normalize_cell(row.get(Canon.Z_OPTIC))
    cable = _normalize_cell(row.get(Canon.CABLE_ID))

    # Breakout detection (these column names are not profile-managed
    # because breakout columns only appear in V1/Quincy format).
    # Try space-separated first (normalized headers), fall back to newline (raw Excel).
    def _brk(col_space, col_nl):
        return _normalize_cell(row.get(col_space)) or _normalize_cell(row.get(col_nl))
    a_breakout_loc = _brk("A-BREAKOUT LOC:CAB:RU", "A-BREAKOUT\nLOC:CAB:RU")
    a_breakout_slot = _brk("A-BREAKOUT SLOT:PORT", "A-BREAKOUT\nSLOT:PORT")
    z_breakout_loc = _brk("Z-BREAKOUT LOC:CAB:RU", "Z-BREAKOUT\nLOC:CAB:RU")
    z_breakout_slot = _brk("Z-BREAKOUT SLOT:PORT", "Z-BREAKOUT\nSLOT:PORT")

    is_a_breakout = bool(a_breakout_loc)
    is_z_breakout = bool(z_breakout_loc)

    # Track whether this breakout group's optic has already been counted
    a_breakout_new = True
    if is_a_breakout and a_device:
        bkey = _parse_breakout_key(a_device["loc_cab_ru"], a_port)
        if bkey in breakout_seen:
            a_breakout_new = False
        else:
            breakout_seen.add(bkey)

    conn = {
        "section": section,
        "status": status,
        "a_dns": a_device["dns_name"] if a_device else "",
        "a_loc": a_device["loc_cab_ru"] if a_device else "",
        "a_model": a_device["model"] if a_device else "",
        "a_port": a_port,
        "a_optic": a_optic,
        "z_dns": z_device["dns_name"] if z_device else "",
        "z_loc": z_device["loc_cab_ru"] if z_device else "",
        "z_model": z_device["model"] if z_device else "",
        "z_port": z_port,
        "z_optic": z_optic,
        "cable": cable,
        "a_breakout": is_a_breakout,
        "a_breakout_loc": a_breakout_loc,
        "a_breakout_slot": a_breakout_slot,
        "a_breakout_new_optic": a_breakout_new,
        "z_breakout": is_z_breakout,
        "z_breakout_loc": z_breakout_loc,
        "z_breakout_slot": z_breakout_slot,
    }
    return conn


# ---------------------------------------------------------------------------
# LLM-ready context builder
# ---------------------------------------------------------------------------

def load_prebuilt_sheets(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Detect and load pre-built DEVICE_INVENTORY, CONNECTIONS, and SUMMARY
    sheets from a V3-style cutsheet Excel file. Returns None if the file
    doesn't have the expected sheets.
    """
    try:
        xls = pd.ExcelFile(file_path)
    except (FileNotFoundError, ValueError):
        return None

    sheet_names_lower = {s.strip().casefold(): s for s in xls.sheet_names}

    # Must have at least DEVICE_INVENTORY or SUMMARY to qualify as pre-built
    has_inventory = "device_inventory" in sheet_names_lower
    has_summary = "summary" in sheet_names_lower
    has_connections = "connections" in sheet_names_lower

    if not (has_inventory or has_summary):
        return None

    result: Dict[str, Any] = {"source": "prebuilt_sheets"}

    # --- DEVICE_INVENTORY ---
    if has_inventory:
        inv_df = pd.read_excel(file_path, sheet_name=sheet_names_lower["device_inventory"])
        model_summary: Dict[str, Any] = {}
        for row in inv_df.to_dict('records'):
            model = _normalize_model_cell(row.get("MODEL_NORMALIZED") or row.get("MODEL"))
            if not model:
                continue
            dns = _normalize_dns(row.get("DEVICE_DNS_NAME", ""))
            loc = _normalize_cell(row.get("LOCATION", ""))
            site = _normalize_cell(row.get("SITE", ""))
            role = _normalize_cell(row.get("DEVICE_ROLE", ""))

            entry = model_summary.setdefault(model, {
                "count": 0,
                "locations": [],
                "dns_names": [],
                "sections": set(),
                "roles": set(),
                "site": site,
            })
            entry["count"] += 1
            if loc and len(entry["locations"]) < 15:
                entry["locations"].append(loc)
            if dns and len(entry["dns_names"]) < 5:
                entry["dns_names"].append(dns)
            if role:
                entry["roles"].add(role)

        # Convert sets to lists for JSON
        for info in model_summary.values():
            info["sections"] = sorted(info.get("sections", set()))
            info["roles"] = sorted(info.get("roles", set()))

        result["device_inventory"] = model_summary
        result["total_devices"] = len(inv_df)

    # --- CONNECTIONS ---
    if has_connections:
        conn_df = pd.read_excel(file_path, sheet_name=sheet_names_lower["connections"])
        # Optic summary
        optic_summary: Dict[str, Any] = {}
        section_conn_counts: Dict[str, int] = {}
        status_counts: Dict[str, int] = {}

        optic_mismatches: List[Dict[str, str]] = []
        status_by_section: Dict[str, Dict[str, int]] = {}

        for row in conn_df.to_dict('records'):
            section = _normalize_cell(row.get("SECTION", ""))
            status_raw = _normalize_cell(row.get("STATUS", ""))
            status = normalize_status(status_raw) if status_raw else ""
            if section:
                section_conn_counts[section] = section_conn_counts.get(section, 0) + 1
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1

            # Track status by section for per-section completion analysis
            if section and status:
                sec_status = status_by_section.setdefault(section, {})
                sec_status[status] = sec_status.get(status, 0) + 1

            a_optic = _normalize_cell(row.get("A_OPTIC", ""))
            z_optic = _normalize_cell(row.get("Z_OPTIC", ""))

            for col, optic in [("A_OPTIC", a_optic), ("Z_OPTIC", z_optic)]:
                if not optic:
                    continue
                side = "A" if col.startswith("A") else "Z"
                dns_col = f"{side}_DNS_NAME"
                dns = _normalize_cell(row.get(dns_col, ""))
                entry = optic_summary.setdefault(optic, {"count": 0, "locations": {}})
                entry["count"] += 1
                if dns:
                    entry["locations"][dns] = entry["locations"].get(dns, 0) + 1

            # Flag A/Z optic mismatches (both sides must have an optic)
            if a_optic and z_optic and a_optic != z_optic:
                # Only track unique pairings, cap at 50 examples
                if len(optic_mismatches) < 50:
                    optic_mismatches.append({
                        "a_device": _normalize_cell(row.get("A_DNS_NAME", "")),
                        "a_optic": a_optic,
                        "z_device": _normalize_cell(row.get("Z_DNS_NAME", "")),
                        "z_optic": z_optic,
                        "section": section,
                    })

        # Trim location lists per optic
        for optic, info in optic_summary.items():
            sorted_locs = sorted(info["locations"].items(), key=lambda x: x[1], reverse=True)
            info["locations"] = dict(sorted_locs[:10])
            info["total_locations"] = len(sorted_locs)

        # Summarize mismatches by pairing type
        mismatch_pairs: Dict[str, int] = {}
        for m in optic_mismatches:
            pair_key = f"{m['a_optic']} <-> {m['z_optic']}"
            mismatch_pairs[pair_key] = mismatch_pairs.get(pair_key, 0) + 1

        result["optic_summary"] = optic_summary
        result["section_connection_counts"] = section_conn_counts
        result["status_counts"] = status_counts
        result["status_by_section"] = status_by_section
        result["total_connections"] = len(conn_df)
        result["optic_mismatches"] = {
            "total_mismatched_connections": len(optic_mismatches),
            "pairing_summary": dict(sorted(mismatch_pairs.items(), key=lambda x: x[1], reverse=True)),
            "examples": optic_mismatches[:20],
        }

    # --- SUMMARY (pre-computed quick reference) ---
    if has_summary:
        sum_df = pd.read_excel(file_path, sheet_name=sheet_names_lower["summary"], header=None)
        quick_ref: Dict[str, str] = {}
        model_counts: Dict[str, Dict[str, int]] = {}
        parsing_section = None

        for row in sum_df.to_dict('records'):
            c0 = _normalize_cell(row.get(0, ""))
            c1 = _normalize_cell(row.get(1, ""))
            c2 = _normalize_cell(row.get(2, ""))

            if "UNIQUE DEVICE COUNTS" in c0.upper():
                parsing_section = "models"
                continue
            elif "CONNECTION STATUS" in c0.upper():
                parsing_section = "status"
                continue
            elif "QUICK REFERENCE" in c0.upper():
                parsing_section = "qref"
                continue
            elif c0.upper() in ("MODEL", "STATUS", "QUESTION", ""):
                continue

            if parsing_section == "models" and c0 and c0.upper() != "TOTAL":
                model_counts[c0] = {
                    "unique_devices": int(c1) if c1.isdigit() else 0,
                    "total_connections": int(c2) if c2.isdigit() else 0,
                }
            elif parsing_section == "qref" and c0:
                quick_ref[c0] = c1

        result["summary_model_counts"] = model_counts
        result["quick_reference"] = quick_ref

    return result


def build_llm_context_from_prebuilt(prebuilt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert pre-built sheet data into the same LLM context format
    that build_llm_context produces, so the rest of the pipeline
    doesn't need to change.

    Keys emitted here MUST match what build_llm_context() returns so
    downstream code (demo_auth_ai._trim_context_for_llm) behaves
    identically regardless of which path produced the context.
    """
    ctx: Dict[str, Any] = {}

    ctx["device_inventory"] = prebuilt.get("device_inventory", {})
    # H8: prebuilt sheets don't carry per-device side tracking; leave empty so
    # downstream code that expects this key doesn't get a KeyError.
    ctx["model_by_side"] = {}
    ctx["optic_summary"] = prebuilt.get("optic_summary", {})
    ctx["section_connection_counts"] = prebuilt.get("section_connection_counts", {})

    # section_topology: prebuilt sheets don't have ordered section lists,
    # but we can derive one from the connection counts keys
    ctx["section_topology"] = list(prebuilt.get("section_connection_counts", {}).keys())

    # Merge summary model counts as a cross-reference
    if "summary_model_counts" in prebuilt:
        ctx["verified_model_counts"] = prebuilt["summary_model_counts"]

    if "quick_reference" in prebuilt:
        ctx["quick_reference"] = prebuilt["quick_reference"]

    if "status_counts" in prebuilt:
        ctx["connection_status_counts"] = prebuilt["status_counts"]

    if "status_by_section" in prebuilt:
        ctx["status_by_section"] = prebuilt["status_by_section"]

    if "optic_mismatches" in prebuilt:
        ctx["optic_mismatches"] = prebuilt["optic_mismatches"]

    ctx["stats"] = {
        "total_devices": prebuilt.get("total_devices", 0),
        "total_connections": prebuilt.get("total_connections", 0),
        "source": "prebuilt_sheets",
    }

    return ctx


# ---------------------------------------------------------------------------
# In-memory connection cache for fast per-device lookups
# ---------------------------------------------------------------------------

_CONNECTION_CACHE: Dict[str, pd.DataFrame] = {}  # keyed by file_path
_CONNECTION_CACHE_COLS: Dict[str, Dict[str, str]] = {}  # column name mapping per file
_MAX_CACHE = 3


def _evict_connection_cache_if_full():
    while len(_CONNECTION_CACHE) >= _MAX_CACHE:
        oldest = next(iter(_CONNECTION_CACHE))
        del _CONNECTION_CACHE[oldest]
        _CONNECTION_CACHE_COLS.pop(oldest, None)


def preload_connections(file_path: str) -> bool:
    """
    Load the CONNECTIONS or CUTSHEET sheet into memory once.
    Call this at upload time so subsequent device lookups are instant.

    For raw CUTSHEET tabs, we run canonicalize() first so column names
    are consistent with Canon.* constants used everywhere else.

    Returns True if data was loaded successfully.
    """
    if file_path in _CONNECTION_CACHE:
        return True

    try:
        xls = pd.ExcelFile(file_path)
    except (FileNotFoundError, ValueError):
        return False

    sheet_lower = {s.strip().casefold(): s for s in xls.sheet_names}

    # Prefer CONNECTIONS sheet (V3 pre-built format, columns are underscored)
    if "connections" in sheet_lower:
        df = pd.read_excel(file_path, sheet_name=sheet_lower["connections"])
        if "A_DNS_NAME" in df.columns and "Z_DNS_NAME" in df.columns:
            # Pre-lowercase the DNS columns for fast matching
            df["_a_dns_lower"] = df["A_DNS_NAME"].astype(str).str.lower()
            df["_z_dns_lower"] = df["Z_DNS_NAME"].astype(str).str.lower()
            _evict_connection_cache_if_full()
            _CONNECTION_CACHE[file_path] = df
            _CONNECTION_CACHE_COLS[file_path] = {
                "a_dns": "A_DNS_NAME", "z_dns": "Z_DNS_NAME",
                "a_model": "A_MODEL", "z_model": "Z_MODEL",
                "a_port": "A_PORT", "z_port": "Z_PORT",
                "a_optic": "A_OPTIC", "z_optic": "Z_OPTIC",
                "cable": "CABLE_TYPE", "section": "SECTION",
                "status": "STATUS",
            }
            return True

    # Fallback: raw CUTSHEET tab - canonicalize first!
    if "cutsheet" in sheet_lower:
        df = pd.read_excel(file_path, sheet_name=sheet_lower["cutsheet"])
        df, _profile = canonicalize(df, sheet_type="cutsheet")

        # After canonicalize, columns are Canon.* names
        if Canon.A_DEVICE in df.columns and Canon.Z_DEVICE in df.columns:
            df["_a_dns_lower"] = df[Canon.A_DEVICE].astype(str).str.lower()
            df["_z_dns_lower"] = df[Canon.Z_DEVICE].astype(str).str.lower()
            _evict_connection_cache_if_full()
            _CONNECTION_CACHE[file_path] = df
            _CONNECTION_CACHE_COLS[file_path] = {
                "a_dns": Canon.A_DEVICE, "z_dns": Canon.Z_DEVICE,
                "a_model": Canon.A_MODEL, "z_model": Canon.Z_MODEL,
                "a_port": Canon.A_PORT, "z_port": Canon.Z_PORT,
                "a_optic": Canon.A_OPTIC, "z_optic": Canon.Z_OPTIC,
                "cable": Canon.CABLE_ID, "section": Canon.SECTION,
                "status": Canon.STATUS,
            }
            return True

    return False


def lookup_device_connections(file_path: str, question: str, max_rows: int = 200) -> Optional[List[Dict[str, str]]]:
    """
    Search the cached connection data for rows matching a device name
    mentioned in the question. Uses in-memory DataFrame for instant lookups.
    Falls back to loading from disk if cache is empty.
    """
    # Extract potential device hostnames from the question.
    tokens = [t.strip("'\"?.,") for t in question.replace(",", " ").replace(";", " ").split()]
    candidates = [t for t in tokens if t.count("-") >= 1 and len(t) > 5 and ":" not in t]
    if not candidates:
        candidates = [
            t for t in tokens
            if len(t) > 4 and any(c.isdigit() for c in t) and any(c.isalpha() for c in t) and ":" not in t
        ]
    if not candidates:
        log.debug("No device hostname candidates found in: %r", question[:80])
        return None

    target = candidates[0].lower()

    # Ensure data is loaded
    if file_path not in _CONNECTION_CACHE:
        if not preload_connections(file_path):
            return None

    df = _CONNECTION_CACHE[file_path]
    cols = _CONNECTION_CACHE_COLS[file_path]

    mask = df["_a_dns_lower"].str.contains(target, na=False) | df["_z_dns_lower"].str.contains(target, na=False)
    matches = df[mask].head(max_rows)

    if matches.empty:
        return None

    records = []
    for row in matches.to_dict("records"):
        records.append({
            "status": _normalize_cell(row.get(cols["status"], "")),
            "a_device": _normalize_cell(row.get(cols["a_dns"], "")),
            "a_model": _normalize_cell(row.get(cols["a_model"], "")),
            "a_port": _normalize_cell(row.get(cols["a_port"], "")),
            "a_optic": _normalize_cell(row.get(cols["a_optic"], "")),
            "z_device": _normalize_cell(row.get(cols["z_dns"], "")),
            "z_model": _normalize_cell(row.get(cols["z_model"], "")),
            "z_port": _normalize_cell(row.get(cols["z_port"], "")),
            "z_optic": _normalize_cell(row.get(cols["z_optic"], "")),
            "cable": _normalize_cell(row.get(cols["cable"], "")),
            "section": _normalize_cell(row.get(cols["section"], "")) if cols["section"] else "",
        })
    return records


def build_llm_context(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert normalized output into a compact, structured context dict
    for the LLM. Keeps token count low while preserving all queryable info.
    """
    # Device inventory summary grouped by model
    model_summary = {}
    for dev in normalized["devices"]:
        model = dev["model"] or "UNKNOWN"
        entry = model_summary.setdefault(model, {
            "count": 0,
            "locations": [],
            "dns_names": [],
            "sections": set(),
        })
        entry["count"] += 1
        if len(entry["locations"]) < 10:
            entry["locations"].append(dev["loc_cab_ru"])
        if dev["dns_name"] and len(entry["dns_names"]) < 5:
            entry["dns_names"].append(dev["dns_name"])
        entry["sections"].update(dev["sections"])

    # Convert sets to lists for JSON serialization
    for model in model_summary.values():
        model["sections"] = sorted(model["sections"])

    # device_roles: not available in the in-memory path (no host_inventory loaded).
    # The Postgres path uses the role_lookup query instead. If host data is ever
    # passed into this function, build a device_name -> role mapping here.
    device_roles: Dict[str, str] = {}

    # H8: Model-by-side grouping.
    # Tells the LLM which device models appear exclusively on the A-side, Z-side, or both.
    # Helps answer "what's on the Z-side?" without Postgres role data.
    # Note: role info (FDP, CDU, etc.) comes from host_inventory via the Postgres path;
    # this in-memory view only shows model names grouped by side.
    model_by_side: Dict[str, Dict[str, int]] = {}
    for dev in normalized["devices"]:
        model = dev["model"] or "UNKNOWN"
        sides = dev["seen_as"]  # set containing "A", "Z", or both
        entry = model_by_side.setdefault(model, {"a_only": 0, "z_only": 0, "both": 0})
        if "A" in sides and "Z" in sides:
            entry["both"] += 1
        elif "A" in sides:
            entry["a_only"] += 1
        elif "Z" in sides:
            entry["z_only"] += 1

    # Optic summary with locations
    optic_summary = {}
    for conn in normalized["connections"]:
        for side in ["a", "z"]:
            optic = conn[f"{side}_optic"]
            loc = conn[f"{side}_loc"]
            if not optic:
                continue
            # For breakout A-side, only count if it's a new optic
            if side == "a" and conn["a_breakout"] and not conn["a_breakout_new_optic"]:
                continue
            entry = optic_summary.setdefault(optic, {"count": 0, "locations": {}})
            entry["count"] += 1
            entry["locations"][loc] = entry["locations"].get(loc, 0) + 1

    # Trim location lists to top 10 per optic for token savings
    for optic, info in optic_summary.items():
        sorted_locs = sorted(info["locations"].items(), key=lambda x: x[1], reverse=True)
        info["locations"] = dict(sorted_locs[:10])
        info["total_locations"] = len(sorted_locs)

    # Section topology (ordered as they appear in the sheet)
    section_topology = normalized["sections"]

    # Connection count by section
    section_conn_counts = {}
    for conn in normalized["connections"]:
        sec = conn["section"]
        section_conn_counts[sec] = section_conn_counts.get(sec, 0) + 1

    # Connection status counts (matches prebuilt path output)
    status_counts: Dict[str, int] = {}
    for conn in normalized["connections"]:
        st = conn["status"]
        if st:
            status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "device_inventory": model_summary,
        "model_by_side": model_by_side,
        "optic_summary": optic_summary,
        "section_topology": section_topology,
        "section_connection_counts": section_conn_counts,
        "connection_status_counts": status_counts,
        "device_roles": device_roles,
        "stats": normalized["stats"],
    }


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def devices_to_dicts(normalized: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return device list with sets converted to sorted lists for JSON."""
    result = []
    for dev in normalized["devices"]:
        d = dict(dev)
        d["sections"] = sorted(d["sections"])
        d["seen_as"] = sorted(d["seen_as"])
        result.append(d)
    return result
