#!/usr/bin/env python3
"""
CLI only

ai_enabled_optic_count.py

Count optics from a CoreWeave cutsheet using MPO-aware logic:

- A-side:
  * Only rows with A-OPTIC set are considered.
  * All rows sharing (A-SIDE-DNS-NAME, A-PORT, A-OPTIC) are treated
    as ONE A-side optic (handles MPO fan-out).
- Z-side:
  * Only rows with Z-OPTIC set are considered.
  * Each row counts as one Z-side optic (no de-duplication).
- Combined:
  * total_per_model = A_count + Z_count
"""

import argparse
from pathlib import Path

import pandas as pd

def count_optics(cutsheet_path: str, sheet_name: str = "CUTSHEET") -> pd.DataFrame:
    """Return a DataFrame with A, Z, and combined optic counts by model."""
    df = pd.read_excel(cutsheet_path, sheet_name=sheet_name)

    # ---- A-side (MPO-aware de-duplication) ----
    a = df[df["A-OPTIC"].notna()].copy()
    # One optic per unique (device, port, optic model)
    a_groups = (
        a.groupby(["A-SIDE-DNS-NAME", "A-PORT", "A-OPTIC"], dropna=False)
        .size()
        .reset_index(name="rows")
    )
    # Each group counts as 1 optic
    a_counts = (
        a_groups.groupby("A-OPTIC")["rows"]
        .size()
        .sort_index()
    )

    # ---- Z-side (per-port counts) ----
    z = df[df["Z-OPTIC"].notna()].copy()
    z_counts = (
        z["Z-OPTIC"]
        .value_counts()
        .sort_index()
    )

    # ---- Combined ----
    combined = (
        a_counts.add(z_counts, fill_value=0)
        .astype(int)
        .sort_values(ascending=False)
    )

    # Build a tidy table
    result = pd.DataFrame({
        "optic_model": combined.index,
        "count_total": combined.values,
        "count_A_side": a_counts.reindex(combined.index).fillna(0).astype(int).values,
        "count_Z_side": z_counts.reindex(combined.index).fillna(0).astype(int).values,
    })

    return result.reset_index(drop=True)

def main():
    parser = argparse.ArgumentParser(
        description="Count optics from a CoreWeave cutsheet (MPO-aware)."
    )
    parser.add_argument(
        "cutsheet",
        help="Path to cutsheet Excel file (e.g. cutsheet_test.xlsx)",
    )
    parser.add_argument(
        "--sheet",
        default="CUTSHEET",
        help="Worksheet name containing the cutsheet (default: CUTSHEET)",
    )
    parser.add_argument(
        "--csv-out",
        help="Optional path to write results as CSV",
    )

    args = parser.parse_args()
    path = Path(args.cutsheet)

    df_result = count_optics(str(path), sheet_name=args.sheet)

    # Print nicely to stdout
    print(df_result.to_string(index=False))

    # Optional CSV export
    if args.csv_out:
        out_path = Path(args.csv_out)
        df_result.to_csv(out_path, index=False)
        print(f"\nWrote CSV to {out_path}")

if __name__ == "__main__":
    main()
