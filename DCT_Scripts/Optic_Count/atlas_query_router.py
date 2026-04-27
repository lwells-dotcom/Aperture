"""
atlas_query_router.py - Question classification and SQL template routing.

Classifies natural language questions using domain routers (query_intent),
selects the appropriate parameterized SQL template, executes it against
Postgres, and formats results for compact LLM context.

No LLM-generated SQL. All queries are pre-built templates with parameterized inputs.

Refactored 2026-04-19: monolithic regex list replaced with domain routers.
Old _PATTERNS list and inline extractors moved to query_intent.py / query_extractors.py.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from atlas_data_loader import managed_connection
from query_intent import classify_question, classify_with_context, IntentResult, QuestionContext
import query_extractors as ext

from sql_templates import (
    _SQL_TEMPLATES,
    _MODEL_SEARCH_RAW_COUNT_SQL,
    _MODEL_SEARCH_STATUS_COUNT_SQL,
    _MODEL_SEARCH_UNIQUE_COUNT_SQL,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Question type definitions
# ---------------------------------------------------------------------------

QUESTION_TYPES = [
    "optic_count",
    "model_search",
    "device_list",
    "z_device_list",
    "a_device_list",
    "role_lookup",
    "device_detail",
    "device_connections",
    "connection_status",
    "cable_status",
    "section_summary",
    "section_completion",
    "lldp_failures",
    "lldp_neighbor_mismatch",
    "link_status",
    "rack_summary",
    "location_lookup",
    "site_overview",
    "data_hall_summary",
    "ip_lookup",
    "node_compute",
    "upload_diff",
    "upload_list",
    "cross_site_models",
    "cross_site_optics",
    "cross_site_status",
    "trend_status",
    "trend_section",
    "cable_type_summary",
    "general",
]


def _model_search_mode(question: str, *, has_status_filter: bool = False) -> str:
    normalized = " ".join(question.lower().split())
    is_count_question = bool(
        re.search(r"\bhow\s+many\b", normalized)
        or re.search(r"\bcount\b", normalized)
        or re.search(r"\btotal\s+(?:count|number)\b", normalized)
    )
    if not is_count_question:
        return "list"
    if has_status_filter:
        return "status_count"
    if re.search(r"\b(?:unique|distinct)\b", normalized):
        return "unique_count"
    return "raw_count"


# ---------------------------------------------------------------------------
# Classification and routing
# ---------------------------------------------------------------------------

def _escape_ilike(value: str) -> str:
    """Escape ILIKE metacharacters (%, _, \\) so user input can't widen query scope."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_location_pattern(location: str) -> Optional[str]:
    """Build a scoped ILIKE pattern for exact locs, racks, or hall prefixes.

    Returns None for bare rack numbers (ambiguous without a data hall).
    """
    if not location:
        return "%__NO_LOCATION__%"

    loc = location.strip().lower()

    if re.fullmatch(r"[a-z]{1,4}\d+:\d+:\d+", loc):
        return _escape_ilike(loc)

    m = re.fullmatch(r"([a-z]{1,4}\d+):(\d{1,4})", loc)
    if m:
        hall, rack = m.groups()
        return f"{_escape_ilike(hall)}%:{_escape_ilike(rack)}:%"

    if re.fullmatch(r"[a-z]{1,4}\d+", loc):
        return f"{_escape_ilike(loc)}%:%"

    if re.fullmatch(r"\d{1,4}", loc):
        return None

    return f"%{_escape_ilike(loc)}%"


