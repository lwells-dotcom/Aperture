import logging
import os
import re
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

import Define_Optic_Count
import demo_auth_ai

log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB
UPLOAD_DIR = Path(os.getenv("DEMO_UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# In-memory user context for demo purposes.
# Rule R7: these dicts don't survive across gunicorn workers.
# Postgres is the shared state layer; in-memory is a fast cache.
USER_CONTEXT = {}
USER_SITE = {}  # username -> {"site_code": str, "site_id": int}
AUDIT_LOG = []
_state_lock = threading.Lock()

_CONTEXT_TTL_SECONDS = 2 * 60 * 60  # 2 hours
_AUDIT_LOG_MAX = 1000
_MAX_QUESTION_LEN = 2000

# Rate limiter: ip -> (attempt_count, window_start_timestamp)
_RATE_LIMIT_STORE: dict = {}
_RATE_LIMIT_MAX = 10        # max attempts per window
_RATE_LIMIT_WINDOW = 60     # seconds
_RATE_LIMIT_MAX_KEYS = 5000  # evict stale entries when store exceeds this size


# ---------------------------------------------------------------------------
# Security middleware
# ---------------------------------------------------------------------------

@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Allow inline scripts only for the single-page UI served at /
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'"
    )
    return response


def _get_client_ip() -> str:
    """Return the best-effort client IP for rate limiting."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_rate_limit(key: str) -> bool:
    """Return True if the request is within limits, False if it should be blocked."""
    with _state_lock:
        now = time.time()
        # Evict expired entries when the store grows large to prevent unbounded memory use
        if len(_RATE_LIMIT_STORE) > _RATE_LIMIT_MAX_KEYS:
            expired = [k for k, (_, ws) in _RATE_LIMIT_STORE.items()
                       if now - ws > _RATE_LIMIT_WINDOW]
            for k in expired:
                del _RATE_LIMIT_STORE[k]
        count, window_start = _RATE_LIMIT_STORE.get(key, (0, now))
        if now - window_start > _RATE_LIMIT_WINDOW:
            _RATE_LIMIT_STORE[key] = (1, now)
            return True
        if count >= _RATE_LIMIT_MAX:
            return False
        _RATE_LIMIT_STORE[key] = (count + 1, window_start)
        return True


# ---------------------------------------------------------------------------
# Postgres availability check (TTL-cached to avoid a new connection per request)
# ---------------------------------------------------------------------------

_pg_cache: dict = {"ok": False, "ts": 0.0}
_PG_CACHE_TTL = 10.0  # seconds


def _check_postgres() -> bool:
    """Return True if Postgres is reachable. Result cached for 10 seconds."""
    now = time.monotonic()
    if now - _pg_cache["ts"] < _PG_CACHE_TTL:
        return _pg_cache["ok"]
    try:
        from atlas_data_loader import check_postgres
        result = check_postgres()
    except Exception:
        result = False
    _pg_cache["ok"] = result
    _pg_cache["ts"] = now
    return result


# ---------------------------------------------------------------------------
# Site code detection (3-layer)
# ---------------------------------------------------------------------------

# Known site code patterns
_KNOWN_SITES = {
    "QCY": "QCY", "QUINCY": "QCY",
    "ELD": "ELD", "ELLENDALE": "ELD",
    "DTN": "DTN", "DALTON": "DTN",
    "AUS": "AUS", "AUSTIN": "AUS",
    "PHX": "PHX", "PHOENIX": "PHX",
    "DFW": "DFW", "DALLAS": "DFW",
    "ORD": "ORD", "CHICAGO": "ORD",
    "IAD": "IAD", "ASHBURN": "IAD",
    "PDX": "PDX", "PORTLAND": "PDX",
    "SEA": "SEA", "SEATTLE": "SEA",
}

# UN/LOCODE pattern like US-LZL01, US-QCY, etc.
_LOCODE_RE = re.compile(r"\b([A-Z]{2}-[A-Z0-9]{3,6})\b", re.I)


def _extract_site_code(save_path, prebuilt=None):
    """
    Extract site code using 3-layer detection:
      1. Prebuilt sheets quick_reference
      2. SITE-VARS sheet in the Excel file
      3. Regex patterns in filename
    """
    # Layer 1: prebuilt sheets
    if prebuilt:
        qr = prebuilt.get("quick_reference", {})
        site = qr.get("Site code?", "") or qr.get("site_code", "")
        if site and site.upper() != "UNKNOWN":
            return site.upper()

    # Layer 2: SITE-VARS sheet
    if str(save_path).lower().endswith(".xlsx"):
        try:
            import pandas as pd
            xls = pd.ExcelFile(str(save_path))
            for sn in xls.sheet_names:
                if sn.strip().casefold() in ("site-vars", "site_vars", "site vars", "sitevars"):
                    sv = pd.read_excel(str(save_path), sheet_name=sn, header=None)
                    for _, row in sv.iterrows():
                        key = str(row.iloc[0]).strip().lower() if len(row) > 0 else ""
                        val = str(row.iloc[1]).strip() if len(row) > 1 else ""
                        if key in ("site_code", "site code", "site", "locode") and val:
                            return val.upper()
        except Exception:
            pass

    # Layer 3: filename regex
    filename = Path(str(save_path)).stem.upper()

    # Known site names take priority over LOCODE regex to avoid false positives
    # (e.g., "US-WEST" in a Quincy filename should not override "QUINCY" → QCY)
    for token, code in _KNOWN_SITES.items():
        if token in filename:
            return code

    # Fall back to UN/LOCODE pattern (e.g., US-LZL01)
    m = _LOCODE_RE.search(filename)
    if m:
        return m.group(1).upper()

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_bearer_token(auth_header):
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def _evict_stale_contexts():
    with _state_lock:
        cutoff = time.time() - _CONTEXT_TTL_SECONDS
        stale = [u for u, ctx in USER_CONTEXT.items() if ctx.get("ts", 0) < cutoff]
        for u in stale:
            del USER_CONTEXT[u]


def _audit(event, user, details):
    with _state_lock:
        AUDIT_LOG.append(
            {
                "event": event,
                "user": user,
                "details": details,
                "timestamp": int(time.time()),
            }
        )
        if len(AUDIT_LOG) > _AUDIT_LOG_MAX:
            del AUDIT_LOG[:-_AUDIT_LOG_MAX]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    pg_ok = _check_postgres()
    return jsonify({"ok": True, "postgres": pg_ok})


@app.post("/api/demo-verify-pin")
def demo_verify_pin():
    client_ip = _get_client_ip()
    if not _check_rate_limit(f"pin:{client_ip}"):
        return jsonify({"ok": False, "error": "Too many attempts. Try again later."}), 429

    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "demo_user").strip()
    pin = (payload.get("pin") or "").strip()

    if not demo_auth_ai.verify_demo_pin(pin):
        _audit("verify_failed", username, {"reason": "invalid_pin"})
        return jsonify({"ok": False, "error": "Invalid PIN"}), 401

    token = demo_auth_ai.create_demo_token(username)
    _audit("verify_success", username, {"scope": ["sheet:qa"]})
    return jsonify({"ok": True, "token": token, "token_type": "demo_json_token"})


@app.post("/api/upload-count")
def upload_count():
    auth_token = _extract_bearer_token(request.headers.get("Authorization"))
    if not auth_token:
        return jsonify({"error": "Missing bearer token"}), 401

    try:
        claims = demo_auth_ai.parse_and_validate_demo_token(auth_token)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 401

    if "file" not in request.files:
        return jsonify({"error": "Missing file upload"}), 400

    file_obj = request.files["file"]
    if not (file_obj.filename.lower().endswith(".xlsx") or file_obj.filename.lower().endswith(".csv")):
        return jsonify({"error": "Only .xlsx and .csv files are supported"}), 400

    safe_name = secure_filename(file_obj.filename)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400
    unique_name = f"{int(time.time())}_{claims['sub']}_{safe_name}"
    save_path = UPLOAD_DIR / unique_name
    # Guard against path traversal
    if not save_path.resolve().is_relative_to(UPLOAD_DIR.resolve()):
        return jsonify({"error": "Invalid file path"}), 400
    file_obj.save(save_path)

    # --- In-memory context (always, for fallback) ---
    try:
        context = Define_Optic_Count.build_sheet_context([str(save_path)])
    except Exception as exc:
        return jsonify({"error": f"Failed to parse file: {exc}"}), 500
    finally:
        Define_Optic_Count.clear_excel_cache()
    context["ts"] = time.time()
    _evict_stale_contexts()
    with _state_lock:
        USER_CONTEXT[claims["sub"]] = context
    _audit("upload_count", claims["sub"], {"file": safe_name})

    # Preload connections into memory for fast per-device lookups during Q&A
    prebuilt_stats = None
    prebuilt = None
    try:
        import cutsheet_normalizer
        cutsheet_normalizer.preload_connections(str(save_path))
        prebuilt = cutsheet_normalizer.load_prebuilt_sheets(str(save_path))
        if prebuilt:
            prebuilt_stats = {
                "total_devices": prebuilt.get("total_devices", 0),
                "total_connections": prebuilt.get("total_connections", 0),
                "device_models": len(prebuilt.get("device_inventory", {})),
                "optic_types": len(prebuilt.get("optic_summary", {})),
                "status_counts": prebuilt.get("status_counts", {}),
                "data_halls": prebuilt.get("quick_reference", {}).get("Data halls covered?", ""),
                "site": prebuilt.get("quick_reference", {}).get("Site code?", ""),
            }
    except (FileNotFoundError, ValueError, OSError):
        pass  # Non-fatal

    # --- Detect site code ---
    site_code = _extract_site_code(save_path, prebuilt)

    # --- Postgres dual-write (non-fatal) ---
    pg_result = None
    if _check_postgres():
        try:
            import atlas_data_loader
            pg_result = atlas_data_loader.load_file(
                str(save_path), site_code, uploaded_by=claims["sub"]
            )
            if pg_result and pg_result.get("ok") and not pg_result.get("skipped"):
                with _state_lock:
                    USER_SITE[claims["sub"]] = {
                        "site_code": site_code,
                        "site_id": pg_result["site_id"],
                        "upload_id": pg_result.get("upload_id"),
                    }
                log.info(
                    "Postgres load OK: site=%s upload_id=%s rows=%s",
                    site_code, pg_result.get("upload_id"), pg_result.get("connections_loaded"),
                )
        except Exception as exc:
            log.warning("Postgres dual-write failed (non-fatal): %s", exc)

    return jsonify(
        {
            "ok": True,
            "user": claims["sub"],
            "file": file_obj.filename,
            "summary": context["summary"],
            "prebuilt_stats": prebuilt_stats,
            "site_code": site_code,
            "postgres": pg_result if pg_result else None,
        }
    )


@app.post("/api/sheet-qa")
def sheet_qa():
    auth_token = _extract_bearer_token(request.headers.get("Authorization"))
    if not auth_token:
        return jsonify({"error": "Missing bearer token"}), 401

    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()[:_MAX_QUESTION_LEN]
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        claims = demo_auth_ai.parse_and_validate_demo_token(auth_token)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 401

    username = claims["sub"]

    # --- Try Postgres-first path ---
    with _state_lock:
        site_info = USER_SITE.get(username)

    # Rule R7: If this worker doesn't have site_info (gunicorn multi-worker),
    # try recovering from Postgres
    if not site_info and _check_postgres():
        try:
            import psycopg2.extras
            from atlas_data_loader import managed_connection
            with managed_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT cu.id AS upload_id, cu.site_id, s.site_code "
                        "FROM cutsheet_uploads cu "
                        "JOIN sites s ON s.id = cu.site_id "
                        "WHERE cu.uploaded_by = %s AND cu.is_active = TRUE "
                        "ORDER BY cu.created_at DESC LIMIT 1",
                        (username,),
                    )
                    row = cur.fetchone()
            if row:
                site_info = {
                    "site_code": row["site_code"],
                    "site_id": row["site_id"],
                    "upload_id": row["upload_id"],
                }
                with _state_lock:
                    USER_SITE[username] = site_info
                log.info("Recovered site context from Postgres for user=%s", username)
        except Exception as exc:
            log.warning("Postgres site recovery failed: %s", exc)

    # Try Postgres context if we have site info
    pg_context = None
    if site_info and _check_postgres():
        try:
            from atlas_postgres_context import build_postgres_context
            pg_context = build_postgres_context(
                question, site_info["site_id"],
                upload_id=site_info.get("upload_id"),
            )
            if pg_context and "error" not in pg_context:
                log.info(
                    "Postgres context: type=%s tokens=%s elapsed=%ss",
                    pg_context.get("question_type"),
                    pg_context.get("token_estimate"),
                    pg_context.get("query_elapsed_seconds"),
                )
        except Exception as exc:
            log.warning("Postgres context build failed (falling back): %s", exc)
            pg_context = None

    # Build sheet context: inject Postgres context if available
    with _state_lock:
        sheet_context = USER_CONTEXT.get(username)

    # If this worker has no in-memory context either, we can still proceed
    # if Postgres gave us context
    if not sheet_context and not pg_context:
        return jsonify({"error": "No uploaded sheet context for this user"}), 400

    if not sheet_context:
        # Create a minimal shell so qa_with_token has something to work with
        sheet_context = {"summary": {}, "files": [], "ts": time.time()}

    # Inject Postgres context for the LLM layer to pick up
    if pg_context and "error" not in pg_context:
        sheet_context["_postgres_context"] = pg_context

    result = demo_auth_ai.qa_with_token(auth_token, question, sheet_context)

    # Add Postgres metadata to response
    if pg_context and "error" not in pg_context:
        result["context_source"] = "POSTGRES"
        result["context_tokens"] = pg_context.get("token_estimate", 0)
        result["question_type"] = pg_context.get("question_type", "")
        result["query_elapsed"] = pg_context.get("query_elapsed_seconds", 0)
    else:
        result["context_source"] = "IN-MEMORY"

    _audit("sheet_qa", username, {"question": question, "source": result.get("context_source")})
    return jsonify({"ok": True, "result": result})


@app.get("/api/audit-log")
def audit_log():
    auth_token = _extract_bearer_token(request.headers.get("Authorization"))
    if not auth_token:
        return jsonify({"error": "Missing bearer token"}), 401
    try:
        demo_auth_ai.parse_and_validate_demo_token(auth_token)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 401
    return jsonify({"events": AUDIT_LOG[-200:]})


@app.get("/")
def index():
    return """
<!doctype html>
<html>
<head>
  <meta charset='utf-8'/>
  <title>Atlas - DCT Infrastructure Intelligence</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 24px auto; background: #F9FAFC; color: #343338; }
    .box { border: 1px solid #CDCED6; padding: 12px; margin-bottom: 12px; border-radius: 6px; background: #fff; }
    input, button, textarea { margin: 6px 0; width: 100%; padding: 8px; }
    input, textarea { border: 1px solid #CDCED6; border-radius: 4px; }
    input:focus, textarea:focus { outline: none; border-color: #2741E7; box-shadow: 0 0 0 2px #DAE5FF; }
    button { width: auto; background: #2741E7; color: #fff; border: 1px solid #2741E7; border-radius: 4px; cursor: pointer; }
    button:hover { background: #4665FF; border-color: #4665FF; }
    pre {
      background: #F3F3F5;
      padding: 10px;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
      overflow-x: hidden;
      border: 1px solid #CDCED6;
      border-radius: 4px;
    }
    .controls-btn { margin-left: 8px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-right: 6px; }
    .badge-pg { background: #DAE5FF; color: #2741E7; }
    .badge-mem { background: #fff3cd; color: #856404; }
    .modal {
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      overflow: auto;
      background: rgba(0, 0, 0, 0.35);
    }
    .modal-content {
      background: #fff;
      margin: 8% auto;
      padding: 16px;
      border: 1px solid #CDCED6;
      border-radius: 8px;
      width: min(760px, 92%);
      max-height: 75vh;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .modal-title { margin: 0 0 10px 0; color: #343338; }
    .modal-actions { margin-top: 12px; text-align: right; }
  </style>
</head>
<body>
  <h2>Atlas - DCT Infrastructure Intelligence</h2>
  <div class='box'>
    <h3>1) Verify Identity (PIN)</h3>
    <input id='username' placeholder='username' value='Lamar'/>
    <input id='pin' placeholder='PIN' type='password'/>
    <button onclick='verify()'>Verify</button>
    <div id='verifyStatus'></div>
  </div>

  <div class='box'>
    <h3>2) Upload .xlsx and Count</h3>
    <input type='file' id='sheetFile' accept='.xlsx,.csv'/>
    <button onclick='uploadCount()'>Upload + Count</button>
    <pre id='countOut'></pre>
  </div>

  <div class='box'>
    <h3>3) Ask Atlas</h3>
    <textarea id='question' rows='3' placeholder='Ask Atlas about your infrastructure data'></textarea>
    <button onclick='askAi()'>Ask Atlas</button>
    <button id='controlsBtn' class='controls-btn' style='display:none;' onclick='openControlsModal()'>Top 3 Controls</button>
    <pre id='qaOut'></pre>
  </div>

  <div id='controlsModal' class='modal' onclick='closeControlsModalOnBackdrop(event)'>
    <div class='modal-content'>
      <h3 class='modal-title'>Top 3 Controls to Implement</h3>
      <pre id='controlsContent'></pre>
      <div class='modal-actions'>
        <button onclick='closeControlsModal()'>Close</button>
      </div>
    </div>
  </div>

<script>
  function appPath(path) {
    const match = window.location.pathname.match(/^(\/canvas-apps\/[^/]+)/);
    const base = match ? match[1] : '';
    if (!path) return base || '/';
    if (/^[a-z]+:\/\//i.test(path)) return path;
    const normalized = path.startsWith('/') ? path : '/' + path;
    if (base && (normalized === base || normalized.startsWith(base + '/'))) return normalized;
    return base ? base + normalized : normalized;
  }

  const apiPath = appPath;

  let token = null;
  let latestControlsText = '';

  function renderSummaryText(text) {
    if (!text) return '';
    return text;
  }

  function parseMarkdownHeadings(text) {
    const sections = {};
    if (!text) return sections;
    const headingRegex = /^\\s*\\*\\*(.+?)\\*\\*\\s*$/gm;
    let match;
    const hits = [];
    while ((match = headingRegex.exec(text)) !== null) {
      hits.push({ title: match[1].trim(), index: match.index, end: headingRegex.lastIndex });
    }
    for (let i = 0; i < hits.length; i++) {
      const current = hits[i];
      const next = hits[i + 1];
      const content = text.slice(current.end, next ? next.index : text.length).trim();
      sections[current.title.toLowerCase()] = content;
    }
    return sections;
  }

  function extractTopControls(text) {
    const sections = parseMarkdownHeadings(text);
    const direct = sections['top 3 controls to implement'];
    if (direct) return direct;
    return '';
  }

  function openControlsModal() {
    if (!latestControlsText) return;
    document.getElementById('controlsContent').innerText = latestControlsText;
    document.getElementById('controlsModal').style.display = 'block';
  }

  function closeControlsModal() {
    document.getElementById('controlsModal').style.display = 'none';
  }

  function closeControlsModalOnBackdrop(event) {
    if (event.target && event.target.id === 'controlsModal') {
      closeControlsModal();
    }
  }

  async function verify() {
    const res = await fetch(apiPath('/api/demo-verify-pin'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ username: document.getElementById('username').value, pin: document.getElementById('pin').value })
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('verifyStatus').innerText = 'Verify failed: ' + (data.error || 'unknown');
      return;
    }
    token = data.token;
    document.getElementById('verifyStatus').innerText = 'Verified. Token issued.';
  }

  async function uploadCount() {
    if (!token) { alert('Verify first'); return; }
    const fileInput = document.getElementById('sheetFile');
    if (!fileInput.files.length) { alert('Select a file first'); return; }
    const form = new FormData();
    form.append('file', fileInput.files[0]);
    const res = await fetch(apiPath('/api/upload-count'), {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token },
      body: form
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('countOut').innerText = 'Upload failed: ' + (data.error || 'unknown error');
      return;
    }

    const ps = data.prebuilt_stats;
    let output = 'File: ' + data.file;
    if (data.site_code) output += '  |  Site: ' + data.site_code;
    output += '\\n\\n';

    if (ps) {
      output += ps.total_devices.toLocaleString() + ' devices  |  ' +
                ps.total_connections.toLocaleString() + ' connections  |  ' +
                ps.device_models + ' device models  |  ' +
                ps.optic_types + ' optic types\\n';
      if (ps.site) output += 'Site: ' + ps.site;
      if (ps.data_halls) output += '  |  Data halls: ' + ps.data_halls;
      output += '\\n\\n';

      const sc = ps.status_counts || {};
      const complete = sc['Cable Is Ran: Complete'] || sc['Cable Is Ran Complete'] || 0;
      const notTerm = sc['Cable Is Ran: Not Terminated'] || sc['Cable Is Ran Not Terminated'] || 0;
      const notRun = sc['Cable Not Run'] || 0;
      const total = ps.total_connections || 1;
      const pctDone = ((complete / total) * 100).toFixed(1);
      output += 'Cable status: ' + complete.toLocaleString() + ' complete (' + pctDone + '%)  |  ' +
                notTerm.toLocaleString() + ' not terminated  |  ' +
                notRun.toLocaleString() + ' not run\\n\\n';
    } else {
      const summary = data.summary || {};
      const lines = Object.entries(summary)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => k + ': ' + v);
      output += lines.length ? lines.join('\\n') : 'No counts found';
      output += '\\n\\n';
    }

    if (data.postgres && data.postgres.ok) {
      output += '[POSTGRES] Loaded ' + (data.postgres.connections_loaded || 0) + ' connections';
      if (data.postgres.profile) output += ' (profile: ' + data.postgres.profile.name + ')';
      output += '\\n';
    }
    output += 'Ready for questions. Ask Atlas anything about this cutsheet.';
    document.getElementById('countOut').innerText = output;
  }

  async function askAi() {
    if (!token) { alert('Verify first'); return; }
    const q = document.getElementById('question').value;
    const res = await fetch(apiPath('/api/sheet-qa'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token
      },
      body: JSON.stringify({ question: q })
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('qaOut').innerText = 'Q&A failed: ' + (data.error || 'unknown error');
      return;
    }
    const result = data.result || {};
    const answerText = renderSummaryText(result.answer);
    latestControlsText = extractTopControls(answerText);
    const controlsBtn = document.getElementById('controlsBtn');
    controlsBtn.style.display = latestControlsText ? 'inline-block' : 'none';
    const ts = result.timestamp ? new Date(result.timestamp * 1000).toLocaleString() : '';
    const inTok = result.input_tokens || 0;
    const outTok = result.output_tokens || 0;
    const elapsed = result.elapsed_seconds || 0;
    const provider = result.provider || '';
    const model = result.model || '';
    const source = result.context_source || '';
    const ctxTokens = result.context_tokens || 0;
    const qType = result.question_type || '';
    const qElapsed = result.query_elapsed || 0;

    let statsLine = '';
    if (provider) {
      statsLine = provider + ' / ' + model + '  |  ' +
        inTok.toLocaleString() + ' in + ' + outTok.toLocaleString() + ' out tokens  |  ' + elapsed + 's';
    }
    let contextLine = '';
    if (source) {
      contextLine = 'Context: ' + source;
      if (ctxTokens) contextLine += ' (' + ctxTokens + ' tokens)';
      if (qType) contextLine += '  |  Type: ' + qType;
      if (qElapsed) contextLine += '  |  Query: ' + qElapsed + 's';
    }

    document.getElementById('qaOut').innerText =
      'User: ' + (result.user || 'unknown') + '\\n' +
      'Time: ' + ts + '\\n' +
      (contextLine ? contextLine + '\\n' : '') +
      (statsLine ? statsLine + '\\n' : '') +
      '\\n' + answerText;
  }
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5050")), debug=False)
