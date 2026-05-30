"""
Bench harness for cutsheet_normalizer.normalize_cutsheet.

Runs the pure-Python row loop and the Cython fast path (if built) against
the same workbook, reports wall-clock time and a parity check on the
returned dict shape.

Usage:
    python3 scripts/bench_normalizer.py <path/to/cutsheet.xlsx> [--repeat N]

Force the pure-Python path with ATLAS_NORMALIZER_FORCE_PY=1.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from pathlib import Path


def _load_dataframe(xlsx_path: Path):
    import pandas as pd
    # Reuse build_sheet_processor's sheet detection when available, else
    # fall back to the first sheet — bench is best-effort, not production.
    sheets = pd.read_excel(xlsx_path, sheet_name=None, dtype=object)
    for name, df in sheets.items():
        cols = [str(c).upper() for c in df.columns]
        if any("LOC:CAB:RU" in c or "LOC-CAB-RU" in c for c in cols):
            print(f"  using sheet: {name} ({len(df)} rows)")
            return df
    first = next(iter(sheets.values()))
    print(f"  fallback first sheet ({len(first)} rows)")
    return first


def _run_once(df, label: str) -> tuple[float, dict]:
    # Re-import inside each run so the FORCE_PY toggle takes effect.
    if "cutsheet_normalizer" in sys.modules:
        importlib.reload(sys.modules["cutsheet_normalizer"])
    normalizer = importlib.import_module("cutsheet_normalizer")
    t0 = time.perf_counter()
    result = normalizer.normalize_cutsheet(df)
    elapsed = time.perf_counter() - t0
    stats = result.get("stats", {})
    print(f"  [{label}] {elapsed:.3f}s  devices={stats.get('total_devices')} "
          f"connections={stats.get('total_connections')} rows={stats.get('data_rows')}")
    return elapsed, result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    if not args.xlsx.exists():
        print(f"file not found: {args.xlsx}", file=sys.stderr)
        return 2

    # Make sibling modules importable when run as `python scripts/bench_*.py`.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    print(f"loading {args.xlsx} ...")
    df = _load_dataframe(args.xlsx)

    py_times = []
    fast_times = []

    print(f"\npure Python (ATLAS_NORMALIZER_FORCE_PY=1) x{args.repeat}")
    os.environ["ATLAS_NORMALIZER_FORCE_PY"] = "1"
    py_result = None
    for _ in range(args.repeat):
        t, py_result = _run_once(df, "py")
        py_times.append(t)

    print(f"\nfast path (Cython if built) x{args.repeat}")
    os.environ.pop("ATLAS_NORMALIZER_FORCE_PY", None)
    fast_result = None
    for _ in range(args.repeat):
        t, fast_result = _run_once(df, "fast")
        fast_times.append(t)

    py_best = min(py_times)
    fast_best = min(fast_times)
    ratio = py_best / fast_best if fast_best > 0 else float("inf")
    print(f"\nbest-of-{args.repeat}: py={py_best:.3f}s fast={fast_best:.3f}s speedup={ratio:.2f}x")

    if py_result and fast_result:
        py_stats = py_result.get("stats", {})
        fast_stats = fast_result.get("stats", {})
        if py_stats == fast_stats:
            print("parity: stats match ✓")
        else:
            print("parity: STATS DIFFER")
            print(f"  py:   {py_stats}")
            print(f"  fast: {fast_stats}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
