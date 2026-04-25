#!/usr/bin/env python3
"""
ai_enabled_optic_count.py

Count optics from a CoreWeave cutsheet using MPO-aware logic, with optional
AI (Glean) filtering based on a natural-language spec, e.g.:

  python ai_enabled_optic_count.py cutsheet_test.xlsx \
      --spec "Only count optics in DH2"

Behavior:
- A-side:
  * Only rows with A-OPTIC set are considered.
  * All rows sharing (A-SIDE-DNS-NAME, A-PORT, A-OPTIC) are ONE A-side optic
    (handles MPO fan-out).
- Z-side:
  * Only rows with Z-OPTIC set are considered.
  * Each row counts as one Z-side optic (no de-duplication).
- Combined:
  * total_per_model = A_count + Z_count

If --spec is provided, the DataFrame is first filtered by AI/Glean, then the
optic counting runs on the filtered rows only.

NOTE: You must configure the OAuth + Glean settings in the CONFIG section
before using this script.
"""

import argparse
import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import webbrowser

from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests

# ------------------ CONFIG: fill these in for your Glean tenant ------------------

# Backend / tenant domain for your Glean instance, e.g.:
#   https://coreweave-be.glean.com
GLEAN_TENANT_BASE_URL = "https://<your-tenant>-be.glean.com"

# OAuth client info for this CLI app.
# Create / configure this in Glean Admin using the OAuth Authorization Server docs.
OAUTH_CLIENT_ID = "<your_oauth_client_id>"

# Redirect URI you registered for this client.
# For a local CLI tool, a common pattern is a loopback URI like:
#   http://127.0.0.1:8765/callback
OAUTH_REDIRECT_URI = "http://127.0.0.1:8765/callback"

# OAuth authorize and token endpoints for the Glean Authorization Server.
# Get the exact URLs from:
#   Glean OAuth Authorization Server documentation for your tenant.
OAUTH_AUTHORIZE_URL = "<your_authorize_endpoint>"
OAUTH_TOKEN_URL = "<your_token_endpoint>"

# Scopes your tool needs. Adjust per Glean docs (e.g. "SEARCH CHAT AGENTS").
OAUTH_SCOPES = "AGENTS"

# ID of a Glean Agent that turns a natural-language spec into a structured filter.
GLEAN_FILTER_AGENT_ID = "<your_filter_agent_id>"

# Where to cache tokens per user.
TOKEN_DIR = Path.home() / ".optic_counter"
TOKEN_PATH = TOKEN_DIR / "token.json"