def build_query_params(
    question: str, qtype: str, site_id: int, upload_id: Optional[int] = None,
    *,
    ctx: Optional[QuestionContext] = None,
) -> Dict[str, Any]:
    """Build the parameter dict for the SQL template.

    Accepts an optional pre-built QuestionContext so extractors don't re-run
    when called from route_question().  If ctx is None, extractors run fresh.
    """
    params: Dict[str, Any] = {"site_id": site_id, "upload_id": upload_id}

    # Use pre-extracted values from ctx when available, else call extractors directly
    _loc = ctx.extracted_location if ctx else ext.extract_location(question)
    _optic = ctx.extracted_optic if ctx else ext.extract_optic_type(question)
    _section_filter = ctx.extracted_section_filter if ctx else ext.extract_section_filter(question)
    _device = ctx.extracted_device if ctx else ext.extract_device_name(question)
    _section_name = ctx.extracted_section if ctx else ext.extract_section_name(question)
    _model = ctx.extracted_model if ctx else ext.extract_model(question)
    _model_status_filters, _model_status_label = (
        ext.extract_model_status_filter(question)
    )
    _role = ctx.extracted_role if ctx else ext.extract_role_and_side(question)[0]
    _side = ctx.extracted_side if ctx else ext.extract_role_and_side(question)[1]
    _ip = ctx.extracted_ip if ctx else ext.extract_ip(question)

    if qtype == "location_lookup":
        params["location_pattern"] = _build_location_pattern(_loc)
        params["location_input"] = _loc

    if qtype == "optic_count":
        params["optic_filter"] = f"%{_escape_ilike(_optic)}%" if _optic else ""
        params["section_filter"] = f"%{_escape_ilike(_section_filter)}%" if _section_filter else ""
        _loc_pat = _build_location_pattern(_loc) if _loc else ""
        params["location_filter"] = f"%:{_escape_ilike(_loc)}:%" if _loc_pat is None else _loc_pat

    if qtype == "rack_summary":
        _loc_pat = _build_location_pattern(_loc) if _loc else ""
        params["location_filter"] = f"%:{_escape_ilike(_loc)}:%" if _loc_pat is None else _loc_pat

    if qtype in ("device_detail", "device_connections"):
        params["device_pattern"] = f"%{_escape_ilike(_device)}%" if _device else "%"

    if qtype in ("section_summary", "section_completion"):
        params["section_name_filter"] = f"%{_escape_ilike(_section_name)}%" if _section_name else ""

    if qtype == "model_search":
        params["model_pattern"] = f"%{_escape_ilike(_model)}%" if _model else "%"
        params["model_status_filters"] = _model_status_filters or None
        params["model_status_label"] = _model_status_label
        params["model_search_mode"] = _model_search_mode(
            question,
            has_status_filter=bool(_model_status_filters),
        )
        _loc_pat = _build_location_pattern(_loc) if _loc else ""
        params["location_filter"] = f"%:{_escape_ilike(_loc)}:%" if _loc_pat is None else _loc_pat
        _hall = ctx.extracted_data_hall if ctx else ext.extract_data_hall(question)
        params["data_hall_filter"] = f"{_escape_ilike(_hall)}:%" if _hall else ""

    if qtype == "role_lookup":
        params["role_filter"] = f"%{_escape_ilike(_role)}%" if _role else ""
        params["side_filter"] = _side
        params["device_filter"] = f"%{_escape_ilike(_device)}%" if _device else ""

    if qtype == "data_hall_summary":
        _hall = ctx.extracted_data_hall if ctx else ext.extract_data_hall(question)
        params["hall_filter"] = f"{_escape_ilike(_hall)}%" if _hall else ""

    if qtype == "cable_type_summary":
        _cable = ctx.extracted_cable_type if ctx else ext.extract_cable_type(question)
        params["cable_type_filter"] = f"%{_escape_ilike(_cable)}%" if _cable else ""

    if qtype == "ip_lookup":
        if _ip:
            params["search_pattern"] = f"%{_escape_ilike(_ip)}%"
        else:
            log.warning("ip_lookup: no IP extracted from %r, will reroute to general", question[:80])

    if qtype == "upload_diff":
        upload_a, upload_b = ext.extract_upload_ids(question)
        params["upload_id_a"] = upload_a
        params["upload_id_b"] = upload_b

    # upload_list only needs site_id (already in params)

    # cross_site queries don't filter by site_id; the SQL joins across all
    # active uploads.  Params dict still carries site_id for signature compat.

    if qtype == "trend_section":
        params["section_name_filter"] = f"%{_escape_ilike(_section_name)}%" if _section_name else ""

    if qtype in ("cross_site_models", "cross_site_optics", "cross_site_status"):
        codes = ext.extract_site_codes(question)
        params["site_codes"] = codes if codes else None

    return params


def execute_query(qtype: str, params: Dict[str, Any]) -> Tuple[List[Dict], float]:
    """
    Execute the SQL template for the given question type.
    Returns (rows_as_dicts, elapsed_seconds).
    """
    if qtype not in _SQL_TEMPLATES:
        log.warning("Unknown qtype %r, falling back to general", qtype)
    sql = _SQL_TEMPLATES.get(qtype, _SQL_TEMPLATES["general"])
    if qtype == "model_search":
        mode = params.get("model_search_mode", "list")
        if mode == "raw_count":
            sql = _MODEL_SEARCH_RAW_COUNT_SQL
        elif mode == "status_count":
            sql = _MODEL_SEARCH_STATUS_COUNT_SQL
        elif mode == "unique_count":
            sql = _MODEL_SEARCH_UNIQUE_COUNT_SQL
    t0 = time.monotonic()
    with managed_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    elapsed = round(time.monotonic() - t0, 4)
    return rows, elapsed



# ---------------------------------------------------------------------------
# Per-type result formatters — registered in _FORMATTERS below
# ---------------------------------------------------------------------------

