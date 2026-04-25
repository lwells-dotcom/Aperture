"""
gsheet_fetcher.py - Pull cutsheet data from Google Sheets into the Atlas pipeline.

Standalone script that fetches one or more tabs from a Google Sheet, converts
them to DataFrames, and feeds them through cutsheet_normalizer (in-memory path)
or atlas_data_loader (Postgres path).

Auth: uses a GCP service account JSON key.  Set GOOGLE_SA_KEY_PATH in .env or
pass --key-path on the CLI.

Usage:
    # In-memory normalization only (prints summary, no DB write)
    python gsheet_fetcher.py --sheet-id <SHEET_ID> --site QCY

    # Full load into Postgres
    python gsheet_fetcher.py --sheet-id <SHEET_ID> --site QCY --load-postgres

    # Override tab name (default: auto-detect CUTSHEET/CONNECTIONS)
    python gsheet_fetcher.py --sheet-id <SHEET_ID> --site QCY --tab "CUTSHEET"

    # Fetch multiple sheets (Quincy + Ellendale)
    python gsheet_fetcher.py \
        --sheet-id <QCY_SHEET_ID> --site QCY \
        --sheet-id <ELD_SHEET_ID> --site ELD
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build as build_service
    HAS_GSHEET_DEPS = True
except ImportError:
    HAS_GSHEET_DEPS = False

# Atlas pipeline imports (expected to be in the same directory or on PYTHONPATH)
try:
    from cutsheet_normalizer import normalize_cutsheet, build_llm_context
    HAS_NORMALIZER = True
except ImportError:
    HAS_NORMALIZER = False

try:
    from atlas_data_loader import load_file as pg_load_file
    HAS_LOADER = True
except ImportError:
    HAS_LOADER = False

log = logging.getLogger(__name__)

# Google Sheets API scope (read-only)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Tabs to look for when auto-detecting, in priority order
CANDIDATE_TABS = ["cutsheet", "connections", "sheet1"]


# ---------------------------------------------------------------------------
# Google Sheets client
# ---------------------------------------------------------------------------

def _get_sheets_service(key_path: str):
    """Build an authorized Google Sheets API v4 service."""
    if not HAS_GSHEET_DEPS:
        raise RuntimeError(
            "google-auth and google-api-python-client are required. "
            "Install with: pip install google-auth google-api-python-client"
        )
    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return build_service("sheets", "v4", credentials=creds)


def list_tabs(service, sheet_id: str) -> List[str]:
    """Return all tab (sheet) names in the spreadsheet."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _detect_tab(tabs: List[str], explicit_tab: Optional[str] = None) -> str:
    """Pick the right tab: explicit override > auto-detect by name."""
    if explicit_tab:
        # Case-insensitive match against available tabs
        for t in tabs:
            if t.strip().casefold() == explicit_tab.strip().casefold():
                return t
        raise ValueError(
            f"Tab '{explicit_tab}' not found. Available: {tabs}"
        )
    # Auto-detect
    lower_map = {t.strip().casefold(): t for t in tabs}
    for candidate in CANDIDATE_TABS:
        if candidate in lower_map:
            return lower_map[candidate]
    # Fallback: check for optic-related columns in first tab
    log.warning("No CUTSHEET/CONNECTIONS tab found, falling back to first tab: %s", tabs[0])
    return tabs[0]


def fetch_tab_as_dataframe(
    service, sheet_id: str, tab_name: str
) -> pd.DataFrame:
    """Pull all rows from a single tab and return as a DataFrame.

    Uses the Sheets API values().get() which returns a 2D list.
    Row 0 is treated as headers.
    """
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=tab_name)
        .execute()
    )
    rows = result.get("values", [])
    if len(rows) < 2:
        raise ValueError(f"Tab '{tab_name}' has no data rows (got {len(rows)} rows total)")

    headers = [str(h).strip() for h in rows[0]]
    data = rows[1:]

    # Pad short rows to match header length (Sheets API omits trailing empty cells)
    ncols = len(headers)
    padded = [r + [""] * (ncols - len(r)) if len(r) < ncols else r[:ncols] for r in data]

    df = pd.DataFrame(padded, columns=headers)
    log.info("Fetched %d rows x %d cols from tab '%s'", len(df), len(df.columns), tab_name)
    return df


def fetch_all_tabs(
    service, sheet_id: str, tabs: Optional[List[str]] = None
) -> Dict[str, pd.DataFrame]:
    """Fetch multiple tabs as DataFrames. If tabs is None, fetch all."""
    available = list_tabs(service, sheet_id)
    targets = tabs if tabs else available
    result = {}
    for tab_name in targets:
        matched = None
        for a in available:
            if a.strip().casefold() == tab_name.strip().casefold():
                matched = a
                break
        if matched:
            try:
                result[matched] = fetch_tab_as_dataframe(service, sheet_id, matched)
            except Exception as exc:
                log.warning("Failed to fetch tab '%s': %s", matched, exc)
        else:
            log.warning("Tab '%s' not found in sheet, skipping", tab_name)
    return result


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def run_normalizer(df: pd.DataFrame, site_code: str) -> Dict[str, Any]:
    """Run the in-memory normalization pipeline on a DataFrame."""
    if not HAS_NORMALIZER:
        raise RuntimeError("cutsheet_normalizer not available. Run from the Optic_Count directory.")

    t0 = time.time()
    normalized = normalize_cutsheet(df)
    elapsed = time.time() - t0

    stats = normalized.get("stats", {})
    log.info(
        "[%s] Normalized in %.2fs: %d devices, %d connections, %d sections",
        site_code, elapsed,
        stats.get("device_count", 0),
        stats.get("connection_count", 0),
        len(normalized.get("sections", [])),
    )
    return normalized


