"""
ib_analyzer.py

Query an IB (InfiniBand) build-sheet Excel file for all cables involving a
given device name.  The file contains multiple pull-schedule sheet types
with varying column layouts; this module normalises across all of them.

Supported sheet types
---------------------
- Leaf Pull Schedule  : Source / Source Port / Destination / Destination Port / Optic Type
- Core Group          : (Unnamed status) / Source / Source Port / Destination / Destination Port / Unnamed (optic)
- Node-to-Leaf        : Status / Source / Destination / Destination Port  (no explicit optic column)

For any sheet where an optic column cannot be found, "Twin Port OSFP" is
used as the default — all IB cables in this environment use that optic.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKIP_PATTERNS = ("overhead", "elev", "backup", "ufm")

_OPTIC_RE = re.compile(r"(?i)(osfp|sfp\+?|qsfp|mms|twin|transceiver)")

_STATUS_RE = re.compile(r"(?i)(cable|run|complete|progress|pending|addition|label)")

DEFAULT_OPTIC = "Twin Port OSFP"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Strip leading/trailing whitespace from all column names in-place."""
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _find_col(df: pd.DataFrame, *names: str) -> Optional[str]:
    """Return the first column whose name matches any of the given names (case-insensitive)."""
    col_lower = {c.lower(): c for c in df.columns}
    for name in names:
        if name.lower() in col_lower:
            return col_lower[name.lower()]
    return None


def _find_optic_col(df: pd.DataFrame) -> Optional[str]:
    """
    Find the optic type column by:
    1. Exact name: 'Optic Type' or 'Optic'
    2. Any Unnamed column whose first non-null values look like optic type strings
    Returns None if not found (caller should default to DEFAULT_OPTIC).
    """
    col = _find_col(df, "optic type", "optic")
    if col:
        return col
    for col in df.columns:
        if "Unnamed" in str(col):
            sample = df[col].dropna().astype(str).head(10).tolist()
            if any(_OPTIC_RE.search(v) for v in sample):
                return col
    return None


def _find_status_col(df: pd.DataFrame) -> Optional[str]:
    """
    Find the status column by name ('Status') or by inspecting the first
    Unnamed column whose values match typical cable status strings.
    """
    col = _find_col(df, "status")
    if col:
        return col
    for c in df.columns:
        sample = df[c].dropna().astype(str).head(10).tolist()
        if any(_STATUS_RE.search(v) for v in sample):
            return c
    return None


def _s(val) -> str:
    """Coerce a cell value to a clean string; return '' for NaN/None."""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == "nan" else s


def _port_slot(port: str) -> str:
    """Extract optic slot from a fiber-qualified port: '17/1' → '17', '1' → '1'."""
    return port.split("/")[0].strip() if "/" in port else port


def _port_sort_key(port: str):
    """Natural sort key for port strings like '17', '1', '21'."""
    result = []
    for part in re.split(r"[/.]", port):
        try:
            result.append((0, int(part)))
        except ValueError:
            result.append((1, part.lower()))
    return result or [(1, "")]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query_device(filepath: str, device_name: str) -> Dict[str, Any]:
    """
    Search all cable sheets in an IB build-sheet Excel file for every row
    where the given device appears as Source or Destination.

    Parameters
    ----------
    filepath    : path to the IB build-sheet .xlsx file
    device_name : exact device name to search for (e.g. 'S2.1.1', 'L30.1.1-DH1')
                  matching is case-insensitive

    Returns
    -------
    {
        "device":        str,
        "optic_summary": {"Twin Port OSFP": 32, ...},   # count by optic type
        "connections": [
            {
                "device_port":  "17",
                "remote":       "S2.1.1",
                "remote_port":  "1",
                "optic":        "Twin Port OSFP",
                "cable":        "MTP",
                "status":       "Cable Not Run",
                "sheet":        "DH1 Rack 101 Leaf Pull Schedule",
                "side":         "source",     # "source" or "destination"
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
    device_upper = device_name.strip().upper()
    connections: List[Dict[str, Any]] = []

    for sheet in xf.sheet_names:
        if any(p in sheet.lower() for p in _SKIP_PATTERNS):
            continue
        try:
            df = xf.parse(sheet)
        except Exception:
            continue
        if df.empty:
            continue

        df = _norm_cols(df)

        src_col    = _find_col(df, "source")
        dst_col    = _find_col(df, "destination")
        sp_col     = _find_col(df, "source port")
        dp_col     = _find_col(df, "destination port")
        cable_col  = _find_col(df, "cable type")
        optic_col  = _find_optic_col(df)
        status_col = _find_status_col(df)

        if not src_col or not dst_col:
            continue

        for _, row in df.iterrows():
            src = _s(row.get(src_col))
            dst = _s(row.get(dst_col))
            if not src and not dst:
                continue

            sp     = _s(row.get(sp_col))     if sp_col     else ""
            dp     = _s(row.get(dp_col))     if dp_col     else ""
            cable  = _s(row.get(cable_col))  if cable_col  else ""
            status = _s(row.get(status_col)) if status_col else ""
            optic  = _s(row.get(optic_col))  if optic_col  else ""
            if not optic:
                optic = DEFAULT_OPTIC

            if src.upper() == device_upper:
                connections.append({
                    "device_port": sp,
                    "remote":      dst,
                    "remote_port": dp,
                    "optic":       optic,
                    "cable":       cable,
                    "status":      status,
                    "sheet":       sheet,
                    "side":        "source",
                })
            elif dst.upper() == device_upper:
                connections.append({
                    "device_port": dp,
                    "remote":      src,
                    "remote_port": sp,
                    "optic":       optic,
                    "cable":       cable,
                    "status":      status,
                    "sheet":       sheet,
                    "side":        "destination",
                })

    # Collapse fiber-qualified ports (X/1, X/2) to optic slot (X) and deduplicate.
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for c in connections:
        slot        = _port_slot(c["device_port"])
        remote_slot = _port_slot(c["remote_port"])
        key = (slot, c["remote"].upper(), remote_slot, c["sheet"])
        if key not in seen:
            seen.add(key)
            deduped.append({**c, "device_port": slot, "remote_port": remote_slot})
    connections = deduped

    connections.sort(key=lambda c: (c["sheet"], _port_sort_key(c["device_port"])))

    optic_summary = dict(
        sorted(
            Counter(c["optic"] for c in connections if c["optic"]).items(),
            key=lambda x: x[1],
            reverse=True,
        )
    )

    return {
        "device":        device_name.strip(),
        "optic_summary": optic_summary,
        "connections":   connections,
        "total":         len(connections),
    }