def _fmt_model_search(rows: List[Dict], question: str, lines: List[str]) -> None:
    _LIST_CAP = 20
    _status_filters, status_label = ext.extract_model_status_filter(question)
    if status_label:
        lines.append(f"  Status filter: {status_label}")
    if rows and "matching_device_locations" in rows[0]:
        row = rows[0]
        lines.append(
            f"  Unique device locations matching pattern: {row.get('matching_device_locations', 0)}"
        )
        lines.append(
            f"  Unique hostnames matching filter: {row.get('matching_device_names', 0)}"
        )
        lines.append(
            f"  Matching cutsheet rows: {row.get('matching_cutsheet_rows', 0)}"
            f"  |  A-side rows: {row.get('a_side_rows', 0)}"
            f"  |  Z-side rows: {row.get('z_side_rows', 0)}"
        )
        return
    if rows and "cutsheet_occurrences" in rows[0]:
        row = rows[0]
        lines.append(f"  Total cutsheet appearances matching pattern: {row.get('cutsheet_occurrences', 0)}")
        lines.append(
            f"  A-side appearances: {row.get('a_side_occurrences', 0)}"
            f"  |  Z-side appearances: {row.get('z_side_occurrences', 0)}"
        )
        lines.append(f"  Unique devices represented in cutsheet: {row.get('cutsheet_unique_devices', 0)}")
        return
    if rows and "total_unique_devices" in rows[0]:
        row = rows[0]
        lines.append(f"  Total unique devices matching pattern: {row.get('total_unique_devices', 0)}")
        lines.append(f"  In cutsheet connections: {row.get('cutsheet_unique_devices', 0)} device(s)")
        lines.append(f"  In host inventory only: {row.get('inventory_unique_devices', 0)} device(s)")
        return
    distinct_devices = [r for r in rows if r["connections"] > 0]
    inventory_only = [r for r in rows if r["connections"] == 0]
    total_unique = rows[0].get("total_unique") if rows else 0
    truncated = len(rows) == 200 and total_unique and total_unique > 200
    if total_unique:
        lines.append(
            f"  Total distinct devices matching pattern: {total_unique}"
            + (" (showing top 200 by matching row count)" if truncated else "")
        )
    else:
        lines.append(f"  Total distinct devices matching pattern: {len(rows)}")
    if distinct_devices:
        lines.append(
            f"  In cutsheet connections: {len(distinct_devices)} device(s)"
            + (f" (showing first {_LIST_CAP})" if len(distinct_devices) > _LIST_CAP else "")
        )
        for r in distinct_devices[:_LIST_CAP]:
            model_tag = f" [model={r['model']}]" if r.get("model") else ""
            lines.append(f"    {r['device_name']}{model_tag} ({r['connections']} connections)")
    if inventory_only:
        lines.append(
            f"  In host inventory only: {len(inventory_only)} device(s)"
            + (f" (showing first {_LIST_CAP})" if len(inventory_only) > _LIST_CAP else "")
        )
        for r in inventory_only[:_LIST_CAP]:
            model_tag = f" [model={r['model']}]" if r.get("model") else ""
            lines.append(f"    {r['device_name']}{model_tag}")
    if not rows:
        lines.append("  No devices found matching this model pattern.")


def _fmt_link_status(rows: List[Dict], question: str, lines: List[str]) -> None:
    up = [r for r in rows if str(r.get("link_status", "")).lower() == "up"]
    down = [r for r in rows if str(r.get("link_status", "")).lower() == "down"]
    other = [r for r in rows if str(r.get("link_status", "")).lower() not in ("up", "down")]
    lines.append(f"  Links up: {len(up)}  |  Links down: {len(down)}  |  Other: {len(other)}")
    for r in down:
        lines.append(
            f"  [DOWN] {r['a_device']}:{r['a_port']} -> {r['z_device']}:{r['z_port']}"
            + (f"  neighbor={r['current_neighbor']}" if r.get("current_neighbor") else "")
            + (f"  note={r['dct_notes']}" if r.get("dct_notes") else "")
        )
    for r in other:
        lines.append(
            f"  [{r.get('link_status', '?').upper()}] {r['a_device']}:{r['a_port']} -> "
            f"{r['z_device']}:{r['z_port']}"
        )


def _fmt_lldp_neighbor_mismatch(rows: List[Dict], question: str, lines: List[str]) -> None:
    lines.append(f"  Total mismatches found: {len(rows)}")
    for r in rows:
        link = str(r.get("link_status", "")).upper() or "?"
        lines.append(
            f"  [{link}] {r['a_device']}:{r['a_port']}"
            f"  expected={r['z_device']} actual={r['current_neighbor']}"
            + (f"  neighbor_port={r['current_neighbor_port']}" if r.get("current_neighbor_port") else "")
            + (f"  note={r['dct_notes']}" if r.get("dct_notes") else "")
        )


def _fmt_role_lookup(rows: List[Dict], question: str, lines: List[str]) -> None:
    role_filter, side_filter = ext.extract_role_and_side(question)
    if role_filter:
        lines.append(f"  Filter: role contains '{role_filter}'")
    if side_filter:
        lines.append(f"  Filter: {side_filter}-side only")
    by_role_side: Dict[Tuple[str, str], List[Dict]] = {}
    for r in rows:
        key = (r.get("role") or "unknown", r.get("side") or "?")
        by_role_side.setdefault(key, []).append(r)
    _DEVICE_CAP = 20
    if not role_filter:
        lines.append("  Role inventory (from host_inventory):")
        for (role, side), devices in sorted(by_role_side.items()):
            lines.append(f"    {role} ({side}-side): {len(devices)} unique device(s)")
        lines.append(
            "  Note: role data only covers devices present in the SITE-HOSTS tab. "
            "Devices not in host_inventory have no role assigned."
        )
    else:
        total_unique = len(rows)
        truncated = total_unique == 200
        lines.append(f"  Unique devices: {total_unique}" + (" (showing top 200)" if truncated else ""))
        for (role, side), devices in sorted(by_role_side.items()):
            lines.append(f"  {role} ({side}-side): {len(devices)} device(s)")
            for r in devices[:_DEVICE_CAP]:
                model_tag = f" [{r['model']}]" if r.get("model") else ""
                lines.append(f"    {r['device_name']}{model_tag} ({r['connection_count']} connections)")
            if len(devices) > _DEVICE_CAP:
                lines.append(f"    ... and {len(devices) - _DEVICE_CAP} more")