def run_postgres_load(df: pd.DataFrame, site_code: str, uploaded_by: str = "gsheet_fetcher") -> Dict[str, Any]:
    """Save DataFrame to a temp Excel file and load into Postgres via atlas_data_loader."""
    if not HAS_LOADER:
        raise RuntimeError("atlas_data_loader not available. Run from the Optic_Count directory.")

    # atlas_data_loader.load_file() expects a file path, so we write a temp xlsx
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False, prefix=f"gsheet_{site_code}_") as tmp:
        tmp_path = tmp.name

    try:
        df.to_excel(tmp_path, index=False, sheet_name="CUTSHEET")
        log.info("[%s] Wrote temp file: %s (%d rows)", site_code, tmp_path, len(df))
        result = pg_load_file(tmp_path, site_code, uploaded_by=uploaded_by)
        return result
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class SheetSitePair(argparse.Action):
    """Custom action to collect --sheet-id / --site pairs."""

    def __call__(self, parser, namespace, values, option_string=None):
        pairs = getattr(namespace, "sheet_site_pairs", None) or []
        if option_string == "--sheet-id":
            pairs.append({"sheet_id": values, "site": None})
        elif option_string == "--site":
            if not pairs or pairs[-1]["site"] is not None:
                parser.error("--site must follow a --sheet-id")
            pairs[-1]["site"] = values
        namespace.sheet_site_pairs = pairs


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch cutsheet data from Google Sheets into the Atlas pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sheet-id", action=SheetSitePair, dest="sheet_site_pairs",
        help="Google Sheet ID (from the URL). Pair with --site.",
    )
    parser.add_argument(
        "--site", action=SheetSitePair, dest="sheet_site_pairs",
        help="Site code for the preceding --sheet-id (e.g., QCY, ELD).",
    )
    parser.add_argument(
        "--tab", default=None,
        help="Explicit tab name to fetch (default: auto-detect CUTSHEET/CONNECTIONS).",
    )
    parser.add_argument(
        "--key-path", default=os.getenv("GOOGLE_SA_KEY_PATH", ""),
        help="Path to GCP service account JSON key (or set GOOGLE_SA_KEY_PATH env var).",
    )
    parser.add_argument(
        "--load-postgres", action="store_true",
        help="Also load into Postgres via atlas_data_loader.",
    )
    parser.add_argument(
        "--list-tabs", action="store_true",
        help="Just list available tabs in the sheet(s) and exit.",
    )
    parser.add_argument(
        "--output-csv", default=None,
        help="Save fetched data to a local CSV (useful for debugging).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args(argv)

    # Validate pairs
    pairs = getattr(args, "sheet_site_pairs", None) or []
    for p in pairs:
        if p["site"] is None:
            parser.error("Every --sheet-id must be followed by --site")
    if not pairs:
        parser.error("At least one --sheet-id / --site pair is required")

    if not args.key_path:
        parser.error(
            "Service account key path required. Set GOOGLE_SA_KEY_PATH in .env "
            "or pass --key-path."
        )

    return args


def main(argv=None):
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    service = _get_sheets_service(args.key_path)

    for pair in args.sheet_site_pairs:
        sheet_id = pair["sheet_id"]
        site_code = pair["site"]
        log.info("Processing sheet %s for site %s", sheet_id, site_code)

        # List tabs
        tabs = list_tabs(service, sheet_id)
        log.info("Available tabs: %s", tabs)

        if args.list_tabs:
            print(f"\n[{site_code}] Sheet {sheet_id}")
            for t in tabs:
                print(f"  - {t}")
            continue

        # Detect and fetch the cutsheet tab
        tab_name = _detect_tab(tabs, args.tab)
        log.info("Using tab: %s", tab_name)
        df = fetch_tab_as_dataframe(service, sheet_id, tab_name)

        # Optional: save raw CSV for debugging
        if args.output_csv:
            csv_path = args.output_csv if len(args.sheet_site_pairs) == 1 else f"{site_code}_{args.output_csv}"
            df.to_csv(csv_path, index=False)
            log.info("Saved raw data to %s", csv_path)

        # Run normalizer (in-memory path)
        try:
            normalized = run_normalizer(df, site_code)
            stats = normalized.get("stats", {})
            print(f"\n[{site_code}] Normalization complete:")
            print(f"  Devices:     {stats.get('device_count', 0)}")
            print(f"  Connections: {stats.get('connection_count', 0)}")
            print(f"  Sections:    {len(normalized.get('sections', []))}")
            print(f"  Statuses:    {json.dumps(stats.get('status_counts', {}), indent=4)}")
        except Exception as exc:
            log.error("[%s] Normalization failed: %s", site_code, exc)
            if args.verbose:
                log.exception("Full traceback:")

        # Optionally load into Postgres
        if args.load_postgres:
            try:
                result = run_postgres_load(df, site_code)
                if result.get("ok"):
                    if result.get("skipped"):
                        print(f"  Postgres: skipped (duplicate, upload_id={result.get('existing_upload_id')})")
                    else:
                        print(f"  Postgres: loaded {result.get('connections_loaded', 0)} connections")
                        print(f"  Hosts:    {result.get('hosts_loaded', 0)}")
                        print(f"  Upload:   {result.get('upload_id')}")
                else:
                    print(f"  Postgres: FAILED - {result.get('error', 'unknown')}")
            except Exception as exc:
                log.error("[%s] Postgres load failed: %s", site_code, exc)

    log.info("Done.")


if __name__ == "__main__":
    main()
