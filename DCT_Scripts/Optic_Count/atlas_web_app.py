"""
atlas_web_app.py — MOCKUP
Full web version of the Atlas desktop app (Optic_Count_GUI.py).
Adds NetBox streaming (SSE) on top of the existing demo_web_app.py foundation.

To run:
    pip install flask
    python atlas_web_app.py

Desktop app is unchanged — still launch via Optic_Count_GUI_Main.py as before.
"""

import json
import logging
import os
import queue
import re
import secrets
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import io
from flask import Flask, Response, jsonify, request, send_file, session, stream_with_context
from werkzeug.utils import secure_filename
import openpyxl.utils.exceptions

import Define_Optic_Count
import Source_count_Netbox
import demo_auth_ai
import build_sheet_processor
import cutsheet_preprocessor
import ib_analyzer
import roce_analyzer
import netbox_dashboard_ingest
from netbox_dashboard_routes import netbox_dashboard_bp

log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB


def _resolve_flask_secret() -> str:
    # Doppler-provided FLASK_SECRET_KEY is the prod source of truth.
    env_secret = os.getenv("FLASK_SECRET_KEY", "").strip()
    if env_secret:
        return env_secret

    # Fallback: persist a random secret to disk so it survives gunicorn
    # process restarts within a single pod. Without this, the secret rotated
    # on every restart, invalidating every prior session cookie and breaking
    # /api/ask with "No sheet loaded" after the upload session was lost.
    secret_dir = Path(os.getenv("ATLAS_STATE_DIR", "./uploads"))
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / ".flask_secret"
    if secret_path.exists():
        try:
            return secret_path.read_text().strip() or secrets.token_hex(32)
        except OSError:
            pass
    generated = secrets.token_hex(32)
    try:
        secret_path.write_text(generated)
        secret_path.chmod(0o600)
    except OSError:
        pass
    return generated


app.secret_key = _resolve_flask_secret()

# When deployed under Canvas, BASE_PATH is injected by the CI build. Use it
# as a proxy for "running embedded in an iframe at *.coreweave.app" — in
# that context the session cookie needs SameSite=None + Secure or browsers
# will drop it, which manifested as /api/ask seeing a fresh user_id and
# reporting "No sheet loaded" after a successful upload. For local
# `docker compose up` on http://localhost we leave Flask defaults so the
# cookie still works over plain HTTP.
_IS_CANVAS_DEPLOYMENT = bool(os.getenv("BASE_PATH", "").strip())
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None" if _IS_CANVAS_DEPLOYMENT else "Lax",
    SESSION_COOKIE_SECURE=_IS_CANVAS_DEPLOYMENT,
)


def _normalize_base_path(value: str) -> str:
    trimmed = str(value or "").strip().strip("/")
    return f"/{trimmed}" if trimmed else ""


def _read_manifest_app_id() -> str:
    manifest_path = Path(__file__).resolve().parent / ".canvas" / "manifest.yaml"
    try:
        manifest = manifest_path.read_text()
    except OSError:
        return ""

    match = re.search(r"^id:\s*([a-z0-9-]+)\s*$", manifest, re.MULTILINE)
    return match.group(1) if match else ""


APP_BASE_PATH = _normalize_base_path(
    os.getenv("APP_BASE_PATH") or os.getenv("BASE_PATH") or ""
)
if not APP_BASE_PATH:
    manifest_app_id = _read_manifest_app_id()
    APP_BASE_PATH = f"/canvas-apps/{manifest_app_id}" if manifest_app_id else ""


class PrefixMiddleware:
    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "")
        if self.prefix and (
            path_info == self.prefix or path_info.startswith(f"{self.prefix}/")
        ):
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + self.prefix
            environ["PATH_INFO"] = path_info[len(self.prefix):] or "/"
        return self.app(environ, start_response)


app.wsgi_app = PrefixMiddleware(app.wsgi_app, APP_BASE_PATH)

# Canvas/Union embeds apps in an iframe from *.coreweave.app — frame-ancestors
# must allow that origin. X-Frame-Options: DENY is replaced by the CSP directive
# (X-Frame-Options is ignored when frame-ancestors is present in modern browsers).
_FRAME_ANCESTORS = "frame-ancestors 'self' https://*.coreweave.app"

_DASHBOARD_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    + _FRAME_ANCESTORS
)
_DEFAULT_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    + _FRAME_ANCESTORS
)


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # The /dashboard route loads Tailwind, Chart.js, and Google Fonts from CDNs.
    # All other routes keep the stricter default CSP.
    if request.path == "/dashboard":
        response.headers["Content-Security-Policy"] = _DASHBOARD_CSP
    else:
        response.headers["Content-Security-Policy"] = _DEFAULT_CSP
    return response


# Register the NetBox dashboard blueprint
app.register_blueprint(netbox_dashboard_bp)