def _fmt_optic_count(rows: List[Dict], question: str, lines: List[str]) -> None:
    optic_filter = ext.extract_optic_type(question)
    section_filter = ext.extract_section_filter(question)
    if optic_filter:
        lines.append(f"  Filter: optic type contains '{optic_filter}'")
    if section_filter:
        lines.append(f"  Filter: section contains '{section_filter}'")
    for r in rows:
        cable_count = r.get("cable_count") or 0
        a_count = r.get("a_count") or 0
        z_count = r.get("z_count") or 0
        in_service = r.get("in_service") or 0
        failed = r.get("failed") or 0
        pending = r.get("pending") or 0
        incomplete = failed + pending
        both = a_count + z_count - cable_count
        side_str = f"A:{a_count} Z:{z_count}"
        if both > 0:
            side_str += f" both-sides:{both}"
        lines.append(
            f"  {r['optic_type']}: {cable_count} cables ({side_str}), "
            f"{in_service} in-service, {failed} failed, {pending} pending"
            + (f"  [{incomplete} incomplete]" if incomplete else "")
        )


def _fmt_device_list(rows: List[Dict], question: str, lines: List[str]) -> None:
    qtype = lines[0].replace("Query type: ", "")
    side_label = {"z_device_list": "Z-side", "a_device_list": "A-side"}.get(qtype)
    if side_label:
        total_unique = rows[0].get("total_unique") if rows else len(rows)
        truncated = len(rows) == 200 and total_unique and total_unique > 200
        lines.append(f"  Side: {side_label} only")
        if truncated:
            lines.append(f"  Unique {side_label} devices: {total_unique} total (showing top 200 by connection count)")
        else:
            lines.append(f"  Unique {side_label} devices: {total_unique or len(rows)}")
    for r in rows:
        lines.append(f"  {r['device_name']}: {r['connections']} connections, {r['ports']} ports")


def _fmt_device_connections(rows: List[Dict], question: str, lines: List[str]) -> None:
    for r in rows:
        lines.append(
            f"  [{r['status']}] {r['a_device']}:{r['a_port']} ({r['a_optic']}) "
            f"-> {r['z_device']}:{r['z_port']} ({r['z_optic']}) cable={r['cable_id']}"
        )


def _fmt_status(rows: List[Dict], question: str, lines: List[str]) -> None:
    qtype = lines[0].replace("Query type: ", "")
    total = sum(r.get("cnt", 0) for r in rows)
    label = "LLDP/verification statuses" if qtype == "connection_status" else "Cable run statuses"
    lines.append(f"  {label} (total: {total} connections):")
    for r in rows:
        cnt = r.get("cnt", 0)
        norm = r.get("status_normalized", "")
        raw = r.get("status") or norm
        pct = round(100.0 * cnt / total, 1) if total else 0
        lines.append(f"  {raw} [{norm}]: {cnt} ({pct}%)")


def _fmt_section_summary(rows: List[Dict], question: str, lines: List[str]) -> None:
    total = sum(r["connections"] for r in rows)
    if len(rows) <= 5:
        lines.append(f"  Combined total: {total} connections across {len(rows)} section(s)")
    for r in rows:
        lines.append(
            f"  {r['section']}: {r['connections']} connections, "
            f"{r['a_devices']} A-devices, {r['z_devices']} Z-devices"
        )
    if len(rows) > 5:
        lines.append(f"  --- Total: {total} connections across {len(rows)} sections ---")


def _fmt_section_completion(rows: List[Dict], question: str, lines: List[str]) -> None:
    total_all = sum(r["total"] for r in rows)
    complete_all = sum(r["complete"] for r in rows)
    incomplete_all = sum(r["incomplete"] for r in rows)
    pct_all = round(100.0 * complete_all / total_all, 1) if total_all else 0
    lines.append(f"  Site totals: {total_all} connections, {complete_all} complete, "
                 f"{incomplete_all} incomplete ({pct_all}% complete)")
    lines.append(f"  Sections: {len(rows)}")
    for r in rows:
        lines.append(
            f"  {r['section']}: {r['total']} total, {r['complete']} complete, "
            f"{r['incomplete']} incomplete ({r['pct_complete']}%)"
        )


def _fmt_lldp_failures(rows: List[Dict], question: str, lines: List[str]) -> None:
    for r in rows:
        lines.append(
            f"  [{r['status']}] {r['a_device']}:{r['a_port']} -> "
            f"{r['z_device']}:{r['z_port']} cable={r['cable_id']} section={r['section']}"
        )


