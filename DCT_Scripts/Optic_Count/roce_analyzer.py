"""
roce_analyzer.py

Query a RoCE build-sheet Excel file for all cables involving a given
LOC:CAB:RU identifier (e.g. 'dh202:003:37').

RoCE sheets use an A-side / Z-side column layout similar to the cutsheet
format.  The device identifier is the A-LOC:CAB:RU or Z-LOC:CAB:RU value.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKIP_PATTERNS = ("overhead", "backup")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    """Normalise a column name: strip, upper, collapse internal newlines."""
    return str(name).strip().upper().replace("\n", " ").replace("\r", " ")


def _s(val) -> str:
    """Coerce a cell value to a clean string; return '' for NaN/None."""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == "nan" else s


def _build_col_map(df: pd.DataFrame) -> Dict[str, str]:
    """Return {normalised_name: raw_column_name} for the DataFrame."""
    return {_norm(c): c for c in df.columns}


def _gc(col_map: Dict[str, str], *targets: str) -> Optional[str]:
    """Get raw column name for the first matching normalised target, or None."""
    for t in targets:
        raw = col_map.get(t.upper())
        if raw is not None:
            return raw
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query_location(filepath: str, loc_filter: str) -> Dict[str, Any]:
    """
    Search all cable sheets in a RoCE build-sheet Excel file for every row
    where the given LOC:CAB:RU appears on either the A or Z side.

    Parameters
    ----------
    filepath   : path to the RoCE build-sheet .xlsx file
    loc_filter : LOC:CAB:RU to search for, e.g. 'dh202:003:37'
                 matching is case-insensitive

    Returns
    -------
    {
        "location":      str,
        "dns_name":      str,             # first DNS name seen for this loc
        "optic_summary": {"MMS4X00-NM-FLT": 8, ...},
        "connections": [
            {
                "side":             "a",        # "a" or "z"
                "device_port":      "gpu0",
                "device_connector": "C1",
                "device_interface": "ibs0p0",
                "device_optic":     "MMS4X00-NM-FLT",
                "remote_loc":       "dh202:010:38",
                "remote_dns":       "dh202-t0a-01-...",
                "remote_port":      "swp1",
                "remote_connector": "C1",
                "remote_optic":     "MMS4X00-NM-T",
                "status":           "No Label & Not Yet Run",
                "sheet":            "DH202 NODE TO TIER-0",
            },
            ...
        ],
        "total": int,
    }
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    xf = pd.ExcelFile(filepath, engine="calamine")
    loc_upper = loc_filter.strip().upper()
    connections: List[Dict[str, Any]] = []
    dns_name = ""

    for sheet in xf.sheet_names:
        if any(p in sheet.lower() for p in _SKIP_PATTERNS):
            continue
        try:
            df = xf.parse(sheet)
        except Exception:
            continue
        if df.empty:
            continue

        cm = _build_col_map(df)

        a_loc_col = _gc(cm, "A-LOC:CAB:RU")
        z_loc_col = _gc(cm, "Z-LOC:CAB:RU")
        if not a_loc_col and not z_loc_col:
            continue

        a_dns_col = _gc(cm, "A-SIDE-DNS-NAME")
        a_port_col = _gc(cm, "A-PORT")
        a_con_col  = _gc(cm, "A-CONNECTOR")
        a_ifc_col  = _gc(cm, "A-INTERFACE")
        a_opc_col  = _gc(cm, "A-OPTIC")

        z_dns_col  = _gc(cm, "Z-SIDE-DNS-NAME")
        z_port_col = _gc(cm, "Z-PORT")
        z_con_col  = _gc(cm, "Z-CONNECTOR")
        z_ifc_col  = _gc(cm, "Z-INTERFACE")
        z_opc_col  = _gc(cm, "Z-OPTIC")

        status_col = _gc(cm, "STATUS")

        for _, row in df.iterrows():
            a_loc = _s(row.get(a_loc_col)).upper() if a_loc_col else ""
            z_loc = _s(row.get(z_loc_col)).upper() if z_loc_col else ""

            if a_loc == loc_upper:
                d_dns = _s(row.get(a_dns_col)) if a_dns_col else ""
                if d_dns and not dns_name:
                    dns_name = d_dns
                connections.append({
                    "side":             "a",
                    "device_port":      _s(row.get(a_port_col)) if a_port_col else "",
                    "device_connector": _s(row.get(a_con_col))  if a_con_col  else "",
                    "device_interface": _s(row.get(a_ifc_col))  if a_ifc_col  else "",
                    "device_optic":     _s(row.get(a_opc_col))  if a_opc_col  else "",
                    "remote_loc":       _s(row.get(z_loc_col))  if z_loc_col  else "",
                    "remote_dns":       _s(row.get(z_dns_col))  if z_dns_col  else "",
                    "remote_port":      _s(row.get(z_port_col)) if z_port_col else "",
                    "remote_connector": _s(row.get(z_con_col))  if z_con_col  else "",
                    "remote_optic":     _s(row.get(z_opc_col))  if z_opc_col  else "",
                    "status":           _s(row.get(status_col)) if status_col else "",
                    "sheet":            sheet,
                })
            elif z_loc == loc_upper:
                d_dns = _s(row.get(z_dns_col)) if z_dns_col else ""
                if d_dns and not dns_name:
                    dns_name = d_dns
                connections.append({
                    "side":             "z",
                    "device_port":      _s(row.get(z_port_col)) if z_port_col else "",
                    "device_connector": _s(row.get(z_con_col))  if z_con_col  else "",
                    "device_interface": _s(row.get(z_ifc_col))  if z_ifc_col  else "",
                    "device_optic":     _s(row.get(z_opc_col))  if z_opc_col  else "",
                    "remote_loc":       _s(row.get(a_loc_col))  if a_loc_col  else "",
                    "remote_dns":       _s(row.get(a_dns_col))  if a_dns_col  else "",
                    "remote_port":      _s(row.get(a_port_col)) if a_port_col else "",
                    "remote_connector": _s(row.get(a_con_col))  if a_con_col  else "",
                    "remote_optic":     _s(row.get(a_opc_col))  if a_opc_col  else "",
                    "status":           _s(row.get(status_col)) if status_col else "",
                    "sheet":            sheet,
                })

    optic_summary = dict(
        sorted(
            Counter(c["device_optic"] for c in connections if c["device_optic"]).items(),
            key=lambda x: x[1],
            reverse=True,
        )
    )

    return {
        "location":      loc_filter.strip(),
        "dns_name":      dns_name,
        "optic_summary": optic_summary,
        "connections":   connections,
        "total":         len(connections),
    }
