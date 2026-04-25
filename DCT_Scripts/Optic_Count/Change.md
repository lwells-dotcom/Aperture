# Change Log for `DCT_Scripts-main/Optic_Count`

This document summarizes all code and documentation changes made during this session for the Optic Count demo enhancements.

## Scope
- Root folder: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count`
- Goal: Add a production-like demo flow with strict grounded AI Q&A, simulated identity verification, and cleaner output UX.

## New Files Added

### 1) `demo_auth_ai.py`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/demo_auth_ai.py`
- Purpose: Shared auth + grounded AI logic used by GUI and web demo.
- Added functionality:
  - Demo PIN verification via env var (`DEMO_VERIFY_PIN`, default `123456`).
  - Demo token issuance with expiry/scope.
  - Token validation with HMAC signature checking.
  - Strict grounded OpenAI request builder (context-only instruction).
  - OpenAI API call helper (`/v1/chat/completions`) with error handling.
  - Unified Q&A wrapper (`qa_with_token`).
- Important updates made later:
  - Token format changed from JSON token object to compact header-safe string format:
    - New format: `<payload_b64>.<hex_signature>`
  - Backward-compatible parser kept for older JSON token format.
  - Prompt style updated to enforce human-readable sections:
    - `Summary`
    - `Key Findings`
    - `Data Quality Flags`
    - `Missing Data or Limits`
    - `Recommended Next Step`

### 2) `demo_web_app.py`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/demo_web_app.py`
- Purpose: Lightweight Flask web demo for verify/upload/count/AI Q&A.
- Added API endpoints:
  - `GET /api/health`
  - `POST /api/demo-verify-pin`
  - `POST /api/upload-count`
  - `POST /api/sheet-qa`
  - `GET /api/audit-log`
- Added in-memory demo stores:
  - `USER_CONTEXT` for per-user parsed sheet context.
  - `AUDIT_LOG` for event tracking.
- Added browser UI at `/`:
  - Verify section (username + PIN)
  - Upload + Count section
  - Ask AI section
- Runtime config:
  - `debug=False` to avoid environment startup permission issues.
- Output/UX updates:
  - Replaced raw JSON blob rendering with readable text output.
  - Added clean count summary formatting (`Top Counts`).
  - Added cleaner AI answer rendering.
- JS stability fixes:
  - Fixed script syntax issue (`Invalid or unexpected token`) caused by newline escaping in Python triple-quoted HTML.
  - Corrected JS string escaping with `\\n` in Python source so browser receives valid `\n`.

### 3) `requirements-demo.txt`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/requirements-demo.txt`
- Purpose: Demo dependency list.
- Added packages:
  - `pandas`
  - `openpyxl`
  - `flask`
  - `pillow`

### 4) `Change.md`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/Change.md`
- Purpose: This change handoff document.

### 5) `.gitignore`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/.gitignore`
- Purpose: Prevent committing local secrets and runtime artifacts.
- Added ignore rules for:
  - `.env`
  - `uploads/`
  - `__pycache__/`
  - `.venv/`
  - `.DS_Store`

### 6) `.env.example`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/.env.example`
- Purpose: Document required local environment variables without exposing secrets.
- Includes:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `DEMO_VERIFY_PIN`
  - `DEMO_TOKEN_SECRET`
  - `DEMO_TOKEN_TTL_SECONDS`
  - `PORT`
  - `DEMO_UPLOAD_DIR`

### 7) `.env` (local template)
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/.env`
- Purpose: Local secret/config storage for running the demo.
- Note: This file is ignored via `.gitignore`.

## Files Modified

### 8) `Define_Optic_Count.py`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/Define_Optic_Count.py`
- Changes made:
  - Added `import os`.
  - Fixed IB logic bug in `count_infini_band`:
    - Removed stray `put_optic_in_list(optic_list_to_return, value)` call in node-sheet loop where `value` could be stale/undefined.
  - Added shared helper APIs:
    - `count_file(input_file)`
    - `optic_list_to_dict(optics_in)`
    - `build_sheet_context(files_to_count)`
  - New `build_sheet_context` output shape:
    - `files[]`: file metadata + per-file counts
    - `summary`: aggregate counts across all uploaded files
  - Improved cutsheet detection robustness:
    - Added `_find_cutsheet_sheet_name(xls)` helper.
    - Accepts cutsheet tab name case-insensitively (`cutsheet`, `CUTSHEET`, etc.).
    - Fallback auto-detects cutsheet-like tabs by required columns:
      - `A-OPTIC`, `Z-OPTIC`, `A-SIDE LOCODE`, `Z-SIDE LOCODE`
    - `get_file_type()` now uses auto-detection helper.
    - `count_cutsheet()` now resolves sheet name dynamically instead of requiring exact `CUTSHEET`.