def _fmt_rack_summary(rows: List[Dict], question: str, lines: List[str]) -> None:
    total_racks = rows[0].get("total_racks", len(rows)) if rows else 0
    site_unique_connections = rows[0].get("site_unique_connections") if rows else None
    truncated = len(rows) == 50 and total_racks and total_racks > 50
    lines.append(
        f"  Total racks: {total_racks}"
        + (" (showing top 50 by rack connection count)" if truncated else "")
    )
    if site_unique_connections is not None:
        lines.append(f"  Site unique connections: {site_unique_connections}")
    for i, r in enumerate(rows):
        rank = f"#{i + 1}" if i < 10 else "  "
        models = r.get("models") or "?"
        optics = r.get("optics") or ""
        optic_count = r.get("optic_count", 0)
        optic_tag = f" | {optic_count} optic(s): {optics}" if optics else " | 0 optics"
        lines.append(
            f"  {rank} {r['loc_cab_ru']}: {r['connections']} connections, "
            f"{r['devices']} device(s) [{models}]{optic_tag}"
        )


def _fmt_site_overview(rows: List[Dict], question: str, lines: List[str]) -> None:
    if rows:
        r = rows[0]
        lines.append(f"  Total connections: {r['total_connections']}")
        lines.append(f"  Total devices: {r['total_devices']}")
        lines.append(f"  Total sections: {r['total_sections']}")


def _fmt_location_lookup(rows: List[Dict], question: str, lines: List[str]) -> None:
    cutsheet_rows = [r for r in rows if r.get("source") == "cutsheet"]
    inventory_rows = [r for r in rows if r.get("source") == "inventory"]
    if cutsheet_rows:
        lines.append(f"  Devices in location ({len(cutsheet_rows)} unique device+side entries):")
        for r in cutsheet_rows:
            model_tag = f" [{r['model']}]" if r.get("model") else ""
            side = r.get("side", "?")
            conn_count = r.get("connection_count", 0)
            loc = r.get("location", "?")
            lines.append(
                f"    {r['device_name']}{model_tag} ({side}-side, {conn_count} connections) @ {loc}"
            )
    if inventory_rows:
        lines.append(f"  Host inventory ({len(inventory_rows)} hosts):")
        for r in inventory_rows:
            model_tag = f" [{r['model']}]" if r.get("model") else ""
            lines.append(f"    {r['device_name']}{model_tag} rack={r['location']}")


def _fmt_cable_type_summary(rows: List[Dict], question: str, lines: List[str]) -> None:
    cable_filter = ext.extract_cable_type(question)
    if cable_filter:
        lines.append(f"  Filter: cable type contains '{cable_filter}'")
    total = sum(r.get("cable_count", 0) for r in rows)
    lines.append(f"  Total cables with type data: {total}")
    for r in rows:
        lines.append(
            f"  {r['cable_type']}: {r['cable_count']} cables "
            f"({r['a_devices']} A-devices, {r['z_devices']} Z-devices)"
        )


def _fmt_data_hall_summary(rows: List[Dict], question: str, lines: List[str]) -> None:
    for r in rows:
        lines.append(f"  {r['data_hall']}: {r['connections']} connections, {r['devices']} devices")


def _fmt_ip_lookup(rows: List[Dict], question: str, lines: List[str]) -> None:
    for r in rows:
        line = f"  {r['a_device']}:{r.get('a_port','')} -> {r['z_device']}:{r.get('z_port','')} [{r['status']}]"
        raw = r.get('raw_row')
        if isinstance(raw, dict):
            tokens = [t.lower() for t in question.split() if len(t) >= 4]
            matches = [(k, v) for k, v in raw.items()
                       if any(tok in str(v).lower() for tok in tokens)]
            matches.sort(key=lambda kv: len(str(kv[1])))
            if matches:
                line += " | " + ", ".join(f"{k}:{v}" for k, v in matches[:3])
        lines.append(line)


def _fmt_upload_diff(rows: List[Dict], question: str, lines: List[str]) -> None:
    total_changes = 0
    for r in rows:
        change_type = r.get("change_type", "unknown")
        count = r.get("count", 0)
        items = r.get("items", [])
        total_changes += count
        label = {"removed": "REMOVED", "added": "ADDED",
                 "status_changed": "STATUS CHANGED",
                 "optic_changed": "OPTIC CHANGED"}.get(change_type, change_type.upper())
        lines.append(f"  {label} ({count}):")
        for item in items[:10]:
            a_dev = item.get("a_device", "?")
            a_port = item.get("a_port", "?")
            z_dev = item.get("z_device", "?")
            z_port = item.get("z_port", "?")
            status = item.get("status", "")
            sec = item.get("section", "")
            line = f"    {a_dev}:{a_port} -> {z_dev}:{z_port}"
            if status:
                line += f" [{status}]"
            if sec:
                line += f" (sec: {sec})"
            if item.get("a_role"):
                line += f" a_role={item['a_role']}"
            if item.get("z_role"):
                line += f" z_role={item['z_role']}"
            lines.append(line)
        if count > 10:
            lines.append(f"    ... and {count - 10} more")
    lines.insert(2, f"  Total changes: {total_changes}")