# -------------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _generate_pkce_pair() -> Tuple[str, str]:
    verifier = _b64url_encode(os.urandom(32))
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal handler to capture the OAuth authorization code."""

    # These will be populated externally
    auth_code: Optional[str] = None
    auth_state: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        state = qs.get("state", [None])[0]
        error = qs.get("error", [None])[0]

        if error:
            OAuthCallbackHandler.error = error
        else:
            OAuthCallbackHandler.auth_code = code
            OAuthCallbackHandler.auth_state = state

        # Simple response to close the browser tab
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h3>You may close this window.</h3></body></html>")

    def log_message(self, format, *args):  # noqa: A003
        # Silence HTTP server logging
        return

def _start_local_http_server(port: int = 8765):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, OAuthCallbackHandler)

    def run():
        httpd.handle_request()  # handle a single request then exit

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return httpd, thread

def _load_token() -> Optional[Dict[str, Any]]:
    if not TOKEN_PATH.exists():
        return None
    try:
        with TOKEN_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (FileNotFoundError, ValueError, OSError):
        return None

def _save_token(token: Dict[str, Any]) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    with TOKEN_PATH.open("w", encoding="utf-8") as f:
        json.dump(token, f)
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        # Best-effort on non-POSIX systems
        pass

def _is_token_valid(token: Dict[str, Any]) -> bool:
    exp = token.get("expires_at")
    if not exp:
        return False
    # Add small safety margin
    return time.time() < exp - 60

def _exchange_code_for_token(code: str, code_verifier: str) -> Dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "client_id": OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    }
    resp = requests.post(OAUTH_TOKEN_URL, data=data, timeout=30)
    resp.raise_for_status()
    tok = resp.json()

    # Compute absolute expiry time
    now = time.time()
    tok["expires_at"] = now + float(tok.get("expires_in", 3600))
    return tok

def _refresh_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OAUTH_CLIENT_ID,
    }
    resp = requests.post(OAUTH_TOKEN_URL, data=data, timeout=30)
    if not resp.ok:
        return None
    tok = resp.json()
    now = time.time()
    tok["expires_at"] = now + float(tok.get("expires_in", 3600))
    return tok

def get_access_token() -> str:
    """
    Return a valid access token, performing interactive OAuth login if needed.

    Flow:
      1. Try cached token (refresh if expired and refresh_token present).
      2. If still no valid token, start local HTTP server and open browser
         to Glean's authorize URL. User logs in via Okta SSO.
      3. Exchange code for tokens; cache and return access_token.
    """
    # 1) Cached token
    tok = _load_token()
    if tok and _is_token_valid(tok):
        return tok["access_token"]

    # 2) Try refresh
    if tok and tok.get("refresh_token"):
        new_tok = _refresh_token(tok["refresh_token"])
        if new_tok and _is_token_valid(new_tok):
            _save_token(new_tok)
            return new_tok["access_token"]

    # 3) Interactive login (Authorization Code + PKCE)
    code_verifier, code_challenge = _generate_pkce_pair()
    state = _b64url_encode(os.urandom(16))

    # Start local server to catch redirect
    parsed_redirect = urllib.parse.urlparse(OAUTH_REDIRECT_URI)
    port = parsed_redirect.port or 8765
    server, _thread = _start_local_http_server(port=port)

    params = {
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    authorize_url = f"{OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    print("Opening browser for Glean / Okta SSO login...")
    webbrowser.open(authorize_url)

    # Wait for callback
    max_wait = 300  # 5 minutes
    poll_interval = 1
    waited = 0
    while waited < max_wait:
        if OAuthCallbackHandler.error:
            raise RuntimeError(f"OAuth error: {OAuthCallbackHandler.error}")
        if OAuthCallbackHandler.auth_code:
            if OAuthCallbackHandler.auth_state != state:
                raise RuntimeError("OAuth state mismatch; aborting.")
            break
        time.sleep(poll_interval)
        waited += poll_interval

    server.server_close()

    if not OAuthCallbackHandler.auth_code:
        raise RuntimeError("Did not receive OAuth authorization code in time.")

    tok = _exchange_code_for_token(OAuthCallbackHandler.auth_code, code_verifier)
    _save_token(tok)
    return tok["access_token"]

def load_cutsheet(cutsheet_path: str, sheet_name: str = "CUTSHEET") -> pd.DataFrame:
    if cutsheet_path.lower().endswith(".csv"):
        return pd.read_csv(cutsheet_path)
    else:
        return pd.read_excel(cutsheet_path, sheet_name=sheet_name)

def apply_structured_filter(df: pd.DataFrame, flt: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply a simple structured filter to df.

    Expected format (you define this in your Glean Agent), e.g.:

      {
        "mode": "row",
        "conditions": [
          {"column": "A-LOC:CAB:RU", "op": "contains", "value": "dh2"},
          {"column": "Z-LOC:CAB:RU", "op": "contains", "value": "dh2", "logic": "or"}
        ]
      }

    Supported ops: "eq", "neq", "contains", "startswith", "endswith".
    All conditions are AND'ed by default, unless logic == "or".
    """
    if not flt or flt.get("mode") != "row":
        return df

    conds: List[Dict[str, Any]] = flt.get("conditions", [])
    if not conds:
        return df

    mask = None
    for c in conds:
        col = c["column"]
        op = c.get("op", "eq")
        val = c.get("value")
        logic = c.get("logic", "and").lower()

        if col not in df.columns:
            # Skip unknown columns
            continue

        series = df[col].astype(str)

        if op == "eq":
            m = series == str(val)
        elif op == "neq":
            m = series != str(val)
        elif op == "contains":
            m = series.str.contains(str(val), case=False, na=False)
        elif op == "startswith":
            m = series.str.startswith(str(val), na=False)
        elif op == "endswith":
            m = series.str.endswith(str(val), na=False)
        else:
            continue

        if mask is None:
            mask = m
        else:
            if logic == "or":
                mask = mask | m
            else:
                mask = mask & m

    if mask is None:
        return df
    return df[mask]