UPLOAD_DIR = Path(os.getenv("ATLAS_UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# Server-side session store (keyed by session user ID)
USER_CONTEXT = {}
USER_SITE = {}  # session_user_id -> {"site_code": str, "site_id": int, "upload_id": int}
_state_lock = threading.Lock()

_CONTEXT_TTL_SECONDS = 2 * 60 * 60  # 2 hours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_session_user() -> str:
    """Return a stable per-browser-session user ID (UUID hex), creating one if needed."""
    return session.setdefault("user_id", secrets.token_hex(16))


def _evict_stale_contexts():
    with _state_lock:
        cutoff = time.time() - _CONTEXT_TTL_SECONDS
        stale = [u for u, ctx in USER_CONTEXT.items() if ctx.get("ts", 0) < cutoff]
        for u in stale:
            del USER_CONTEXT[u]


def _normalize_rack_id(rack: str) -> str:
    rack = (rack or "").strip()
    if rack.isdigit():
        return rack.zfill(3)
    return rack.lower()


def _rack_location_key(room: str, rack: str) -> str:
    room_norm = (room or "").strip().lower()
    rack_norm = _normalize_rack_id(rack)
    return f"{room_norm}:{rack_norm}" if room_norm and rack_norm else ""


def _question_matches_rack_result(question: str, rack_result: dict) -> bool:
    """Check whether a question appears to target the cached rack-analysis result."""
    try:
        import query_extractors as ext
    except Exception:
        return False

    lower = (question or "").lower()
    room = (rack_result.get("room") or "").strip().lower()
    rack = _normalize_rack_id(rack_result.get("rack") or "")
    if not room or not rack:
        return False

    extracted = (ext.extract_location(question) or "").strip().lower()
    if extracted:
        if extracted == f"{room}:{rack}":
            return True
        # Exact hall variants like dh202:041 should still match a DH2 rack-analysis result.
        if extracted.endswith(f":{rack}") and room.startswith("dh") and extracted.startswith(room):
            return True

    rack_tokens = {rack, rack.lstrip("0") or "0"}
    if "rack" in lower or "cab" in lower or "cabinet" in lower:
        if room in lower and any(re.search(rf"\b{re.escape(tok)}\b", lower) for tok in rack_tokens):
            return True

    return False


def _build_rack_context_for_llm(rack_result: dict) -> dict:
    """Convert Rack Analyzer output into compact preformatted context for the LLM."""
    room = rack_result.get("room", "")
    rack = rack_result.get("rack", "")
    location_key = _rack_location_key(room, rack)
    devices = rack_result.get("devices") or []
    optics = rack_result.get("optic_summary") or {}
    internal_labels = rack_result.get("internal_labels") or []
    cab_labels = rack_result.get("cab_to_cab_labels") or []

    lines = [
        f"Rack Analyzer result for {room} rack {rack}",
        f"Rack key: {location_key}" if location_key else "Rack key: unknown",
        (
            f"Total cables touching this rack: {rack_result.get('total_cables', 0)} | "
            f"Staying inside rack: {rack_result.get('internal_count', 0)} | "
            f"Leaving rack: {rack_result.get('cab_to_cab_count', 0)}"
        ),
    ]
    if rack_result.get("cab_type"):
        lines.append(f"Cab type: {rack_result['cab_type']}")

    lines.append("Devices Physically in Rack:")
    if devices:
        for dev in devices:
            lines.append(
                f"  RU {dev.get('ru') or '?'} | {dev.get('location') or '?'} | "
                f"{dev.get('dns_name') or '(no dns)'} | {dev.get('model') or '(no model)'} | "
                f"{dev.get('status') or '(no status)'}"
            )
    else:
        lines.append("  (none)")

    lines.append("Optic Summary:")
    if optics:
        for optic, count in optics.items():
            lines.append(f"  {optic}: {count}")
    else:
        lines.append("  (none)")

    lines.append("Cables Staying Inside This Rack:")
    if internal_labels:
        for label in internal_labels:
            lines.append(f"  {label}")
    else:
        lines.append("  (none)")

    lines.append("Cables Leaving This Rack:")
    if cab_labels:
        for label in cab_labels:
            lines.append(f"  {label}")
    else:
        lines.append("  (none)")

    context_text = "\n".join(lines)
    return {
        "source": "RACK_ANALYZER",
        "question_type": "rack_summary",
        "confidence": "high",
        "classification_reason": "cached Rack Analyzer workbook result",
        "room": room,
        "rack": rack,
        "location_key": location_key,
        "context": context_text,
        "row_count": len(devices) + len(internal_labels) + len(cab_labels),
        "query_elapsed_seconds": 0.0,
        "token_estimate": len(context_text.split()),
    }


def _sse_stream(target_fn, *args, **kwargs):
    """
    Run target_fn(*args, output_queue) in a background thread and yield
    its output as Server-Sent Events.  target_fn must signal completion
    by putting None into the queue (same contract as the desktop version).
    """
    q = queue.Queue(maxsize=500)
    threading.Thread(target=target_fn, args=(*args, q), kwargs=kwargs, daemon=True).start()

    def generate():
        while True:
            try:
                msg = q.get(timeout=60)
            except queue.Empty:
                break
            if msg is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {json.dumps(msg)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Site code extraction (mirrors demo_web_app logic)
# ---------------------------------------------------------------------------

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
_LOCODE_RE = re.compile(r"\b([A-Z]{2}-[A-Z0-9]{3,6})\b", re.I)


def _extract_site_code(save_path, prebuilt=None):
    """Extract site code from prebuilt context, SITE-VARS sheet, or filename."""
    if prebuilt:
        qr = prebuilt.get("quick_reference", {})
        site = qr.get("Site code?", "") or qr.get("site_code", "")
        if site and site.upper() != "UNKNOWN":
            return site.upper()
    if str(save_path).lower().endswith(".xlsx"):
        try:
            import pandas as pd
            xls = pd.ExcelFile(str(save_path), engine="calamine")
            for sn in xls.sheet_names:
                if sn.strip().casefold() in ("site-vars", "site_vars", "site vars", "sitevars"):
                    sv = pd.read_excel(xls, sheet_name=sn, header=None, engine="calamine")
                    for _, row in sv.iterrows():
                        key = str(row.iloc[0]).strip().lower() if len(row) > 0 else ""
                        val = str(row.iloc[1]).strip() if len(row) > 1 else ""
                        if key in ("site_code", "site code", "site", "locode") and val:
                            return val.upper()
        except Exception:
            pass
    filename = Path(str(save_path)).stem.upper()
    for token, code in _KNOWN_SITES.items():
        if token in filename:
            return code
    m = _LOCODE_RE.search(filename)
    if m:
        return m.group(1).upper()
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Health (liveness / readiness probe target)
# ---------------------------------------------------------------------------

_pg_cache: dict = {"ok": False, "ts": 0.0}
_PG_CACHE_TTL = 10.0  # seconds


def _check_postgres() -> bool:
    """Return True if Postgres is reachable. Cached for _PG_CACHE_TTL seconds
    so probe storms don't hammer the DB."""
    now = time.monotonic()
    if now - _pg_cache["ts"] < _PG_CACHE_TTL:
        return _pg_cache["ok"]
    try:
        from atlas_data_loader import check_postgres
        result = bool(check_postgres())
    except Exception:
        result = False
    _pg_cache["ok"] = result
    _pg_cache["ts"] = now
    return result


def _run_postgres_batch_job(username: str, save_paths: list, site_code: str, gen: int) -> None:
    """Load one or more cutsheets into a SINGLE Postgres upload after
    /api/upload-count returned (background). All sheets share one upload_id so
    they are queried together; prior active uploads for the site are replaced."""
    pg_result = None
    err_msg = None
    try:
        import atlas_data_loader

        pg_result = atlas_data_loader.load_files(
            save_paths, site_code, uploaded_by=username
        )
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        log.exception("Background Postgres batch load failed for user=%s", username)

    with _state_lock:
        ctx = USER_CONTEXT.get(username)
        if not ctx or ctx.get("_pg_import_gen") != gen:
            log.info(
                "Discarding pg batch result (stale or missing context) user=%s",
                username,
            )
            return
        ctx.pop("_postgres_import_pending", None)
        ctx.pop("_pg_import_gen", None)
        if err_msg:
            ctx["_postgres_import_error"] = err_msg[:800]
        elif pg_result and not pg_result.get("ok"):
            ctx["_postgres_import_error"] = str(pg_result.get("error") or "load_failed")[:800]
        else:
            ctx.pop("_postgres_import_error", None)
        USER_CONTEXT[username] = ctx

        if pg_result and pg_result.get("ok"):
            uid = pg_result.get("upload_id")
            sid = pg_result.get("site_id")
            if sid is not None and uid is not None:
                USER_SITE[username] = {
                    "site_code": site_code,
                    "site_id": sid,
                    "upload_id": uid,
                }
                log.info(
                    "Postgres batch load OK (async): site=%s upload_id=%s conns=%s files=%d",
                    site_code,
                    uid,
                    pg_result.get("connections_loaded"),
                    len(save_paths),
                )


@app.get("/api/health")
def health():
    """Kubernetes probe endpoint. Always returns 200 so a transient DB blip
    doesn't take the pod down; surface the DB state in the payload so ops
    can see it but liveness stays up as long as Flask is serving."""
    return jsonify({"ok": True, "postgres": _check_postgres()})


# ---------------------------------------------------------------------------
# Sheet upload + count
# ---------------------------------------------------------------------------

@app.post("/api/upload-count")
def upload_count():
    # Accept one or many cutsheets. Multiple files are loaded into a SINGLE
    # Postgres upload for one site so they are queried together (e.g. 4 US-LZL01
    # sheets). The field name stays "file" for back-compat; getlist picks up all.
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        return jsonify({"error": "No file provided"}), 400
    for f in files:
        if not f.filename.lower().endswith(".xlsx"):
            return jsonify({"error": f"Only .xlsx files are supported ({f.filename})"}), 400

    username = _get_session_user()
    # Real cutsheets often carry no detectable site code (load as UNKNOWN and
    # collide via the per-site soft-delete). Let the GUI pass an explicit code.
    site_code_override = (request.form.get("site_code") or "").strip().upper() or None
    include_by_status = request.form.get("include_by_status", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    saved = []  # list of (safe_name, save_path)
    for f in files:
        safe_name = secure_filename(f.filename)
        if not safe_name:
            return jsonify({"error": "Invalid filename"}), 400
        unique_name = f"{int(time.time())}_{username}_{len(saved)}_{safe_name}"
        save_path = UPLOAD_DIR / unique_name
        f.save(save_path)
        saved.append((safe_name, save_path))

    # Resolve site code once for the whole batch (explicit override wins).
    site_code = site_code_override or _extract_site_code(saved[0][1])
    pg_available = _check_postgres()

    # Run the optic-count preprocessor per file synchronously (counts are the
    # point of this endpoint); aggregate the text. Postgres ingest runs after.
    parts = []
    files_ctx = []
    summary_merged: dict = {}
    multi = len(saved) > 1
    try:
        for safe_name, save_path in saved:
            prep = None
            try:
                prep = cutsheet_preprocessor.preprocess_upload(str(save_path))
                txt = cutsheet_preprocessor.format_optic_count_text(prep["optic_counts"])
                if prep.get("unknown_statuses"):
                    unknowns = ", ".join(f"{v} ({c})" for v, c in prep["unknown_statuses"])
                    txt += f"\n\nWarning: {len(prep['unknown_statuses'])} unknown status values: {unknowns}"
                for k, v in prep["optic_counts"]["by_type"].items():
                    summary_merged[k] = summary_merged.get(k, 0) + v["total"]
            except Exception as prep_exc:  # noqa: BLE001
                log.warning("Preprocessor failed for %s, legacy path: %s", safe_name, prep_exc)
                txt = Define_Optic_Count.count_all_files_gui([str(save_path)])
            if include_by_status:
                try:
                    status_block = Define_Optic_Count.count_all_files_gui_by_status([str(save_path)])
                    txt = txt + "\n\n" + ("=" * 72) + "\n\n" + status_block
                except Exception as status_exc:  # noqa: BLE001
                    log.warning("include_by_status block failed for %s: %s", safe_name, status_exc)
                    txt += f"\n\n(Warning: in-service sort failed: {status_exc})\n"
            parts.append(f"===== {safe_name} =====\n{txt}" if multi else txt)
            files_ctx.append({"file_name": safe_name, "file_path": str(save_path)})
        result_text = "\n\n".join(parts)
    except Exception:
        log.exception("File upload processing failed")
        return jsonify({"error": "File processing failed"}), 500
    finally:
        Define_Optic_Count.clear_excel_cache()

    context = {"files": files_ctx, "ts": time.time()}
    if not pg_available:
        # In-memory fallback: expose merged optic summary for the ask path.
        context["summary"] = summary_merged
        context["parser_warnings"] = []

    pg_import_gen = int(time.time() * 1000) & 0x7FFFFFFF
    if pg_available:
        context["_postgres_import_pending"] = True
        context["_pg_import_gen"] = pg_import_gen

    _evict_stale_contexts()
    with _state_lock:
        USER_CONTEXT[username] = context

    if pg_available:
        threading.Thread(
            target=_run_postgres_batch_job,
            args=(username, [str(p) for _, p in saved], site_code, pg_import_gen),
            name="atlas-pg-upload",
            daemon=True,
        ).start()

    resp = {
        "ok": True,
        "file": saved[0][0],          # back-compat (single-file callers)
        "files": [n for n, _ in saved],
        "output": result_text,
        "site_code": site_code,
        "pg_loaded": "pending" if pg_available else "skipped",
    }
    if pg_available:
        resp["pg_message"] = (
            f"Saving {len(saved)} cutsheet(s) to the database in the background "
            f"under site {site_code} — counts above are ready now."
        )
    return jsonify(resp)


@app.post("/api/count-by-status")
def count_by_status():
    username = _get_session_user()
    if username not in USER_CONTEXT:
        return jsonify({"error": "No file loaded — upload first"}), 400

    # Re-run against the saved file path stored in context
    files = [f["file_path"] for f in USER_CONTEXT[username].get("files", [])]
    try:
        result_text = Define_Optic_Count.count_all_files_gui_by_status(files)
    except (FileNotFoundError, OSError):
        return jsonify({"error": "File no longer available — re-upload to refresh"}), 400
    return jsonify({"ok": True, "output": result_text})


# ---------------------------------------------------------------------------
# NetBox — SSE streaming endpoints
# The queue-based contract in Source_count_Netbox is reused unchanged.
# ---------------------------------------------------------------------------

@app.get("/api/stream/netbox")
def stream_netbox():
    """Single-site NetBox inventory — streams live as results arrive."""
    site_name = request.args.get("site", "").strip() or "us-west-09a"
    active_only = request.args.get("active_only", "true").lower() != "false"
    include_optic_locations = request.args.get("include_optic_locations", "false").lower() == "true"
    return _sse_stream(Source_count_Netbox.get_site_inventory, site_name, active_only=active_only, include_optic_locations=include_optic_locations)


@app.get("/api/stream/all-sites")
def stream_all_sites():
    """All-sites NetBox inventory — streams per-site progress live."""
    active_only = request.args.get("active_only", "true").lower() != "false"
    include_optic_locations = request.args.get("include_optic_locations", "false").lower() == "true"
    return _sse_stream(Source_count_Netbox.get_all_sites_inventory, active_only=active_only, include_optic_locations=include_optic_locations)


# ---------------------------------------------------------------------------
# Rack Analyzer
# ---------------------------------------------------------------------------

@app.post("/api/buildsheet")
def buildsheet():
    if "cutsheet" not in request.files:
        return jsonify({"error": "'cutsheet' file is required"}), 400

    room = request.form.get("room", "").strip()
    rack = request.form.get("rack", "").strip()
    if not room or not rack:
        return jsonify({"error": "room and rack are required"}), 400

    cutsheet_file = request.files["cutsheet"]
    template_file = request.files.get("template")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_cut:
        cutsheet_file.save(tmp_cut.name)
        cut_path = tmp_cut.name

    tpl_path = None
    if template_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_tpl:
            template_file.save(tmp_tpl.name)
            tpl_path = tmp_tpl.name

    try:
        result = build_sheet_processor.process_rack(cut_path, tpl_path, room, rack)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except (FileNotFoundError, OSError, openpyxl.utils.exceptions.InvalidFileException, zipfile.BadZipFile):
        log.exception("Rack sheet generation failed")
        return jsonify({"error": "File processing failed"}), 500
    finally:
        try:
            os.unlink(cut_path)
            if tpl_path:
                os.unlink(tpl_path)
        except OSError:
            pass

    username = _get_session_user()
    _evict_stale_contexts()
    with _state_lock:
        user_ctx = USER_CONTEXT.get(username, {"summary": {}, "files": []})
        user_ctx["ts"] = time.time()
        user_ctx["_last_rack_result"] = result
        USER_CONTEXT[username] = user_ctx

    return jsonify({"ok": True, "data": result})


@app.post("/api/buildsheet/layout")
def buildsheet_layout():
    if "cutsheet" not in request.files:
        return jsonify({"error": "'cutsheet' file is required"}), 400
    room = request.form.get("room", "").strip()
    if not room:
        return jsonify({"error": "room is required"}), 400

    cutsheet_file = request.files["cutsheet"]
    template_file = request.files.get("template")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_cut:
        cutsheet_file.save(tmp_cut.name)
        cut_path = tmp_cut.name

    tpl_path = None
    if template_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_tpl:
            template_file.save(tmp_tpl.name)
            tpl_path = tmp_tpl.name

    try:
        excel_bytes = build_sheet_processor.generate_layout_workbook(cut_path, tpl_path, room)
    except ValueError:
        return jsonify({"error": "Invalid input parameters"}), 400
    except Exception:
        log.exception("Layout workbook generation failed")
        return jsonify({"error": "File processing failed"}), 500
    finally:
        try:
            os.unlink(cut_path)
            if tpl_path:
                os.unlink(tpl_path)
        except OSError:
            pass

    return send_file(
        io.BytesIO(excel_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"layout_{room.upper()}.xlsx",
    )


@app.post("/api/buildsheet/dh")
def buildsheet_dh():
    if "cutsheet" not in request.files:
        return jsonify({"error": "'cutsheet' file is required"}), 400

    room = request.form.get("room", "").strip()
    if not room:
        return jsonify({"error": "room is required"}), 400

    cutsheet_file = request.files["cutsheet"]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_cut:
        cutsheet_file.save(tmp_cut.name)
        cut_path = tmp_cut.name

    try:
        result = build_sheet_processor.process_room(cut_path, room)
    except ValueError:
        return jsonify({"error": "Invalid input parameters"}), 400
    except (FileNotFoundError, OSError, openpyxl.utils.exceptions.InvalidFileException, zipfile.BadZipFile):
        log.exception("Room sheet generation failed")
        return jsonify({"error": "File processing failed"}), 500
    finally:
        try:
            os.unlink(cut_path)
        except OSError:
            pass

    return jsonify({"ok": True, "data": result})


# ---------------------------------------------------------------------------
# IB Analyzer
# ---------------------------------------------------------------------------

@app.post("/api/ib-query")
def ib_query():
    if "file" not in request.files:
        return jsonify({"error": "'file' is required"}), 400
    device = request.form.get("device", "").strip()
    if not device:
        return jsonify({"error": "'device' is required"}), 400

    ib_file = request.files["file"]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        ib_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = ib_analyzer.query_device(tmp_path, device)
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 400
    except Exception:
        log.exception("IB query failed")
        return jsonify({"error": "File processing failed"}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if result["total"] == 0:
        return jsonify({"error": f"Device '{device}' not found in any sheet"}), 404

    return jsonify({"ok": True, "data": result})


# ---------------------------------------------------------------------------
# RoCE Analyzer
# ---------------------------------------------------------------------------

@app.post("/api/roce-query")
def roce_query():
    if "file" not in request.files:
        return jsonify({"error": "'file' is required"}), 400
    loc = request.form.get("loc", "").strip()
    if not loc:
        return jsonify({"error": "'loc' is required"}), 400

    roce_file = request.files["file"]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        roce_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = roce_analyzer.query_location(tmp_path, loc)
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 400
    except Exception:
        log.exception("RoCE query failed")
        return jsonify({"error": "File processing failed"}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if result["total"] == 0:
        return jsonify({"error": f"Location '{loc}' not found in any sheet"}), 404

    return jsonify({"ok": True, "data": result})


# ---------------------------------------------------------------------------
# AI Q&A
# ---------------------------------------------------------------------------

def _get_latest_upload_for_user(conn, username):
    """Return {site_code, site_id, upload_id} for the user's latest active upload, or None."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.id AS site_id, s.site_code, cu.id AS upload_id
               FROM cutsheet_uploads cu
               JOIN sites s ON cu.site_id = s.id
               WHERE cu.uploaded_by = %s AND cu.is_active = TRUE
               ORDER BY cu.created_at DESC LIMIT 1""",
            (username,),
        )
        row = cur.fetchone()
    if row:
        return {"site_code": row[1], "site_id": row[0], "upload_id": row[2]}
    return None


@app.post("/api/ask")
def ask_ai():
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    username = _get_session_user()

    # --- Meta-questions about what's loaded short-circuit before Postgres ---
    _meta_re = re.compile(
        r"\b(?:what|which)\b.*\b(?:file|sheet|cutsheet|upload|workbook|xlsx)\b.*\b(?:loaded|uploaded|active|current|using)\b",
        re.IGNORECASE,
    )
    _meta_re2 = re.compile(
        r"\b(?:file|sheet|cutsheet|upload)\s+(?:is\s+)?(?:loaded|uploaded|active|current)\b",
        re.IGNORECASE,
    )
    if _meta_re.search(question) or _meta_re2.search(question):
        with _state_lock:
            _ctx = USER_CONTEXT.get(username) or {}
        _files = _ctx.get("files") or []
        if _files:
            _names = [f.get("file_name") or Path(f.get("file_path", "")).name for f in _files]
            _msg = "Loaded file(s): " + ", ".join(_names)
            return jsonify({
                "ok": True,
                "result": {
                    "answer": _msg,
                    "timestamp": int(time.time()),
                    "model": "atlas-meta",
                    "provider": "atlas",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "elapsed_seconds": 0,
                },
                "context_source": "META",
                "question_type": "file_loaded",
            })
        return jsonify({"error": "No sheet loaded — upload a file first"}), 400

    # --- If a cutsheet upload just started Postgres ingest, wait (bounded) ---
    with _state_lock:
        _pend_ctx = USER_CONTEXT.get(username)
    if _pend_ctx and _pend_ctx.get("_postgres_import_pending"):
        _deadline = time.monotonic() + 120.0
        while time.monotonic() < _deadline:
            time.sleep(0.25)
            with _state_lock:
                if not USER_CONTEXT.get(username, {}).get("_postgres_import_pending"):
                    break
        with _state_lock:
            if USER_CONTEXT.get(username, {}).get("_postgres_import_pending"):
                return jsonify(
                    {
                        "error": (
                            "Database import still running (large file). "
                            "Wait up to two minutes and try again."
                        ),
                        "detail": "pending_postgres_import",
                    }
                ), 503

    # --- Try Postgres context first ---
    pg_fallback_reason = None

    with _state_lock:
        site_info = USER_SITE.get(username)

    if not site_info and not _check_postgres():
        pg_fallback_reason = "postgres_unreachable"

    # If no in-memory site info, try recovering from Postgres
    if not site_info and _check_postgres():
        try:
            from atlas_data_loader import managed_connection
            with managed_connection() as conn:
                recovered = _get_latest_upload_for_user(conn, username)
            if recovered:
                site_info = recovered
                with _state_lock:
                    USER_SITE[username] = site_info
                log.info("Recovered site context from Postgres for user=%s", username)
        except Exception as exc:
            log.warning("Postgres site recovery failed: %s", exc)

    if not site_info and pg_fallback_reason is None:
        pg_fallback_reason = "no_active_upload_for_user"

    pg_context = None
    if site_info and _check_postgres():
        try:
            from atlas_postgres_context import build_postgres_context
            _t0 = time.monotonic()
            pg_context = build_postgres_context(
                question, site_info["site_id"],
                upload_id=site_info.get("upload_id"),
            )
            _elapsed = time.monotonic() - _t0
            if _elapsed > 30:
                log.warning(
                    "build_postgres_context took %.1fs (>30s threshold) for user=%s",
                    _elapsed, username,
                )
            if pg_context and "error" not in pg_context:
                if site_info and site_info.get("site_code"):
                    pg_context["site_code"] = site_info["site_code"]
                log.info(
                    "Postgres context: type=%s tokens=%s elapsed=%ss",
                    pg_context.get("question_type"),
                    pg_context.get("token_estimate"),
                    pg_context.get("query_elapsed_seconds"),
                )
        except Exception as exc:
            log.warning("Postgres context build failed (falling back): %s", exc)
            pg_context = None
            pg_fallback_reason = f"build_failed: {exc}"

    if pg_context and "error" in pg_context:
        pg_fallback_reason = f"context_error: {pg_context['error']}"
        pg_context = None

    # Build sheet context: try in-memory, fall back to Postgres
    with _state_lock:
        sheet_context = USER_CONTEXT.get(username)

    if not sheet_context and not pg_context:
        return jsonify({"error": "No sheet loaded — upload a file first"}), 400

    if not sheet_context:
        sheet_context = {"summary": {}, "files": [], "ts": time.time()}

    rack_ctx = None
    if sheet_context.get("_last_rack_result") and _question_matches_rack_result(question, sheet_context["_last_rack_result"]):
        rack_ctx = _build_rack_context_for_llm(sheet_context["_last_rack_result"])
        # Prefer the cached Rack Analyzer when:
        # (a) Postgres returned nothing or low confidence, OR
        # (b) Postgres returned a generic rack_summary (all racks) but the
        #     cached result has specific per-rack data (devices, optics, cables)
        pg_is_generic_rack = (
            pg_context
            and pg_context.get("question_type") == "rack_summary"
            and pg_context.get("row_count", 0) > 1
        )
        rack_has_detail = rack_ctx.get("row_count", 0) > 0
        if (not pg_context
                or pg_context.get("row_count", 0) == 0
                or pg_context.get("confidence") == "low"
                or (pg_is_generic_rack and rack_has_detail)):
            with _state_lock:
                sheet_context["_active_rack_context"] = rack_ctx
            pg_context = None
            log.info(
                "Using cached Rack Analyzer context for user=%s room=%s rack=%s",
                username,
                rack_ctx.get("room"),
                rack_ctx.get("rack"),
            )

    if pg_context and "error" not in pg_context:
        if pg_context.get("confidence") == "low":
            log.info(
                "Demoting low-confidence Postgres context for user=%s type=%s — using in-memory sheet",
                username, pg_context.get("question_type"),
            )
            pg_context = None
            pg_fallback_reason = "low_confidence_postgres"
        else:
            with _state_lock:
                sheet_context["_postgres_context"] = pg_context

    _raw = demo_auth_ai.ask_grounded(question, sheet_context)
    result = {
        "answer": _raw.get("answer", ""),
        "timestamp": int(time.time()),
        "model": _raw.get("model", ""),
        "provider": _raw.get("provider", ""),
        "input_tokens": _raw.get("input_tokens", 0),
        "output_tokens": _raw.get("output_tokens", 0),
        "elapsed_seconds": _raw.get("elapsed_seconds", 0),
    }

    # Add Postgres metadata to response
    resp = {"ok": True, "result": result}

    # Determine context source and add diagnostic fields
    if pg_context and "error" not in pg_context:
        resp["context_source"] = "POSTGRES"
        resp["question_type"] = pg_context.get("question_type", "")
        resp["upload_id"] = site_info.get("upload_id") if site_info else None
        resp["row_count"] = pg_context.get("row_count", 0)
        resp["classification_confidence"] = pg_context.get("confidence")
        resp["classification_reason"] = pg_context.get("classification_reason")
    elif rack_ctx:
        resp["context_source"] = "RACK_ANALYZER"
        resp["question_type"] = rack_ctx.get("question_type", "")
    elif sheet_context.get("summary") or sheet_context.get("files"):
        resp["context_source"] = "IN_MEMORY"
    else:
        resp["context_source"] = "EMPTY_FALLBACK"

    if resp["context_source"] != "POSTGRES" and pg_fallback_reason:
        resp["pg_fallback_reason"] = pg_fallback_reason

    return jsonify(resp)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return HTML_PAGE


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔬</text></svg>"/>
  <title>Aperture — DCT Infrastructure Intelligence</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: Arial, sans-serif; max-width: 1000px; margin: 24px auto; padding: 0 12px; background: #F9FAFC; color: #343338; }
    h1 { font-size: 1.4rem; margin-bottom: 4px; color: #343338; }
    h3 { margin: 0 0 10px 0; font-size: 1rem; color: #343338; }
    .section { border: 1px solid #CDCED6; border-radius: 6px; padding: 14px; margin-bottom: 14px; background: #fff; }
    input[type=text], input[type=file] { width: 100%; padding: 7px; margin: 4px 0 8px 0; border: 1px solid #CDCED6; border-radius: 4px; }
    input[type=text]:focus { outline: none; border-color: #2741E7; box-shadow: 0 0 0 2px #DAE5FF; }
    .btn-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
    button { padding: 7px 14px; border: 1px solid #2741E7; border-radius: 4px; background: #2741E7; color: #fff; cursor: pointer; }
    button:hover { background: #4665FF; border-color: #4665FF; }
    button:disabled { opacity: 0.5; cursor: default; }
    .output {
      background: #F3F3F5; border: 1px solid #CDCED6; border-radius: 4px;
      padding: 10px; font-family: monospace; font-size: 0.85rem;
      white-space: pre-wrap; word-break: break-word; min-height: 60px;
      max-height: 400px; overflow-y: auto;
    }
    .status-bar { font-size: 0.8rem; color: #747283; margin: 4px 0 6px 0; min-height: 18px; }
    .spinner { display: none; }
    .spinner.active { display: inline-block; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spinner svg { animation: spin 0.9s linear infinite; vertical-align: middle; }
    nav { display: flex; gap: 2px; margin: 16px 0 0 0; background: #343338; border-radius: 6px 6px 0 0; padding: 6px 8px 0 8px; }
    nav a { padding: 8px 18px; color: #CDCED6; text-decoration: none; border-radius: 4px 4px 0 0; font-size: 0.95rem; cursor: pointer; }
    nav a:hover { background: #2741E7; color: #fff; }
    nav a.active { background: #fff; color: #2741E7; font-weight: bold; }
    .page { display: none; padding-top: 14px; }
    .page.active { display: block; }
  </style>
</head>
<body>
  <h1>Aperture — DCT Infrastructure Intelligence <a href="https://coreweave.atlassian.net/wiki/spaces/~71202033c11abfc5ac4e7295722dd23b043a53/pages/1702789201/Atlas+DCT+Infrastructure+Intelligence+User+Guide" target="_blank" rel="noopener" style="font-size:0.55em; font-weight:normal; vertical-align:middle; margin-left:14px; background:#1868db; color:#fff; padding:4px 12px; border-radius:5px; text-decoration:none;">Documentation</a></h1>

  <nav>
    <a class="active" onclick="showPage('main', this)">Optic Inventory</a>
    <a onclick="showPage('buildsheet', this)">Rack Analyzer</a>
    <a onclick="showPage('ib', this)">IB Rack Analyzer</a>
    <a onclick="showPage('roce', this)">RoCE Rack Analyzer</a>
  </nav>

  <!-- Page: Main -->
  <div class="page active" id="page-main">

  <!-- 2. Sheet Count -->
  <div class="section">
    <h3>Build Sheet Optic Count</h3>
    <input type="file" id="sheetFile" accept=".xlsx" multiple/>
    <div style="margin:8px 0; font-size:0.85rem; opacity:0.8;">Tip: select multiple cutsheets (Cmd/Ctrl-click) to load them together as one site.</div>
    <div style="margin:8px 0;">
      <label for="siteCode" style="font-size:0.85rem;">Site code (optional, groups the sheets):</label>
      <input type="text" id="siteCode" placeholder="e.g. US-LZL01" style="width:160px; margin-left:6px;"/>
    </div>
    <div class="btn-row">
      <button onclick="uploadCount()">Count</button>
      <button onclick="countByStatus()">Count, Sort by In Service</button>
    </div>
    <div class="status-bar" id="countStatus"></div>
    <div class="output" id="countOut"></div>
  </div>

  <!-- 3. NetBox -->
  <div class="section">
    <h3>NetBox Inventory</h3>
    <input type="text" id="netboxSite" placeholder="Site slug or name (e.g. us-west-09a)" style="width:300px"/>
    <div style="margin: 6px 0;">
      <label><input type="checkbox" id="netboxActiveOnly" checked/> Count In Service items only</label>
    </div>
    <div style="margin: 6px 0;">
      <label><input type="checkbox" id="netboxOpticLocations"/> Include itemized optic locations</label>
    </div>
    <div class="btn-row">
      <button onclick="streamNetbox()">Netbox</button>
      <button onclick="streamAllSites()">All Sites</button>
    </div>
    <div class="status-bar" id="netboxStatus">
      <span class="spinner" id="netboxSpinner">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
          <path d="M12 2a10 10 0 0 1 10 10"/>
        </svg>
      </span>
      <span id="netboxStatusText"></span>
    </div>
    <div class="output" id="netboxOut"></div>
  </div>

  <!-- 4. AI Q&A -->
  <div class="section">
    <h3>Ask Aperture (Sheet Context)</h3>
    <input type="text" id="question" placeholder="Ask a question about your loaded cutsheet..."/>
    <div class="btn-row">
      <button onclick="askAi()">Ask AI</button>
    </div>
    <div class="status-bar" id="qaStatus"></div>
    <div class="output" id="qaOut"></div>
  </div>

  </div><!-- end page-main -->

  <!-- Page: Rack Analyzer -->
  <div class="page" id="page-buildsheet">

  <div class="section">
    <h3>Rack Analyzer — Rack Query</h3>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px;">
      <div>
        <label style="font-size:0.85rem; font-weight:bold;">Cutsheet Master</label>
        <input type="file" id="bsCutsheet" accept=".xlsx"/>
      </div>
      <div>
        <label style="font-size:0.85rem; font-weight:bold;">Master Region Template <span style="font-weight:normal; color:#747283;">(optional — enables cab type, elevation &amp; unused devices)</span></label>
        <input type="file" id="bsTemplate" accept=".xlsx"/>
      </div>
    </div>
    <div style="display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap; margin-bottom:10px;">
      <div>
        <label style="font-size:0.85rem; font-weight:bold; display:block;">Room Designator</label>
        <input type="text" id="bsRoom" placeholder="e.g. DH2" style="width:160px; margin:0;"/>
      </div>
      <div>
        <label style="font-size:0.85rem; font-weight:bold; display:block;">Rack Number</label>
        <input type="text" id="bsRack" placeholder="e.g. 121" style="width:120px; margin:0;"/>
      </div>
      <button onclick="runBuildSheet()" id="bsBtn">Query Rack</button>
      <button onclick="clearBuildSheetFiles()" style="background:#F3F3F5; color:#747283; border-color:#CDCED6;">Clear Files</button>
    </div>
    <div class="status-bar" id="bsStatus"></div>
  </div>

  <!-- DH-wide label download -->
  <div class="section" id="bsDHSection" style="display:none;">
    <button id="bsDHBtn" onclick="downloadDHLabels()" style="font-size:1rem; padding:9px 18px;">Download all DH labels</button>
    <div class="status-bar" id="bsDHStatus"></div>
  </div>

  <!-- Summary -->
  <div class="section" id="bsSummarySection" style="display:none;">
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <h3 style="margin:0;">Summary</h3>
      <button onclick="downloadAllLabels()">Download all Rack labels</button>
      <button onclick="downloadLayout()" id="bsLayoutBtn">Download Used Cab Layouts</button>
      <span id="bsLayoutStatus" style="font-size:0.8rem; color:#747283;"></span>
    </div>
    <div id="bsSummary" style="font-family:monospace; font-size:0.9rem;"></div>
    <div id="bsCabType" style="margin-top:8px; font-size:0.9rem;"></div>
    <div id="bsCabTypeSummaryWrap" style="display:none; margin-top:12px;">
      <strong style="font-size:0.9rem;">Cab Types in DH</strong>
      <table id="bsCabTypeSummaryTable" style="margin-top:6px; border-collapse:collapse; font-size:0.85rem;">
        <thead>
          <tr style="background:#DAE5FF;">
            <th style="text-align:left; padding:4px 10px; border:1px solid #CDCED6;">Cab Type</th>
            <th style="text-align:right; padding:4px 10px; border:1px solid #CDCED6;">Count</th>
          </tr>
        </thead>
        <tbody id="bsCabTypeSummaryBody"></tbody>
      </table>
    </div>
  </div>

  <!-- Elevation -->
  <div class="section" id="bsElevationSection" style="display:none;">
    <h3>Cab Elevation — <span id="bsCabTypeLabel"></span> <span style="font-size:0.8rem; font-weight:normal; color:#747283;">— Source: Region Template</span></h3>
    <table id="bsElevationTable" style="width:100%; border-collapse:collapse; font-size:0.85rem;">
      <thead>
        <tr style="background:#DAE5FF;">
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">RU</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Device Name</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Device Type</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Cabling Found</th>
        </tr>
      </thead>
      <tbody id="bsElevationBody"></tbody>
    </table>
  </div>

  <!-- Devices -->
  <div class="section" id="bsDevicesSection" style="display:none;">
    <h3>Devices Physically in Rack <span style="font-size:0.8rem; font-weight:normal; color:#747283;">— Source: Cutsheet and SITE-HOSTS</span></h3>
    <div style="font-size:0.82rem; color:#747283; margin:0 0 8px 0;">
      This section lists equipment located in the queried rack. It does not mean every listed cable stays inside the rack.
    </div>
    <table id="bsDevicesTable" style="width:100%; border-collapse:collapse; font-size:0.85rem;">
      <thead>
        <tr style="background:#DAE5FF;">
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">RU</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Location</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">DNS Name</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Model</th>
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Status</th>
        </tr>
      </thead>
      <tbody id="bsDevicesBody"></tbody>
    </table>
  </div>

  <!-- Optic Summary -->
  <div class="section" id="bsOpticSection" style="display:none;">
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <h3 style="margin:0;">Optic Summary</h3>
      <button onclick="downloadOpticSummary()">Download Optic Summary</button>
    </div>
    <div style="display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start;">
      <div>
        <table id="bsOpticTable" style="border-collapse:collapse; font-size:0.85rem;">
          <thead>
            <tr style="background:#DAE5FF;">
              <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Optic Type</th>
              <th style="text-align:right; padding:5px 8px; border:1px solid #CDCED6;">Count</th>
            </tr>
          </thead>
          <tbody id="bsOpticBody"></tbody>
        </table>
      </div>
      <div style="flex:1; min-width:320px;">
        <table id="bsOpticLocTable" style="width:100%; border-collapse:collapse; font-size:0.85rem;">
          <thead>
            <tr style="background:#DAE5FF;">
              <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Location</th>
              <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Port</th>
              <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Optic Type</th>
            </tr>
          </thead>
          <tbody id="bsOpticLocBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Internal Cables -->
  <div class="section" id="bsInternalSection" style="display:none;">
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <h3 style="margin:0;">Cables Staying Inside This Rack (<span id="bsInternalCount">0</span>)</h3>
      <button onclick="downloadCableMapCsv('internal')">Download Cable Map</button>
      <button onclick="downloadLabels('internal')">Download Labels</button>
    </div>
    <div style="font-size:0.82rem; color:#747283; margin:0 0 8px 0;">
      Both cable endpoints are in the queried rack.
    </div>
    <div style="overflow-x:auto; max-height:400px; overflow-y:auto;">
      <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
        <thead>
          <tr style="background:#DAE5FF; position:sticky; top:0;">
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">A Location</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">A Port</th>
            <th style="text-align:center; padding:5px 8px; border:1px solid #CDCED6; color:#747283;">→</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Z Location</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Z Port</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Cable</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Status</th>
          </tr>
        </thead>
        <tbody id="bsInternalBody"></tbody>
      </table>
    </div>
  </div>

  <!-- Cab-to-Cab Cables -->
  <div class="section" id="bsCabSection" style="display:none;">
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <h3 style="margin:0;">Cables Leaving This Rack (<span id="bsCabCount">0</span>)</h3>
      <button onclick="downloadCableMapCsv('cab')">Download Cable Map</button>
      <button onclick="downloadLabels('cab')">Download Labels</button>
    </div>
    <div style="font-size:0.82rem; color:#747283; margin:0 0 8px 0;">
      One cable endpoint is in the queried rack and the other endpoint is in a different rack, room, or hall.
    </div>
    <div style="overflow-x:auto; max-height:400px; overflow-y:auto;">
      <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
        <thead>
          <tr style="background:#DAE5FF; position:sticky; top:0;">
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Bundle</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">This Rack Location</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Port</th>
            <th style="text-align:center; padding:5px 8px; border:1px solid #CDCED6; color:#747283;">→</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Remote Location</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Port</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Cable</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Status</th>
          </tr>
        </thead>
        <tbody id="bsCabBody"></tbody>
      </table>
    </div>
  </div>

  </div><!-- end page-buildsheet -->

  <!-- Page: IB Rack Analyzer -->
  <div class="page" id="page-ib">

  <div class="section">
    <h3>IB Rack Analyzer — Device Query</h3>
    <div style="margin-bottom:10px;">
      <label style="font-size:0.85rem; font-weight:bold;">IB Build Sheet</label>
      <input type="file" id="ibFile" accept=".xlsx"/>
    </div>
    <div style="display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap; margin-bottom:10px;">
      <div>
        <label style="font-size:0.85rem; font-weight:bold; display:block;">Device Name</label>
        <input type="text" id="ibDevice" placeholder="e.g. S2.1.1 or L30.1.1-DH1" style="width:240px; margin:0;" onkeydown="if(event.key==='Enter') runIBQuery()"/>
      </div>
      <button onclick="runIBQuery()" id="ibBtn">Query Device</button>
    </div>
    <div class="status-bar" id="ibStatus"></div>
  </div>

  <div class="section" id="ibOpticSection" style="display:none;">
    <h3>Optic Summary — <span id="ibDeviceLabel"></span></h3>
    <table style="border-collapse:collapse; font-size:0.85rem;">
      <thead>
        <tr style="background:#DAE5FF;">
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Optic Type</th>
          <th style="text-align:right; padding:5px 8px; border:1px solid #CDCED6;">Count</th>
        </tr>
      </thead>
      <tbody id="ibOpticBody"></tbody>
    </table>
  </div>

  <div class="section" id="ibConnSection" style="display:none;">
    <h3>Connections (<span id="ibConnCount">0</span>)</h3>
    <div style="overflow-x:auto; max-height:600px; overflow-y:auto;">
      <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
        <thead>
          <tr style="background:#DAE5FF; position:sticky; top:0;">
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Port</th>
            <th style="text-align:center; padding:5px 8px; border:1px solid #CDCED6; color:#747283;">→</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Remote Device</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Remote Port</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Optic</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Cable</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Status</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Sheet</th>
          </tr>
        </thead>
        <tbody id="ibConnBody"></tbody>
      </table>
    </div>
  </div>

  </div><!-- end page-ib -->

  <!-- Page: RoCE Rack Analyzer -->
  <div class="page" id="page-roce">

  <div class="section">
    <h3>RoCE Rack Analyzer — Device Query</h3>
    <div style="margin-bottom:10px;">
      <label style="font-size:0.85rem; font-weight:bold;">RoCE Build Sheet</label>
      <input type="file" id="roceFile" accept=".xlsx"/>
    </div>
    <div style="display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap; margin-bottom:10px;">
      <div>
        <label style="font-size:0.85rem; font-weight:bold; display:block;">Device Location (LOC:CAB:RU)</label>
        <input type="text" id="roceLoc" placeholder="e.g. dh202:003:37" style="width:220px; margin:0;" onkeydown="if(event.key==='Enter') runRoCEQuery()"/>
      </div>
      <button onclick="runRoCEQuery()" id="roceBtn">Query Device</button>
    </div>
    <div class="status-bar" id="roceStatus"></div>
  </div>

  <div class="section" id="roceOpticSection" style="display:none;">
    <h3>Optic Summary — <span id="roceLocLabel"></span> <span id="roceDnsLabel" style="font-weight:normal; font-size:0.85rem; color:#747283;"></span></h3>
    <table style="border-collapse:collapse; font-size:0.85rem;">
      <thead>
        <tr style="background:#DAE5FF;">
          <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Optic Type</th>
          <th style="text-align:right; padding:5px 8px; border:1px solid #CDCED6;">Count</th>
        </tr>
      </thead>
      <tbody id="roceOpticBody"></tbody>
    </table>
  </div>

  <div class="section" id="roceConnSection" style="display:none;">
    <h3>Connections (<span id="roceConnCount">0</span>)</h3>
    <div style="overflow-x:auto; max-height:600px; overflow-y:auto;">
      <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
        <thead>
          <tr style="background:#DAE5FF; position:sticky; top:0;">
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Port</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Conn</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Interface</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">This Optic</th>
            <th style="text-align:center; padding:5px 8px; border:1px solid #CDCED6; color:#747283;">→</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Remote Loc</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Remote Port</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Remote Optic</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Status</th>
            <th style="text-align:left; padding:5px 8px; border:1px solid #CDCED6;">Sheet</th>
          </tr>
        </thead>
        <tbody id="roceConnBody"></tbody>
      </table>
    </div>
  </div>

  </div><!-- end page-roce -->

<script>
  function appPath(path) {
    const match = window.location.pathname.match(/^([/]canvas-apps[/][^/]+)/);
    const base = match ? match[1] : '';
    if (!path) return base || '/';
    if (/^[a-z]+:\/\//i.test(path)) return path;
    const normalized = path.startsWith('/') ? path : '/' + path;
    if (base && (normalized === base || normalized.startsWith(base + '/'))) return normalized;
    return base ? base + normalized : normalized;
  }

  const apiPath = appPath;

  function showPage(name, el) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    el.classList.add('active');
  }

  // --- Output renderers ---

  function _makeTable(headers, rows, rightAlign) {
    const table = document.createElement('table');
    table.style.cssText = 'border-collapse:collapse; font-size:0.85rem; margin-bottom:12px;';
    const thead = document.createElement('thead');
    const hrow = document.createElement('tr');
    hrow.style.background = '#DAE5FF';
    headers.forEach((h, i) => {
      const th = document.createElement('th');
      th.style.cssText = 'padding:5px 10px; border:1px solid #CDCED6; text-align:' + (rightAlign[i] ? 'right' : 'left') + '; white-space:nowrap;';
      th.textContent = h;
      hrow.appendChild(th);
    });
    thead.appendChild(hrow);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    rows.forEach((row, ri) => {
      const tr = document.createElement('tr');
      if (ri % 2 === 1) tr.style.background = '#FAFAFA';
      row.forEach((val, i) => {
        const td = document.createElement('td');
        td.style.cssText = 'padding:4px 10px; border:1px solid #CDCED6; text-align:' + (rightAlign[i] ? 'right' : 'left') + ';';
        td.textContent = val || '';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  // Parse and render the side-by-side "In Service | Not In Service" block
  function _renderSideBySide(text, container) {
    const lines = text.split('\\n');
    const blocks = [];
    let cur = null;

    for (const raw of lines) {
      const line = raw.trimEnd();
      if (!line.trim()) continue;
      if (/^[-]{5,}\+[-]{5,}/.test(line.trim())) continue; // separator

      const grandMatch = line.match(/Total\s*\(In\s*\+\s*Not In Service\):\s*(\d+)/);
      if (grandMatch) {
        if (cur) cur.grandTotal = +grandMatch[1];
        continue;
      }

      if (line.includes(' | ')) {
        const parts = line.split(' | ');
        if (parts.length >= 2 && parts[0].toLowerCase().includes('service')) {
          if (cur) cur.header = [parts[0].trim(), parts[1].trim()];
          continue;
        }
        const lm = parts[0].trim().match(/^(.+):\s*(\d+)\s*$/);
        const rm = (parts[1] || '').trim().match(/^(.+):\s*(\d+)\s*$/);
        if (lm && rm && cur) {
          const name = lm[1].trimEnd();
          if (name.trim() === 'Total') { cur.totalIn = +lm[2]; cur.totalNotIn = +rm[2]; }
          else cur.rows.push({name: name.trim(), inSvc: +lm[2], notIn: +rm[2]});
        }
        continue;
      }

      // Title line — flush previous block, start new
      if (cur && cur.rows.length) blocks.push(cur);
      cur = {title: line.trim(), header: ['In Service', 'Not In Service'], rows: [], totalIn: 0, totalNotIn: 0, grandTotal: 0};
    }
    if (cur && cur.rows.length) blocks.push(cur);

    blocks.forEach(block => {
      const h4 = document.createElement('h4');
      h4.style.cssText = 'margin:0 0 6px 0; font-size:0.9rem; color:#1a1a2e;';
      h4.textContent = block.title;
      container.appendChild(h4);
      container.appendChild(_makeTable(
        ['Type', block.header[0], block.header[1]],
        block.rows.map(r => [r.name, r.inSvc.toLocaleString(), r.notIn.toLocaleString()]),
        [false, true, true]
      ));
      if (block.totalIn || block.totalNotIn) {
        const tot = document.createElement('div');
        tot.style.cssText = 'font-size:0.85rem; margin:-4px 0 14px; padding:5px 10px; background:#F5F7FF; border:1px solid #CDCED6; border-radius:0 0 4px 4px;';
        const grand = block.grandTotal || (block.totalIn + block.totalNotIn);
        tot.innerHTML = '<strong>In Service:</strong> ' + block.totalIn.toLocaleString()
          + ' &nbsp;|&nbsp; <strong>Not In Service:</strong> ' + block.totalNotIn.toLocaleString()
          + ' &nbsp;|&nbsp; <strong>Grand Total:</strong> ' + grand.toLocaleString();
        container.appendChild(tot);
      }
    });
  }

  // Parse and render count output (both regular and by-status)
  function _renderCountOutput(text) {
    const container = document.getElementById('countOut');
    container.innerHTML = '';
    if (!text || !text.trim()) return;

    const parts = text.split(/\\n\\n={10,}\\n\\n/);
    parts.forEach((part, pi) => {
      if (!part.trim()) return;
      const wrap = document.createElement('div');
      if (pi > 0) { const hr = document.createElement('hr'); hr.style.cssText = 'border:none; border-top:1px solid #CDCED6; margin:12px 0;'; container.appendChild(hr); }

      // Parse lines into sections.  A section is an optional title header
      // followed by optic/device rows and summary totals.  IB and RoCE files
      // use the simple "Name: count" format (no A/Z breakdown); cutsheets use
      // the richer "Name: total  (A: x, Z: y)" format.  Both are handled here.
      const sections = [];
      let pendingTitle = null;
      let opticRows = [], summaryLines = [], noteLines = [], inNote = false;

      function _flushSection() {
        if (opticRows.length || summaryLines.length) {
          sections.push({title: pendingTitle, opticRows: opticRows.slice(), summaryLines: summaryLines.slice(), noteLines: noteLines.slice()});
        }
        pendingTitle = null; opticRows = []; summaryLines = []; noteLines = []; inNote = false;
      }

      for (const line of part.split('\\n')) {
        if (!line.trim()) continue;
        if (/^[-=]{5,}$/.test(line.trim())) continue; // separator lines

        // "Name: total  (A: x, Z: y)" — cutsheet preprocessor format
        const om = line.match(/^(.+?):\\s*(\\d+)\\s+\\(A:\\s*(\\d+),\\s*Z:\\s*(\\d+)\\)/);
        if (om) { opticRows.push({optic: om[1].trim(), total: +om[2], a: +om[3], z: +om[4]}); inNote = false; continue; }

        // "Total A-side optics: 150" / "Grand total optics: 300"
        const tm = line.match(/^(Total [^:]+|Grand total [^:]+):\\s*(.+)$/);
        if (tm) { summaryLines.push({label: tm[1].trim(), value: tm[2].trim()}); continue; }

        // "Total: 150" — simple total from create_count_string (IB/RoCE)
        const stm = line.match(/^Total:\\s*([\\d,]+)$/);
        if (stm) { summaryLines.push({label: 'Total', value: stm[1]}); continue; }

        if (line.startsWith('Note:') || inNote) { inNote = true; noteLines.push(line); continue; }

        // "IB NODE OPTIC: 144" — simple Name: count (IB/RoCE fallback path)
        const sm = line.match(/^([A-Za-z0-9][^:]+?):\\s*(\\d+)$/);
        if (sm) { opticRows.push({optic: sm[1].trim(), total: +sm[2], a: null, z: null}); inNote = false; continue; }

        // Title/header line (no colon) — flush current section and start a new one
        if (!line.includes(':')) { _flushSection(); pendingTitle = line.trim(); }
      }
      _flushSection();

      const hasParsedRows = sections.some(s => s.opticRows.length > 0);

      if (hasParsedRows) {
        sections.forEach((sec, si) => {
          if (!sec.opticRows.length && !sec.summaryLines.length) return;
          if (si > 0) { const hr2 = document.createElement('hr'); hr2.style.cssText = 'border:none; border-top:1px solid #e8e8e8; margin:8px 0;'; wrap.appendChild(hr2); }

          if (sec.title) {
            const h = document.createElement('div');
            h.style.cssText = 'font-weight:bold; font-size:0.88rem; color:#343338; margin:0 0 6px 0;';
            h.textContent = sec.title;
            wrap.appendChild(h);
          }

          if (sec.opticRows.length) {
            const hasAZ = sec.opticRows.some(r => r.a !== null);
            wrap.appendChild(_makeTable(
              hasAZ ? ['Optic Type', 'Total', 'A-Side', 'Z-Side'] : ['Optic / Device Type', 'Count'],
              sec.opticRows.map(r => hasAZ
                ? [r.optic, r.total.toLocaleString(), r.a.toLocaleString(), r.z.toLocaleString()]
                : [r.optic, r.total.toLocaleString()]
              ),
              hasAZ ? [false, true, true, true] : [false, true]
            ));
          }

          if (sec.summaryLines.length) {
            const sd = document.createElement('div');
            sd.style.cssText = 'margin:0 0 10px; font-size:0.85rem;';
            sec.summaryLines.forEach(({label, value}) => {
              const row = document.createElement('div');
              row.style.cssText = 'display:flex; gap:12px; padding:2px 0; border-bottom:1px solid #f0f0f0;';
              const l = document.createElement('span'); l.style.cssText = 'color:#747283; min-width:190px;'; l.textContent = label + ':';
              const v = document.createElement('span'); v.style.fontWeight = 'bold'; v.textContent = (+String(value).replace(/,/g,'')).toLocaleString();
              row.appendChild(l); row.appendChild(v); sd.appendChild(row);
            });
            wrap.appendChild(sd);
          }

          if (sec.noteLines.length) {
            const nd = document.createElement('div');
            nd.style.cssText = 'background:#F5F7FF; border:1px solid #CDCED6; border-radius:4px; padding:8px 12px; font-size:0.82rem; color:#555; margin-top:8px;';
            nd.textContent = sec.noteLines.join(' ').replace(/\\s+/g, ' ');
            wrap.appendChild(nd);
          }
        });
      } else if (part.includes(' | ')) {
        _renderSideBySide(part, wrap);
      } else {
        const pre = document.createElement('div');
        pre.style.cssText = 'font-size:0.85rem; white-space:pre-wrap;';
        pre.textContent = part;
        wrap.appendChild(pre);
      }
      container.appendChild(wrap);
    });
  }

  // Accumulation buffer + renderer for NetBox SSE stream
  let _netboxTextBuffer = '';

  function _renderNetboxOutput(text) {
    const container = document.getElementById('netboxOut');
    container.innerHTML = '';
    if (!text.trim()) return;
    const lines = text.split('\\n');
    const statusLines = [], sections = [];
    let cur = null;

    for (const line of lines) {
      if (!line.trim()) continue;
      const secM = line.match(/^===\\s*(.+?)\\s*===\\s*$/);
      if (secM) { if (cur) sections.push(cur); cur = {header: secM[1], rows: []}; continue; }
      if (/^={10,}$/.test(line.trim())) { if (cur) sections.push(cur); cur = null; continue; }
      if (cur && /^\\s+\\S/.test(line)) {
        const rm = line.match(/^\\s+(.+?):\\s*(\\d[\\d,]*)\\s*$/);
        if (rm) { cur.rows.push({label: rm[1].trim(), count: +rm[2].replace(/,/g,'')}); continue; }
      }
      const subM = line.match(/^---\\s*(.+?)\\s*---\\s*$/);
      if (subM) { if (cur) sections.push(cur); cur = {header: subM[1], rows: [], sub: true}; continue; }
      if (!cur) statusLines.push(line);
    }
    if (cur && cur.rows.length) sections.push(cur);

    if (statusLines.some(l => l.trim())) {
      const log = document.createElement('div');
      log.style.cssText = 'font-size:0.82rem; color:#555; margin-bottom:12px; padding:8px 12px; background:#F5F7FF; border:1px solid #CDCED6; border-radius:4px; line-height:1.7;';
      statusLines.forEach(l => { if (!l.trim()) return; const p = document.createElement('p'); p.style.margin = '0'; p.textContent = l; log.appendChild(p); });
      container.appendChild(log);
    }
    sections.forEach(sec => {
      const sd = document.createElement('div');
      sd.style.cssText = 'margin-bottom:14px;';
      const h4 = document.createElement('h4');
      h4.style.cssText = 'margin:0 0 5px 0; font-size:' + (sec.sub ? '0.85rem' : '0.9rem') + '; color:#1a1a2e; font-weight:bold;';
      h4.textContent = sec.header;
      sd.appendChild(h4);
      if (sec.rows.length) sd.appendChild(_makeTable(['Type / Model', 'Count'], sec.rows.map(r => [r.label, r.count.toLocaleString()]), [false, true]));
      container.appendChild(sd);
    });
  }

  // --- Elapsed timer helper ---
  let _timerInterval = null;
  function _startTimer() {
    const el = document.getElementById('countStatus');
    const t0 = Date.now();
    el.textContent = 'Processing... 0s';
    _timerInterval = setInterval(() => {
      el.textContent = 'Processing... ' + Math.round((Date.now() - t0) / 1000) + 's';
    }, 1000);
  }
  function _stopTimer() {
    clearInterval(_timerInterval);
    _timerInterval = null;
    document.getElementById('countStatus').textContent = '';
  }

  // --- Sheet count ---
  function _buildCountForm() {
    const fileInput = document.getElementById('sheetFile');
    const form = new FormData();
    for (let i = 0; i < fileInput.files.length; i++) form.append('file', fileInput.files[i]);
    const sc = (document.getElementById('siteCode') || {}).value;
    if (sc && sc.trim()) form.append('site_code', sc.trim());
    return form;
  }

  async function uploadCount() {
    const fileInput = document.getElementById('sheetFile');
    if (!fileInput.files.length) { alert('Select one or more files first.'); return; }
    _startTimer();
    try {
      const form = _buildCountForm();
      const res = await fetch(apiPath('/api/upload-count'), {
        method: 'POST',
        body: form
      });
      let data;
      try { data = await res.json(); } catch (_) { data = {error: 'Server returned non-JSON (status ' + res.status + ')'}; }
      if (!res.ok) {
        document.getElementById('countOut').textContent = 'Error: ' + (data.error || 'Unknown error');
      } else {
        _renderCountOutput(data.output);
        if (data.pg_loaded === 'pending' && data.pg_message) {
          const info = document.createElement('div');
          info.style.cssText = 'background:#E8F0FE; border:1px solid #2741E7; border-radius:4px; padding:10px; margin-bottom:10px; color:#1a1a2e; font-size:0.9rem;';
          info.textContent = data.pg_message;
          const countOut = document.getElementById('countOut');
          countOut.parentNode.insertBefore(info, countOut);
        }
        if (data.pg_loaded === 'failed') {
          const banner = document.createElement('div');
          banner.style.cssText = 'background:#FFFACD; border:1px solid #FFD700; border-radius:4px; padding:10px; margin-bottom:10px; color:#333;';
          banner.textContent = 'Warning: Cutsheet counted but database load failed: ' + (data.pg_error || 'Unknown error') + '. Queries will use in-memory context only.';
          const countOut = document.getElementById('countOut');
          countOut.parentNode.insertBefore(banner, countOut);
        }
      }
    } catch (err) {
      document.getElementById('countOut').textContent = 'Error: ' + err.message;
    } finally {
      _stopTimer();
    }
  }

  async function countByStatus() {
    const fileInput = document.getElementById('sheetFile');
    if (!fileInput.files.length) { alert('Select one or more files first.'); return; }
    _startTimer();
    try {
      const form = _buildCountForm();
      form.append('include_by_status', '1');
      const res = await fetch(apiPath('/api/upload-count'), {
        method: 'POST',
        body: form
      });
      let data;
      try { data = await res.json(); } catch (_) { data = {error: 'Server returned non-JSON (status ' + res.status + ')'}; }
      if (!res.ok) {
        document.getElementById('countOut').textContent = 'Error: ' + (data.error || 'Upload failed');
        return;
      }
      _renderCountOutput(data.output);
      if (data.pg_loaded === 'pending' && data.pg_message) {
        const info = document.createElement('div');
        info.style.cssText = 'background:#E8F0FE; border:1px solid #2741E7; border-radius:4px; padding:10px; margin-bottom:10px; color:#1a1a2e; font-size:0.9rem;';
        info.textContent = data.pg_message;
        const countOut = document.getElementById('countOut');
        countOut.parentNode.insertBefore(info, countOut);
      }
      if (data.pg_loaded === 'failed') {
        const banner = document.createElement('div');
        banner.style.cssText = 'background:#FFFACD; border:1px solid #FFD700; border-radius:4px; padding:10px; margin-bottom:10px; color:#333;';
        banner.textContent = 'Warning: Cutsheet counted but database load failed: ' + (data.pg_error || 'Unknown error') + '. Queries will use in-memory context only.';
        const countOut = document.getElementById('countOut');
        countOut.parentNode.insertBefore(banner, countOut);
      }
    } catch (err) {
      document.getElementById('countOut').textContent = 'Error: ' + err.message;
    } finally {
      _stopTimer();
    }
  }

  // --- NetBox SSE ---
  function _startNetboxSSE(url) {
    const spinner = document.getElementById('netboxSpinner');
    const statusText = document.getElementById('netboxStatusText');
    _netboxTextBuffer = '';
    document.getElementById('netboxOut').innerHTML = '';
    spinner.classList.add('active');
    statusText.textContent = 'Querying...';

    const es = new EventSource(url);
    es.onmessage = (e) => {
      if (e.data === '[DONE]') {
        es.close();
        spinner.classList.remove('active');
        statusText.textContent = 'Done.';
        return;
      }
      const msg = JSON.parse(e.data);
      if (msg && typeof msg === 'object' && msg._type === 'csv_ready') {
        msg.files.forEach(f => {
          const blob = new Blob([f.content], {type: 'text/csv'});
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = f.name;
          a.click();
          URL.revokeObjectURL(url);
        });
        statusText.textContent = 'Done. ' + msg.files.length + ' CSV file(s) downloaded.';
        return;
      }
      _netboxTextBuffer += msg;
      _renderNetboxOutput(_netboxTextBuffer);
      const nb = document.getElementById('netboxOut');
      nb.scrollTop = nb.scrollHeight;
    };
    es.onerror = () => {
      es.close();
      spinner.classList.remove('active');
      statusText.textContent = 'Connection error.';
    };
  }

  function streamNetbox() {
    const site = document.getElementById('netboxSite').value.trim() || 'us-west-09a';
    const activeOnly = document.getElementById('netboxActiveOnly').checked ? 'true' : 'false';
    const opticLoc = document.getElementById('netboxOpticLocations').checked ? 'true' : 'false';
    _startNetboxSSE(apiPath('/api/stream/netbox?site=' + encodeURIComponent(site) + '&active_only=' + activeOnly + '&include_optic_locations=' + opticLoc));
  }

  function streamAllSites() {
    const activeOnly = document.getElementById('netboxActiveOnly').checked ? 'true' : 'false';
    const opticLoc = document.getElementById('netboxOpticLocations').checked ? 'true' : 'false';
    _startNetboxSSE(apiPath('/api/stream/all-sites?active_only=' + activeOnly + '&include_optic_locations=' + opticLoc));
  }

  // --- Rack Analyzer ---
  let _bsLastResult = null;

  function clearBuildSheetFiles() {
    document.getElementById('bsCutsheet').value = '';
    document.getElementById('bsTemplate').value = '';
    document.getElementById('bsStatus').textContent = 'Files cleared.';
  }

  function _buildLabelRows(cables) {
    const rows = [];
    cables.forEach(c => {
      const aLabel = ((c.a_loc || '') + ' ' + (c.a_port || '')).trim();
      const zLabel = ((c.z_loc || '') + ' ' + (c.z_port || '')).trim();
      rows.push([aLabel + '\\n' + zLabel, zLabel + '\\n' + aLabel]);
    });
    return rows;
  }

  function _rowsToCsv(rows) {
    return rows.map(r => r.map(cell => '"' + cell.replace(/"/g, '""') + '"').join(',')).join('\\r\\n');
  }

  function _triggerDownload(csv, filename) {
    const blob = new Blob([csv], {type: 'text/csv'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function downloadLayout() {
    if (!_bsLastResult) return;
    const cutFile = document.getElementById('bsCutsheet').files[0];
    if (!cutFile) { alert('Cutsheet file is no longer selected — please re-select it.'); return; }

    const btn = document.getElementById('bsLayoutBtn');
    const status = document.getElementById('bsLayoutStatus');
    btn.disabled = true;
    status.textContent = 'Generating layout...';

    const form = new FormData();
    form.append('cutsheet', cutFile);
    const tplFile = document.getElementById('bsTemplate').files[0];
    if (tplFile) form.append('template', tplFile);
    form.append('room', _bsLastResult.room);

    try {
      const res = await fetch(apiPath('/api/buildsheet/layout'), { method: 'POST', body: form });
      if (!res.ok) {
        const json = await res.json().catch(() => ({}));
        status.textContent = 'Error: ' + (json.error || res.statusText);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'layout_' + _bsLastResult.room.toUpperCase() + '.xlsx';
      a.click();
      URL.revokeObjectURL(url);
      status.textContent = 'Done.';
    } catch(e) {
      status.textContent = 'Request failed: ' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  async function downloadDHLabels() {
    if (!_bsLastResult) return;
    const cutFile = document.getElementById('bsCutsheet').files[0];
    if (!cutFile) { alert('Cutsheet file is no longer selected — please re-select it.'); return; }

    const btn = document.getElementById('bsDHBtn');
    const status = document.getElementById('bsDHStatus');
    btn.disabled = true;
    status.textContent = 'Processing full DH — this may take a moment...';

    const form = new FormData();
    form.append('cutsheet', cutFile);
    form.append('room', _bsLastResult.room);

    try {
      const res = await fetch(apiPath('/api/buildsheet/dh'), { method: 'POST', body: form });
      const json = await res.json();
      if (!res.ok) { status.textContent = 'Error: ' + (json.error || 'Unknown'); return; }
      const d = json.data;
      const internalRows = [['Internal labels', ''], ..._buildLabelRows(d.internal_cables)];
      const cabRows      = [['Cab to Cab Labels', ''], ..._buildLabelRows(d.cab_to_cab_cables)];
      const csv = _rowsToCsv([...internalRows, ['', ''], ...cabRows]);
      _triggerDownload(csv, 'all_labels_' + d.room.toUpperCase() + '.csv');
      status.textContent = 'Done.';
    } catch(e) {
      status.textContent = 'Request failed: ' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  function downloadAllLabels() {
    if (!_bsLastResult) return;
    const d = _bsLastResult;
    const internalRows = [['Internal labels', ''], ..._buildLabelRows(d.internal_cables)];
    const cabRows     = [['Cab to Cab Labels', ''], ..._buildLabelRows(d.cab_to_cab_cables)];
    const csv = _rowsToCsv([...internalRows, ['', ''], ...cabRows]);
    _triggerDownload(csv, 'all_labels_' + d.room + '_rack' + d.rack + '.csv');
  }

  function _rackFromLoc(loc) {
    // Extract rack portion from 'dh202:041:10' → 'dh202:041'
    const parts = (loc || '').split(':');
    return parts.length >= 2 ? parts[0] + ':' + parts[1] : loc;
  }

  function _fmtPort(loc, port) {
    // Strip any existing leading 'port ' from the field to avoid 'port port X'
    const p = (port || '').replace(/^port\\s+/i, '');
    return ((loc || '') + ' port ' + p).trim();
  }

function downloadCableMapCsv(type) {
    if (!_bsLastResult) return;
    const isInternal = type === 'internal';
    const cables = isInternal ? _bsLastResult.internal_cables : _bsLastResult.cab_to_cab_cables;
    const headers = isInternal
      ? ['Source', 'Destination', 'Cable Type', 'Cable Length']
      : ['Source', 'Destination', 'Cable Type', 'Cable Length', 'Cable Bundle'];

    // Build bundle map for cab-to-cab: (a_rack|z_rack) → letter
    const bundleMap = {};
    let letterCode = 65; // 'A'
    if (!isInternal) {
      cables.forEach(c => {
        const key = _rackFromLoc(c.a_loc) + '|' + _rackFromLoc(c.z_loc);
        if (!(key in bundleMap)) {
          bundleMap[key] = _bsLastResult.rack + ':' + String.fromCharCode(letterCode++);
        }
      });
    }

    const sortedCables = isInternal ? cables : [...cables].sort((a, b) => {
      const ka = bundleMap[_rackFromLoc(a.a_loc) + '|' + _rackFromLoc(a.z_loc)] || '';
      const kb = bundleMap[_rackFromLoc(b.a_loc) + '|' + _rackFromLoc(b.z_loc)] || '';
      return ka < kb ? -1 : ka > kb ? 1 : 0;
    });

    const rows = [headers];
    sortedCables.forEach(c => {
      const src = _fmtPort(c.a_loc, c.a_port);
      const dst = _fmtPort(c.z_loc, c.z_port);
      const row = [src, dst, c.cable_type || '', ''];
      if (!isInternal) {
        const key = _rackFromLoc(c.a_loc) + '|' + _rackFromLoc(c.z_loc);
        row.push(bundleMap[key] || '');
      }
      rows.push(row);
    });
    const prefix = isInternal ? 'internal_cables' : 'cab_to_cab_cables';
    _triggerDownload(_rowsToCsv(rows), prefix + '_' + _bsLastResult.room + '_rack' + _bsLastResult.rack + '.csv');
  }

  function downloadOpticSummary() {
    if (!_bsLastResult) return;
    const d = _bsLastResult;
    const rows = [['Optic Type', 'Count']];
    Object.entries(d.optic_summary || {}).forEach(([optic, count]) => rows.push([optic, count]));
    rows.push(['', '']);
    (d.optic_locations || []).forEach(item => rows.push([item.location, item.port, item.optic]));
    _triggerDownload(_rowsToCsv(rows), 'optic_summary_' + d.room + '_rack' + d.rack + '.csv');
  }

  function downloadLabels(type) {
    if (!_bsLastResult) return;
    const isInternal = type === 'internal';
    const cables = isInternal ? _bsLastResult.internal_cables : _bsLastResult.cab_to_cab_cables;
    const title = isInternal ? 'Internal labels' : 'Cab to Cab Labels';
    const rows = [[title, ''], ..._buildLabelRows(cables)];
    const prefix = isInternal ? 'internal_labels' : 'cab_to_cab_labels';
    _triggerDownload(_rowsToCsv(rows), prefix + '_' + _bsLastResult.room + '_rack' + _bsLastResult.rack + '.csv');
  }

  async function runBuildSheet() {
    const cutFile = document.getElementById('bsCutsheet').files[0];
    const tplFile = document.getElementById('bsTemplate').files[0];
    const room = document.getElementById('bsRoom').value.trim();
    const rack = document.getElementById('bsRack').value.trim();

    if (!cutFile) { alert('Select the Cutsheet Master file.'); return; }
    if (!room) { alert('Enter a room designator (e.g. DH2).'); return; }
    if (!rack) { alert('Enter a rack number (e.g. 121).'); return; }

    const btn = document.getElementById('bsBtn');
    btn.disabled = true;
    document.getElementById('bsStatus').textContent = 'Processing — this may take a moment for large cutsheets...';
    ['bsDHSection','bsSummarySection','bsElevationSection','bsDevicesSection','bsOpticSection','bsInternalSection','bsCabSection']
      .forEach(id => document.getElementById(id).style.display = 'none');

    const form = new FormData();
    form.append('cutsheet', cutFile);
    if (tplFile) form.append('template', tplFile);
    form.append('room', room);
    form.append('rack', rack);

    try {
      const res = await fetch(apiPath('/api/buildsheet'), {
        method: 'POST',
        body: form
      });
      const json = await res.json();
      if (!res.ok) {
        document.getElementById('bsStatus').textContent = 'Error: ' + (json.error || 'Unknown error');
        return;
      }
      _bsLastResult = json.data;
      _renderBuildSheet(json.data);
      document.getElementById('bsStatus').textContent = 'Done.';
    } catch(e) {
      document.getElementById('bsStatus').textContent = 'Request failed: ' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  function _renderBuildSheet(d) {
    // DH section — dynamic button label
    document.getElementById('bsDHBtn').textContent = 'Download all ' + d.room.toUpperCase() + ' labels';
    document.getElementById('bsDHStatus').textContent = '';
    document.getElementById('bsDHSection').style.display = '';

    // Summary
    document.getElementById('bsSummary').textContent =
      'Room: ' + d.room + '   Rack: ' + d.rack + '\\n' +
      'Total cables touching this rack: ' + d.total_cables +
      '   Staying inside rack: ' + d.internal_count +
      '   Leaving rack: ' + d.cab_to_cab_count;
    const cabTypeEl = document.getElementById('bsCabType');
    cabTypeEl.textContent = d.cab_type ? 'Cab Type: ' + d.cab_type : '';
    cabTypeEl.style.fontWeight = 'bold';

    // Cab type summary table
    const cabSummary = d.cab_type_summary || {};
    const cabSummaryEntries = Object.entries(cabSummary).filter(([, v]) => v > 0);
    const cabSummaryWrap = document.getElementById('bsCabTypeSummaryWrap');
    if (cabSummaryEntries.length) {
      const tbody = document.getElementById('bsCabTypeSummaryBody');
      tbody.innerHTML = '';
      cabSummaryEntries.forEach(([type, count]) => {
        const tr = document.createElement('tr');
        const td1 = document.createElement('td');
        td1.style.cssText = 'padding:3px 10px; border:1px solid #CDCED6;';
        td1.textContent = type;
        const td2 = document.createElement('td');
        td2.style.cssText = 'padding:3px 10px; border:1px solid #CDCED6; text-align:right; font-weight:bold;';
        td2.textContent = count;
        tr.appendChild(td1); tr.appendChild(td2);
        tbody.appendChild(tr);
      });
      cabSummaryWrap.style.display = '';
    } else {
      cabSummaryWrap.style.display = 'none';
    }

    document.getElementById('bsSummarySection').style.display = '';

    // Elevation
    if (d.cab_type && d.elevation && d.elevation.length) {
      document.getElementById('bsCabTypeLabel').textContent = d.cab_type;

      // Build set of rack:ru pairs that appear in the cable data
      const cableRuSet = new Set();
      [...(d.internal_cables || []), ...(d.cab_to_cab_cables || [])].forEach(c => {
        [c.a_loc, c.z_loc].forEach(loc => {
          if (!loc) return;
          const parts = loc.split(':');
          if (parts.length >= 3) {
            cableRuSet.add((parts[1].replace(/^0+/, '') || '0') + ':' + parts[2]);
          }
        });
      });
      const queriedRack = (d.rack || '').replace(/^0+/, '') || '0';

      const elevBody = document.getElementById('bsElevationBody');
      elevBody.innerHTML = '';
      d.elevation.forEach(item => {
        const tr = document.createElement('tr');
        [item.ru, item.device_name, item.device_type].forEach(val => {
          const td = document.createElement('td');
          td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;';
          td.textContent = val || '';
          tr.appendChild(td);
        });
        const found = item.ru && cableRuSet.has(queriedRack + ':' + item.ru);
        const tdFound = document.createElement('td');
        tdFound.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6; font-weight:bold;';
        tdFound.textContent = item.ru ? (found ? 'YES' : 'NO') : '';
        tdFound.style.color = found ? 'green' : (item.ru ? 'red' : '');
        tr.appendChild(tdFound);
        elevBody.appendChild(tr);
      });
      document.getElementById('bsElevationSection').style.display = '';
    } else {
      document.getElementById('bsElevationSection').style.display = 'none';
    }

    // Devices
    const devBody = document.getElementById('bsDevicesBody');
    devBody.innerHTML = '';
    (d.devices || []).forEach(dev => {
      const tr = document.createElement('tr');
      ['ru','location','dns_name','model','status'].forEach(f => {
        const td = document.createElement('td');
        td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;';
        td.textContent = dev[f] || '';
        if (f === 'status') {
          if (dev[f] === 'Pending')   td.style.background = '#DAE5FF';
          if (dev[f] === 'Installed') td.style.background = '#d4edda';
        }
        tr.appendChild(td);
      });
      devBody.appendChild(tr);
    });
    document.getElementById('bsDevicesSection').style.display = '';

    // Optics — summary counts
    const opticBody = document.getElementById('bsOpticBody');
    opticBody.innerHTML = '';
    Object.entries(d.optic_summary || {}).forEach(([optic, count]) => {
      const tr = document.createElement('tr');
      const td1 = document.createElement('td');
      td1.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;';
      td1.textContent = optic;
      const td2 = document.createElement('td');
      td2.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6; text-align:right; font-weight:bold;';
      td2.textContent = count;
      tr.appendChild(td1); tr.appendChild(td2);
      opticBody.appendChild(tr);
    });

    // Optics — per-port locations
    const opticLocBody = document.getElementById('bsOpticLocBody');
    opticLocBody.innerHTML = '';
    (d.optic_locations || []).forEach(item => {
      const tr = document.createElement('tr');
      [item.location, item.port, item.optic].forEach(val => {
        const td = document.createElement('td');
        td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;';
        td.textContent = val || '';
        tr.appendChild(td);
      });
      opticLocBody.appendChild(tr);
    });
    document.getElementById('bsOpticSection').style.display = '';

    // Internal cables
    document.getElementById('bsInternalCount').textContent = d.internal_count;
    const internalBody = document.getElementById('bsInternalBody');
    internalBody.innerHTML = '';
    if ((d.internal_cables || []).length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 7;
      td.style.cssText = 'padding:8px; color:#747283; text-align:center; border:1px solid #CDCED6;';
      td.textContent = '(none)';
      tr.appendChild(td); internalBody.appendChild(tr);
    } else {
      (d.internal_cables || []).forEach(c => {
        const tr = document.createElement('tr');
        [c.a_loc, c.a_port, '→', c.z_loc, c.z_port, c.cable_type, c.status].forEach((val, i) => {
          const td = document.createElement('td');
          td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;' + (i === 2 ? ' text-align:center; color:#aaa;' : '');
          td.textContent = val || '';
          if (i === 6 && (val || '').toLowerCase().startsWith('cable not run')) td.style.background = '#DAE5FF';
          tr.appendChild(td);
        });
        internalBody.appendChild(tr);
      });
    }
    document.getElementById('bsInternalSection').style.display = '';

    // Cab-to-cab cables
    document.getElementById('bsCabCount').textContent = d.cab_to_cab_count;
    const cabBody = document.getElementById('bsCabBody');
    cabBody.innerHTML = '';
    if ((d.cab_to_cab_cables || []).length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 8;
      td.style.cssText = 'padding:8px; color:#747283; text-align:center; border:1px solid #CDCED6;';
      td.textContent = '(none)';
      tr.appendChild(td); cabBody.appendChild(tr);
    } else {
      function _rackNum(loc) {
        const parts = (loc || '').split(':');
        return parts.length >= 2 ? (parts[1].replace(/^0+/, '') || parts[1]) : '';
      }
      // Sort by remote rack number numerically so bundles group together
      const sortedCab = [...(d.cab_to_cab_cables || [])].sort((a, b) => {
        const sa = a.rack_side || 'a', sb = b.rack_side || 'a';
        const ra = parseInt(_rackNum(sa === 'a' ? a.z_loc : a.a_loc)) || 0;
        const rb = parseInt(_rackNum(sb === 'a' ? b.z_loc : b.a_loc)) || 0;
        return ra - rb;
      });
      const bundleColors = ['#ffffff', '#F5F7FF'];
      let lastBundle = null, colorIdx = 0;
      sortedCab.forEach(c => {
        const side = c.rack_side || 'a';
        const thisLoc  = side === 'a' ? c.a_loc  : c.z_loc;
        const thisPort = side === 'a' ? c.a_port : c.z_port;
        const remLoc   = side === 'a' ? c.z_loc  : c.a_loc;
        const remPort  = side === 'a' ? c.z_port : c.a_port;
        const remRack = _rackNum(remLoc) || 'Unknown';
        const bundle = 'Bundle: ' + _rackNum(thisLoc) + ' to ' + remRack;
        if (bundle !== lastBundle) { colorIdx = (colorIdx + 1) % 2; lastBundle = bundle; }
        const rowBg = bundleColors[colorIdx];
        const tr = document.createElement('tr');
        const MISSING = 'Cutsheet Data Missing';
        [bundle, thisLoc, thisPort, '→', remLoc || MISSING, remPort || MISSING, c.cable_type, c.status].forEach((val, i) => {
          const td = document.createElement('td');
          td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6; background:' + rowBg + ';'
            + (i === 0 ? ' font-weight:bold; white-space:nowrap;' : '')
            + (i === 3 ? ' text-align:center; color:#aaa;' : '')
            + ([4, 5].includes(i) && val === MISSING ? ' color:#cc7a00; font-style:italic;' : '');
          td.textContent = val || '';
          if (i === 7 && (val || '').toLowerCase().startsWith('cable not run')) td.style.background = '#DAE5FF';
          tr.appendChild(td);
        });
        cabBody.appendChild(tr);
      });
    }
    document.getElementById('bsCabSection').style.display = '';
  }

  // --- IB Analyzer ---

  async function runIBQuery() {
    const file = document.getElementById('ibFile').files[0];
    const device = document.getElementById('ibDevice').value.trim();
    if (!file)   { alert('Select the IB Build Sheet file.'); return; }
    if (!device) { alert('Enter a device name (e.g. S2.1.1).'); return; }

    const btn = document.getElementById('ibBtn');
    btn.disabled = true;
    document.getElementById('ibStatus').textContent = 'Searching across all sheets...';
    ['ibOpticSection', 'ibConnSection'].forEach(id => document.getElementById(id).style.display = 'none');

    const form = new FormData();
    form.append('file', file);
    form.append('device', device);

    try {
      const res = await fetch(apiPath('/api/ib-query'), { method: 'POST', body: form });
      const json = await res.json();
      if (!res.ok) {
        document.getElementById('ibStatus').textContent = 'Error: ' + (json.error || 'Unknown error');
        return;
      }
      _renderIBResult(json.data);
      document.getElementById('ibStatus').textContent =
        'Done — ' + json.data.total + ' connection' + (json.data.total === 1 ? '' : 's') + ' found.';
    } catch(e) {
      document.getElementById('ibStatus').textContent = 'Request failed: ' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  function _renderIBResult(d) {
    document.getElementById('ibDeviceLabel').textContent = d.device;

    // Optic summary
    const opticBody = document.getElementById('ibOpticBody');
    opticBody.innerHTML = '';
    Object.entries(d.optic_summary || {}).forEach(([type, count]) => {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td style="padding:4px 8px; border:1px solid #CDCED6;">' + type + '</td>' +
        '<td style="padding:4px 8px; border:1px solid #CDCED6; text-align:right; font-weight:bold;">' + count + '</td>';
      opticBody.appendChild(tr);
    });
    document.getElementById('ibOpticSection').style.display = '';

    // Connections
    document.getElementById('ibConnCount').textContent = d.total;
    const connBody = document.getElementById('ibConnBody');
    connBody.innerHTML = '';
    (d.connections || []).forEach((c, i) => {
      const tr = document.createElement('tr');
      if (i % 2 === 1) tr.style.background = '#FAFAFA';
      [c.device_port, '→', c.remote, c.remote_port, c.optic, c.cable, c.status, c.sheet]
        .forEach((val, ci) => {
          const td = document.createElement('td');
          td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;'
            + (ci === 1 ? ' text-align:center; color:#aaa;' : '')
            + ([0, 3].includes(ci) ? ' font-family:monospace;' : '')
            + (ci === 7 ? ' font-size:0.8rem; color:#747283;' : '');
          td.textContent = val || '';
          tr.appendChild(td);
        });
      connBody.appendChild(tr);
    });
    document.getElementById('ibConnSection').style.display = '';
  }

  // --- RoCE Analyzer ---

  async function runRoCEQuery() {
    const file = document.getElementById('roceFile').files[0];
    const loc  = document.getElementById('roceLoc').value.trim();
    if (!file) { alert('Select the RoCE Build Sheet file.'); return; }
    if (!loc)  { alert('Enter a device location (e.g. dh202:003:37).'); return; }

    const btn = document.getElementById('roceBtn');
    btn.disabled = true;
    document.getElementById('roceStatus').textContent = 'Searching across all sheets...';
    ['roceOpticSection', 'roceConnSection'].forEach(id => document.getElementById(id).style.display = 'none');

    const form = new FormData();
    form.append('file', file);
    form.append('loc', loc);

    try {
      const res = await fetch(apiPath('/api/roce-query'), { method: 'POST', body: form });
      const json = await res.json();
      if (!res.ok) {
        document.getElementById('roceStatus').textContent = 'Error: ' + (json.error || 'Unknown error');
        return;
      }
      _renderRoCEResult(json.data);
      document.getElementById('roceStatus').textContent =
        'Done — ' + json.data.total + ' connection' + (json.data.total === 1 ? '' : 's') + ' found.';
    } catch(e) {
      document.getElementById('roceStatus').textContent = 'Request failed: ' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  function _renderRoCEResult(d) {
    document.getElementById('roceLocLabel').textContent = d.location;
    document.getElementById('roceDnsLabel').textContent = d.dns_name ? '(' + d.dns_name + ')' : '';

    // Optic summary
    const opticBody = document.getElementById('roceOpticBody');
    opticBody.innerHTML = '';
    Object.entries(d.optic_summary || {}).forEach(([type, count]) => {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td style="padding:4px 8px; border:1px solid #CDCED6;">' + type + '</td>' +
        '<td style="padding:4px 8px; border:1px solid #CDCED6; text-align:right; font-weight:bold;">' + count + '</td>';
      opticBody.appendChild(tr);
    });
    document.getElementById('roceOpticSection').style.display = '';

    // Connections
    document.getElementById('roceConnCount').textContent = d.total;
    const connBody = document.getElementById('roceConnBody');
    connBody.innerHTML = '';
    (d.connections || []).forEach((c, i) => {
      const tr = document.createElement('tr');
      if (i % 2 === 1) tr.style.background = '#FAFAFA';
      [c.device_port, c.device_connector, c.device_interface, c.device_optic,
       '→', c.remote_loc, c.remote_port, c.remote_optic, c.status, c.sheet]
        .forEach((val, ci) => {
          const td = document.createElement('td');
          td.style.cssText = 'padding:4px 8px; border:1px solid #CDCED6;'
            + (ci === 4 ? ' text-align:center; color:#aaa;' : '')
            + ([0, 1, 2, 6].includes(ci) ? ' font-family:monospace;' : '')
            + (ci === 9 ? ' font-size:0.8rem; color:#747283;' : '');
          td.textContent = val || '';
          tr.appendChild(td);
        });
      connBody.appendChild(tr);
    });
    document.getElementById('roceConnSection').style.display = '';
  }

  // --- AI Q&A ---
  async function askAi() {
    const q = document.getElementById('question').value.trim();
    if (!q) { alert('Enter a question.'); return; }
    document.getElementById('qaStatus').textContent = 'Thinking...';
    const res = await fetch(apiPath('/api/ask'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q})
    });
    const data = await res.json();
    document.getElementById('qaStatus').textContent = '';
    if (!res.ok) {
      document.getElementById('qaOut').textContent = 'Error: ' + data.error;
      return;
    }
    const r = data.result || {};
    const ts = r.timestamp ? new Date(r.timestamp * 1000).toLocaleString() : '';
    const stats = r.provider
      ? r.provider + ' / ' + r.model + '  |  ' + (r.input_tokens||0).toLocaleString()
        + ' in + ' + (r.output_tokens||0).toLocaleString() + ' out  |  ' + r.elapsed_seconds + 's'
      : '';

    // Build context source badge
    const contextSource = data.context_source || 'UNKNOWN';
    const badgeColors = {
      'POSTGRES': '#2741E7',
      'RACK_ANALYZER': '#0070CC',
      'IN_MEMORY': '#0066B2',
      'EMPTY_FALLBACK': '#CC0000'
    };
    const badgeColor = badgeColors[contextSource] || '#666';
    const badgeHtml = '<span style="display:inline-block; background:' + badgeColor
      + '; color:white; padding:3px 8px; border-radius:3px; font-size:0.75rem; font-weight:bold; margin-left:8px;">'
      + contextSource + '</span>';

    const answerWithBadge = (r.answer || '') + '\\n\\nContext source: ' + badgeHtml;

    const qaOutEl = document.getElementById('qaOut');
    qaOutEl.textContent =
      'Time: ' + ts + '\\n' +
      (stats ? stats + '\\n' : '') +
      '\\n' + (r.answer || '');

    // Add context source badge after answer
    const badgeSpan = document.createElement('span');
    badgeSpan.style.cssText = 'display:inline-block; background:' + badgeColor
      + '; color:white; padding:4px 10px; border-radius:4px; font-size:0.8rem; font-weight:bold; margin-left:8px; margin-top:8px;';
    badgeSpan.textContent = contextSource;
    qaOutEl.appendChild(document.createElement('br'));
    qaOutEl.appendChild(document.createElement('br'));
    const sourceLabel = document.createElement('span');
    sourceLabel.textContent = 'Context: ';
    sourceLabel.style.cssText = 'color:#747283; font-size:0.85rem;';
    qaOutEl.appendChild(sourceLabel);
    qaOutEl.appendChild(badgeSpan);

    // Add pg_warning if present
    if (data.pg_warning) {
      qaOutEl.appendChild(document.createElement('br'));
      const warning = document.createElement('span');
      warning.textContent = 'Warning: ' + data.pg_warning;
      warning.style.cssText = 'display:block; margin-top:8px; color:#D97706; font-size:0.85rem;';
      qaOutEl.appendChild(warning);
    }
  }
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Background NetBox ingestion scheduler
# ---------------------------------------------------------------------------
# Pulls a fresh snapshot every 15 minutes so the /dashboard endpoints always
# read recent data. Single-worker gunicorn config (Dockerfile) means this fires
# once per process. Disable by setting ATLAS_RUN_SCHEDULER=0.

_SCHEDULER_STARTED = False
_scheduler = None


def _run_netbox_ingest_safe():
    """Wrapper that swallows exceptions so the scheduler keeps ticking."""
    try:
        result = netbox_dashboard_ingest.ingest_snapshot()
        log.info("NetBox snapshot complete: %s", result)
    except (RuntimeError, OSError) as exc:
        log.warning("NetBox ingest failed: %s", exc)
    except Exception:  # noqa: BLE001 — keep scheduler alive on unexpected errors
        log.exception("NetBox ingest crashed")


def _start_netbox_scheduler():
    global _SCHEDULER_STARTED, _scheduler
    if _SCHEDULER_STARTED:
        return
    if os.getenv("ATLAS_RUN_SCHEDULER", "1") != "1":
        log.info("NetBox scheduler disabled (ATLAS_RUN_SCHEDULER != 1)")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.warning("APScheduler not installed; NetBox scheduler skipped")
        return

    interval_min = int(os.getenv("NETBOX_INGEST_INTERVAL_MIN", "15"))
    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    # Run once shortly after startup so the dashboard isn't empty.
    sched.add_job(
        _run_netbox_ingest_safe,
        trigger=IntervalTrigger(minutes=interval_min),
        id="netbox_ingest",
        next_run_time=None,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    _SCHEDULER_STARTED = True
    log.info("NetBox scheduler started (every %d min)", interval_min)

    # Seed first snapshot in a background thread so app startup isn't blocked
    # by a slow NetBox query.
    threading.Thread(target=_run_netbox_ingest_safe, name="netbox-seed", daemon=True).start()


# Start the scheduler when the app module is imported (e.g. by gunicorn).
_start_netbox_scheduler()

# ---------------------------------------------------------------------------
# WSGI entrypoint
# ---------------------------------------------------------------------------
# Canvas proxies requests to the container WITH the BASE_PATH prefix intact
# (e.g. /canvas-apps/atlas/api/health). DispatcherMiddleware strips that
# prefix so all Flask routes stay unprefixed and work identically in both
# Canvas and local dev.
# When BASE_PATH is unset (local Docker Compose / direct python run),
# `application` is just the Flask app itself — no behaviour change.
_base_path = os.getenv("BASE_PATH", "").rstrip("/")
if _base_path:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from werkzeug.wrappers import Response as _WerkzeugResponse
    application = DispatcherMiddleware(
        _WerkzeugResponse("Not Found", status=404),
        {_base_path: app},
    )
else:
    application = app


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5050")), debug=False)