def _fmt_upload_list(rows: List[Dict], question: str, lines: List[str]) -> None:
    for r in rows:
        uid = r.get("id", "?")
        fname = r.get("filename", "unknown")
        rc = r.get("row_count", 0)
        created = str(r.get("created_at", "?"))[:19]
        active = " [ACTIVE]" if r.get("is_active") else ""
        uploader = f" by {r['uploaded_by']}" if r.get("uploaded_by") else ""
        profile = f" ({r['profile']})" if r.get("profile") else ""
        lines.append(f"  #{uid}: {fname} | {rc} rows | {created}{uploader}{profile}{active}")


def _fmt_cross_site_models(rows: List[Dict], question: str, lines: List[str]) -> None:
    by_model: Dict[str, List[Dict]] = {}
    for r in rows:
        by_model.setdefault(r.get("model") or "unknown", []).append(r)
    total_sites = len(set(r.get("site_code") for r in rows if r.get("site_code")))
    for model in sorted(by_model):
        model_rows = by_model[model]
        total_conns = sum(r.get("connection_count", 0) for r in model_rows)
        lines.append(f"  {model}: {len(model_rows)}/{total_sites} sites, {total_conns} connections")
        for r in sorted(model_rows, key=lambda x: x.get("site_code", "")):
            lines.append(f"    @ {r.get('site_code', '?')}: {r.get('connection_count', 0)} connections")


def _fmt_cross_site_optics(rows: List[Dict], question: str, lines: List[str]) -> None:
    by_optic: Dict[str, List[Dict]] = {}
    for r in rows:
        by_optic.setdefault(r.get("optic_type") or "unknown", []).append(r)
    total_sites = len(set(r.get("site_code") for r in rows if r.get("site_code")))
    for optic in sorted(by_optic):
        optic_rows = by_optic[optic]
        total_cables = sum(r.get("cable_count", 0) for r in optic_rows)
        total_svc = sum(r.get("in_service", 0) for r in optic_rows)
        total_fail = sum(r.get("failed", 0) for r in optic_rows)
        total_pend = sum(r.get("pending", 0) for r in optic_rows)
        lines.append(
            f"  {optic}: {total_cables} cables across {len(optic_rows)}/{total_sites} sites "
            f"({total_svc} in-service, {total_fail} failed, {total_pend} pending)"
        )
        for r in sorted(optic_rows, key=lambda x: x.get("site_code", "")):
            lines.append(
                f"    @ {r.get('site_code', '?')}: {r.get('cable_count', 0)} cables "
                f"({r.get('in_service', 0)} service, {r.get('failed', 0)} failed)"
            )


def _fmt_cross_site_status(rows: List[Dict], question: str, lines: List[str]) -> None:
    by_site: Dict[str, List[Dict]] = {}
    for r in rows:
        by_site.setdefault(r.get("site_code") or "unknown", []).append(r)
    total_all = sum(r.get("connection_count", 0) for r in rows)
    lines.append(f"  Total across all sites: {total_all} connections")
    lines.append(f"  Sites: {len(by_site)}")
    for site in sorted(by_site):
        site_rows = by_site[site]
        site_total = sum(r.get("connection_count", 0) for r in site_rows)
        lines.append(f"  {site} ({site_total} total):")
        for r in sorted(site_rows, key=lambda x: x.get("connection_count", 0), reverse=True):
            status = r.get("status_normalized") or "unknown"
            cnt = r.get("connection_count", 0)
            pct = round(100.0 * cnt / site_total, 1) if site_total else 0
            lines.append(f"    {status}: {cnt} ({pct}%)")