def call_glean_filter_agent(df: pd.DataFrame, spec: str) -> Dict[str, Any]:
    """
    Call a Glean Agent to turn a natural-language spec into a structured filter.

    You control the Agent's instructions so that given:
      - table schema (columns + a few sample rows)
      - user spec string (e.g. "Only count optics in DH2")

    it returns JSON like the example in apply_structured_filter().
    """
    if not spec:
        return {}

    access_token = get_access_token()

    payload = {
        "agent_id": GLEAN_FILTER_AGENT_ID,
        "inputs": {
            "spec": spec,
            "schema": {
                "columns": list(df.columns),
                "sample_rows": df.head(20).to_dict(orient="records"),
            },
        },
    }

    # Adjust the path below to the correct Client/Agent API endpoint for your tenant.
    url = f"{GLEAN_TENANT_BASE_URL}/api/agents/run"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    return data.get("outputs", {}).get("filter", {})

def count_optics(cutsheet_path: str,
                 sheet_name: str = "CUTSHEET",
                 spec: str = "") -> pd.DataFrame:
    df = load_cutsheet(cutsheet_path, sheet_name=sheet_name)

    # Optional AI/Glean filter stage
    if spec:
        flt = call_glean_filter_agent(df, spec)
        df = apply_structured_filter(df, flt)

    # ---- A-side (MPO-aware de-duplication) ----
    a = df[df["A-OPTIC"].notna()].copy()
    a_groups = (
        a.groupby(["A-SIDE-DNS-NAME", "A-PORT", "A-OPTIC"], dropna=False)
        .size()
        .reset_index(name="rows")
    )
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

    result = pd.DataFrame({
        "optic_model": combined.index,
        "count_total": combined.values,
        "count_A_side": a_counts.reindex(combined.index).fillna(0).astype(int).values,
        "count_Z_side": z_counts.reindex(combined.index).fillna(0).astype(int).values,
    })

    return result.reset_index(drop=True)

def main():
    parser = argparse.ArgumentParser(
        description="Count optics from a CoreWeave cutsheet "
                    "(MPO-aware, optional AI/Glean filter via --spec)."
    )
    parser.add_argument(
        "cutsheet",
        help="Path to cutsheet file (.xlsx or .csv)",
    )
    parser.add_argument(
        "--sheet",
        default="CUTSHEET",
        help="Worksheet name for Excel files (default: CUTSHEET)",
    )
    parser.add_argument(
        "-s", "--spec",
        help="Natural-language filter spec for AI/Glean "
             '(e.g. "Only count optics in DH2")',
    )
    parser.add_argument(
        "--csv-out",
        help="Optional path to write results as CSV",
    )

    args = parser.parse_args()
    path = Path(args.cutsheet)

    df_result = count_optics(str(path), sheet_name=args.sheet, spec=args.spec or "")

    print(df_result.to_string(index=False))

    if args.csv_out:
        out_path = Path(args.csv_out)
        df_result.to_csv(out_path, index=False)
        print(f"\nWrote CSV to {out_path}")

if __name__ == "__main__":
    main()