### 9) `Optic_Count_GUI.py`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/Optic_Count_GUI.py`
- Changes made:
  - Integrated shared module imports:
    - `import demo_auth_ai`
  - Added global demo session state:
    - `current_sheet_context`
    - `session_token`
  - Existing count flow preserved; now also builds grounded context:
    - `current_sheet_context = Define_Optic_Count.build_sheet_context(files_to_count)`
  - Added simulated verification UI and logic:
    - Username entry
    - PIN entry
    - Verify button (`on_verify_pin_click`)
    - Status indicator (`verify_status_var`)
  - Added grounded AI Q&A UI and logic:
    - Question input
    - Ask button (`on_ask_ai_click`)
    - Output pane
  - Added guardrails:
    - Require parsed sheet context before Q&A.
    - Require verified token before Q&A.
  - Updated clear/reset behavior to clear:
    - loaded files
    - context
    - session token
    - verify status
    - Q&A output
  - Output formatting update:
    - GUI now displays clean answer text only in Q&A pane (removed extra wrapper lines for simpler demo readability).

### 10) `README.md`
- Path: `/Users/lwells/Desktop/DCT_Scripts-main/Optic_Count/README.md`
- Changes made:
  - Expanded from minimal script notes to full demo guidance.
- Added documentation for:
  - New demo auth + grounded AI capabilities
  - Web demo endpoints
  - Strict grounded mode behavior
  - `.env`-based configuration flow (`cp .env.example .env`)
  - Run instructions for GUI and web
  - Production note: keep API key server-side, replace PIN with real SSO later

### 11) `demo_auth_ai.py` (additional env upgrade)
- Added local `.env` loader so scripts can read secrets/config from `.env` automatically.
- Loader behavior:
  - Reads `.env` from `Optic_Count` directory.
  - Ignores blank/comment lines.
  - Parses `KEY=VALUE`.
  - Does not overwrite variables that are already set in shell environment.

## Behavior Changes Introduced

1. Strict grounded AI mode
- AI is instructed to answer from parsed sheet context only.
- If data is missing from context, AI should explicitly say what is missing.

2. Simulated identity gate
- Users must verify PIN to get a scoped short-lived token before AI usage.

3. Per-user data isolation (demo-level)
- Uploaded sheet context is stored by `username`/token claim for Q&A.

4. Better parsing resilience for real-world files
- CUTSHEET files now parse even when the tab is not exactly named `CUTSHEET`, if expected columns exist.

5. Cleaner presentation
- Web and GUI output moved away from raw JSON display toward human-readable summaries.

## Validation Performed During Development

- `py_compile` syntax checks for updated Python files.
- Local token creation/validation checks.
- Flask API endpoint smoke tests:
  - health
  - verify PIN
  - upload+count
  - sheet Q&A
- End-to-end test using local XLSX (`CUTSHEET_DEMO_V2.xlsx`) confirming:
  - file type detection now resolves as cutsheet
  - summary contains expected optics keys/counts
  - AI path uses parsed context

## Notes for Repo Owner

- This is intentionally a demo-safe auth simulation (PIN + signed token) and not an enterprise auth implementation.
- Real production path should swap auth adapter to Okta/OIDC and use persistent storage for session/context/audit.
- Current web demo stores context and audit log in memory for simplicity.

---

# Session 2026-04-19 (Session 2): Ingestion Strictness Hardening

## Goal
Address 7 findings where the ingestion pipeline was too permissive, allowing "almost right" cutsheets to store ambiguous data that looked queryable but was subtly wrong.

## Changes

### cutsheet_profiles.py
- `detect_profile()` returns `(profile, score)` tuple instead of just `profile`. Score is 0.0-1.0 fraction of fingerprint columns matched. Logs warning on partial matches.
- `apply_profile()` compares values row-by-row when duplicate source columns map to the same Canon target. Logs conflict count, first conflicting row index, and sample values.
- `normalize_model()` and `normalize_model_column()` do two-pass resolution: exact alias lookup, then strip revision suffixes (`-revB`, `-v2`, `-r1`) and retry.
- Added `Canon.HOST_ROW_TYPE` for physical placement metadata.
- `ROW:TYPE` now maps to `Canon.HOST_ROW_TYPE` instead of `Canon.HOST_ROLE`.

### atlas_data_loader.py
- `load_cutsheet()`: missing required canonical columns now raises `ValueError` instead of logging a warning.
- `load_site_hosts()`: same hard-fail behavior for missing required host columns.
- Section header derivation: added `_SECTION_HEADER_PATTERNS` for positive topology name matching (TIER, SPINE, LEAF, FDP, CDU, GPU, NVLINK, etc.). Rejected candidates logged for pattern expansion.
- Sheet selection: two-pass approach with `_verify_cutsheet_schema()` post-heuristic validation. Tabs must have optic + device/port columns to qualify.
- INSERT uses `ON CONFLICT DO NOTHING` with unique indexes. Duplicate connection count logged.
- `load_site_hosts()` now inserts `row_type` column.

### atlas_schema.sql
- `host_inventory.row_type TEXT` column added (with `ALTER TABLE ADD COLUMN IF NOT EXISTS` migration).
- `idx_cc_unique_cable`: unique index on `(upload_id, cable_id)` where cable_id is non-empty.
- `idx_cc_unique_ports`: unique index on `(upload_id, a_device, a_port, z_device, z_port)` where cable_id is empty.

## Deployment
Requires docker image rebuild and helm redeploy for schema changes. Run deploy steps from DEPLOY_STEPS.md (Full Clean Deploy).