def _fmt_trend_status(rows: List[Dict], question: str, lines: List[str]) -> None:
    by_upload = {}
    for r in rows:
        uid = r["upload_id"]
        if uid not in by_upload:
            by_upload[uid] = r
    upload_list = sorted(by_upload.items(), key=lambda x: x[1]["created_at"])
    lines.append(f"  Timeline: {len(upload_list)} upload(s)")
    prev_row = None
    for uid, r in upload_list:
        date_str = str(r.get("created_at", "?"))[:19]
        total = r.get("total_connections") or 0
        pct = r.get("completion_percentage") or 0
        lp = r.get("lldp_passed_count") or 0
        lf = r.get("lldp_failed_count") or 0
        comp = r.get("complete_count") or 0
        hv = r.get("human_verified_count") or 0
        nr = r.get("not_run_count") or 0
        nt = r.get("not_terminated_count") or 0
        lines.append(f"  [{date_str}] {r.get('filename', '?')}: total={total} | {pct}% complete")
        lines.append(
            f"    LLDP_passed:{lp} LLDP_failed:{lf} complete:{comp} "
            f"human_verified:{hv} not_run:{nr} not_terminated:{nt}"
        )
        if prev_row is not None:
            d_total = total - (prev_row.get("total_connections") or 0)
            d_comp = (r.get("completion_total") or 0) - (prev_row.get("completion_total") or 0)
            d_fail = lf - (prev_row.get("lldp_failed_count") or 0)
            parts = []
            if d_total: parts.append(f"{d_total:+d} connections")
            if d_comp: parts.append(f"{d_comp:+d} complete")
            if d_fail: parts.append(f"{d_fail:+d} LLDP_failed")
            if parts:
                lines.append(f"    Delta: {', '.join(parts)}")
        prev_row = r
    if len(upload_list) > 1:
        first = upload_list[0][1]
        last = upload_list[-1][1]
        fc = first.get("completion_total") or 0
        lc = last.get("completion_total") or 0
        ff = first.get("lldp_failed_count") or 0
        lf_val = last.get("lldp_failed_count") or 0
        lines.append("  TRAJECTORY:")
        lines.append(f"    Completion: {fc} -> {lc} ({lc - fc:+d})")
        lines.append(f"    LLDP failures: {ff} -> {lf_val} ({lf_val - ff:+d})")
        lines.append(f"    Trend: {'IMPROVING' if lc > fc else 'DEGRADING' if lc < fc else 'STABLE'}")


def _fmt_trend_section(rows: List[Dict], question: str, lines: List[str]) -> None:
    by_upload_section: Dict[Tuple[int, str], Dict] = {}
    uploads_meta: Dict[int, Dict] = {}
    sections_seen: set = set()
    for r in rows:
        uid = r["upload_id"]
        sec = r.get("section") or "unspecified"
        by_upload_section[(uid, sec)] = r
        sections_seen.add(sec)
        if uid not in uploads_meta:
            uploads_meta[uid] = {"filename": r.get("filename"), "created_at": r.get("created_at")}
    uploads_ordered = sorted(uploads_meta.keys(), key=lambda u: uploads_meta[u]["created_at"])
    lines.append(f"  {len(uploads_ordered)} upload(s), {len(sections_seen)} section(s)")
    for sec in sorted(sections_seen):
        lines.append(f"  SECTION: {sec}")
        prev = None
        for uid in uploads_ordered:
            r = by_upload_section.get((uid, sec))
            if not r:
                continue
            date_str = str(r.get("created_at", "?"))[:19]
            total = r.get("total_connections") or 0
            comp = r.get("completion_total") or 0
            pct = r.get("completion_percentage") or 0
            lines.append(f"    [{date_str}] total={total} complete={comp} ({pct}%)")
            if prev is not None:
                dt = total - (prev.get("total_connections") or 0)
                dc = comp - (prev.get("completion_total") or 0)
                if dt or dc:
                    lines.append(f"      Delta: {dt:+d} total, {dc:+d} complete")
            prev = r


def _fmt_general(rows: List[Dict], question: str, lines: List[str]) -> None:
    for r in rows:
        lines.append(f"  {r.get('metric', '?')}: {r.get('value', '?')}")


_FORMATTERS: Dict[str, Callable] = {
    "model_search": _fmt_model_search,
    "link_status": _fmt_link_status,
    "lldp_neighbor_mismatch": _fmt_lldp_neighbor_mismatch,
    "role_lookup": _fmt_role_lookup,
    "optic_count": _fmt_optic_count,
    "device_list": _fmt_device_list,
    "device_detail": _fmt_device_list,
    "node_compute": _fmt_device_list,
    "z_device_list": _fmt_device_list,
    "a_device_list": _fmt_device_list,
    "device_connections": _fmt_device_connections,
    "connection_status": _fmt_status,
    "cable_status": _fmt_status,
    "section_summary": _fmt_section_summary,
    "section_completion": _fmt_section_completion,
    "lldp_failures": _fmt_lldp_failures,
    "rack_summary": _fmt_rack_summary,
    "site_overview": _fmt_site_overview,
    "location_lookup": _fmt_location_lookup,
    "cable_type_summary": _fmt_cable_type_summary,
    "data_hall_summary": _fmt_data_hall_summary,
    "ip_lookup": _fmt_ip_lookup,
    "upload_diff": _fmt_upload_diff,
    "upload_list": _fmt_upload_list,
    "cross_site_models": _fmt_cross_site_models,
    "cross_site_optics": _fmt_cross_site_optics,
    "cross_site_status": _fmt_cross_site_status,
    "trend_status": _fmt_trend_status,
    "trend_section": _fmt_trend_section,
    "general": _fmt_general,
}


def format_results_for_llm(qtype: str, rows: List[Dict], question: str = "") -> str:
    """Format query results into a compact string for LLM context."""
    if not rows:
        if qtype == "lldp_failures":
            return (
                f"Query type: {qtype}\n"
                "No LLDP: Failed connections found at this site.\n"
                "This site may use non-LLDP verification workflows. "
                "Use connection_status to see the actual status categories present."
            )
        if qtype == "role_lookup":
            role_filter, side_filter = ext.extract_role_and_side(question)
            side_note = f" on the {side_filter}-side" if side_filter else ""
            role_note = f" with role '{role_filter}'" if role_filter else ""
            return (
                f"Query type: {qtype}\n"
                f"No devices found{role_note}{side_note}.\n"
                "Possible reasons: (1) No SITE-HOSTS tab was uploaded with this cutsheet "
                "so host_inventory is empty and role columns were never populated. "
                "(2) The SITE-HOSTS tab exists but has no 'role' column. "
                "(3) The requested role/side filter matches zero records."
            )
        if qtype == "upload_diff":
            return f"Query type: {qtype}\nNo differences found between the two uploads."
        if qtype == "upload_list":
            return f"Query type: {qtype}\nNo uploads found for this site."
        if qtype in ("trend_status", "trend_section"):
            return (
                f"Query type: {qtype}\n"
                "No uploads found for this site. "
                "Trend analysis requires at least one cutsheet upload."
            )
        return f"Query type: {qtype}\nNo results found."

    lines = [f"Query type: {qtype}", f"Results ({len(rows)} rows):"]
    formatter = _FORMATTERS.get(qtype, _fmt_general)
    formatter(rows, question, lines)
    if len(rows) == 200:
        lines.append(
            "[NOTE: Results truncated at 200 rows. The full dataset may contain more records.]"
        )
    return "\n".join(lines)


def route_question(
    question: str, site_id: int, upload_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Full routing pipeline:
      1. Classify question
      2. Build params (including upload_id for per-cutsheet scoping)
      3. Execute SQL
      4. Format for LLM

    Returns dict with context string, metadata.
    """
    intent, ctx = classify_with_context(question)
    qtype = intent.question_type
    log.info("Classified %r -> %s (confidence=%s, reason=%s)",
             question[:80], qtype, intent.confidence, intent.reason)

    # W6: location_lookup wins over model_search when both model and location are
    # present. Override so the query returns model counts scoped to that location.
    if qtype == "location_lookup" and ctx.extracted_model:
        log.info("Rerouting location_lookup -> model_search: model %r also present", ctx.extracted_model)
        qtype = "model_search"

    params = build_query_params(question, qtype, site_id, upload_id=upload_id, ctx=ctx)

    # upload_diff requires two explicit upload IDs. SQL uses = %(upload_id_a)s which
    # evaluates to NULL when IDs are absent — producing a misleading "no differences" result.
    if qtype == "upload_diff" and (not params.get("upload_id_a") or not params.get("upload_id_b")):
        found_ids = [str(uid) for uid in (params.get("upload_id_a"), params.get("upload_id_b")) if uid]
        if found_ids:
            missing_note = (
                f"Found upload ID {', '.join(found_ids)} but need two explicit upload IDs to compare. "
            )
        else:
            missing_note = "No upload IDs found in your question. "
        context_text = (
            "Query type: upload_diff\n"
            + missing_note
            + "Please specify two upload IDs to compare, e.g.: "
            "'compare upload 5 vs upload 6' or 'diff upload 3 and 4'.\n"
            "Use 'list uploads' or 'show upload history' to see available IDs."
        )
        return {
            "ok": True,
            "question_type": "upload_diff",
            "context": context_text,
            "row_count": 0,
            "query_elapsed_seconds": 0.0,
            "token_estimate": len(context_text.split()),
            "confidence": "low",
            "matched_domain": "diff",
            "reason": "upload_diff matched but no upload IDs found in question",
        }

    if qtype == "location_lookup" and not params.get("location_pattern"):
        raw_loc = params.get("location_input") or ""
        context_text = (
            "Query type: location_lookup\n"
            f"The location '{raw_loc}' is too broad by itself. "
            "Please include a data hall or full rack location, e.g. "
            "'dh202:041', 'dh2 041', or 'dh202:041:10'."
        )
        return {
            "ok": True,
            "question_type": "location_lookup",
            "context": context_text,
            "row_count": 0,
            "query_elapsed_seconds": 0.0,
            "token_estimate": len(context_text.split()),
            "confidence": "low",
            "matched_domain": "location",
            "reason": "location_lookup matched but extracted location was too broad",
        }

    if qtype == "ip_lookup" and "search_pattern" not in params:
        log.warning("ip_lookup: no valid search pattern for %r, routing to general", question[:80])
        qtype = "general"

    try:
        rows, elapsed = execute_query(qtype, params)
        context_text = format_results_for_llm(qtype, rows, question)

        return {
            "ok": True,
            "question_type": qtype,
            "context": context_text,
            "row_count": len(rows),
            "query_elapsed_seconds": elapsed,
            "token_estimate": len(context_text.split()),
            "confidence": intent.confidence,
            "matched_domain": intent.matched_domain,
            "reason": intent.reason,
        }
    except psycopg2.Error as exc:
        log.exception("Database error for type=%s", qtype)
        return {"ok": False, "question_type": qtype, "error": "Database query failed",
                "error_category": "database"}
    except Exception as exc:
        log.exception("Routing error for type=%s", qtype)
        return {"ok": False, "question_type": qtype, "error": "Query routing failed",
                "error_category": "routing"}
