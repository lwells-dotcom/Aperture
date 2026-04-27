# Atlas Attack Plan — 2026-04-25

Source: `GITNEXUS_AUDIT_RESULTS.md` (GitNexus + 6-agent line-by-line review)
Total findings: 70+ across 10 sections (C1, H1-H3, M1-M5, L1, R1-R6, N1-N12, B1-B8, V1-V20, W1-W18, Q1-Q3, G1-G5, F1-F14, P1-P15)

---

## Wave 1 — CRITICAL (3 terminals, parallel)

### Terminal 1: DB Stability (`atlas_data_loader.py`)

```
cd ~/Atlas/DCT_Scripts/Optic_Count

Read GITNEXUS_AUDIT_RESULTS.md for full context on these findings.
Run gitnexus_impact on managed_connection and load_file before making any changes.

Fix these 4 issues in atlas_data_loader.py:

C1 (lines 117-125): managed_connection() returns dirty connections to the pool on exception.
Add conn.rollback() in an except block before re-raising. This is the root cause of
cascading DB failures — one bad query poisons a pool connection for all subsequent callers.

V1 (lines 182, 212, 536, 592, 693, 740, 922): load_file() calls conn.commit() in 7 separate
places. If step 5 fails, steps 1-4 are already committed. Remove individual commits from
helper functions; commit once at the end of load_file(), or use savepoints.

V2 (lines 513-526): After ON CONFLICT DO NOTHING dedup, inserted_ids has gaps but the zip
pairs them positionally with rows[]. Wrong raw JSON gets stored against wrong connection IDs.
Fix: use a RETURNING clause or CTE that pairs (connection_id, raw_json) at insert time.

L1 (lines 128-135): Remove dead get_connection() and return_connection(). No callers exist.
They expose raw pool get/put without a context manager — a leak trap for future contributors.

Run the existing test suite after changes to verify nothing breaks.
Do NOT touch any other files.
```

### Terminal 2: Security Hardening (`atlas_web_app.py` + `demo_auth_ai.py`)

```
cd ~/Atlas/DCT_Scripts/Optic_Count

Read GITNEXUS_AUDIT_RESULTS.md sections 1 and 9 for full context.
Run gitnexus_impact on each function before editing.

Fix these 5 issues:

V5 (atlas_web_app.py lines 435, 576, 610): Raw exception messages returned to API clients.
Replace str(exc) in JSON responses with generic user-facing messages like "File processing
failed" or "Invalid input". Log the full exception server-side with log.exception().

V6 (atlas_web_app.py lines 93-97): _get_client_ip() trusts X-Forwarded-For unconditionally.
Use request.remote_addr directly. If a reverse proxy is needed later, add a TRUSTED_PROXIES
env var allowlist. Rate limiting is currently useless because any client can spoof the header.

V7 (atlas_web_app.py lines 100-115): Rate limit store does O(n) cleanup of 5000+ keys
under _state_lock. Switch to a time-bucketed approach or limit cleanup to a batch of 100
expired keys per check instead of scanning the full dict.

V9 (atlas_web_app.py + demo_web_app.py): No MAX_CONTENT_LENGTH. Add:
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  (100 MB)
to both web apps near the top where app is configured.

V15 (demo_auth_ai.py lines 57-60): TOKEN_SECRET only validated when create_demo_token()
is called. Move _require_token_secret() call to module level so the app fails at startup
if the secret is missing, not mid-request.

Do NOT touch atlas_data_loader.py or atlas_query_router.py — other terminals own those.
```

### Terminal 3: SQL & Routing Fixes (`atlas_query_router.py`)

```
cd ~/Atlas/DCT_Scripts/Optic_Count

Read GITNEXUS_AUDIT_RESULTS.md sections 1, 9, and 10 for full context.
Run gitnexus_impact on each function before editing.

Fix these 5 issues in atlas_query_router.py:

W4 (line ~323): data_hall_summary SQL queries a_locode which is almost always NULL.
Replace with: split_part(a_loc_cab_ru, ':', 1) AS data_hall
This extracts the data hall prefix (e.g. "dh202") from location strings like "dh202:041:42".
Update the GROUP BY and ORDER BY accordingly.

H3 (lines 968-974): ip_lookup fallback uses last word of question or "%" matching all rows.
Fix: if no IP is extracted, do NOT execute ip_lookup. Route to "general" instead.
Add a log.warning when ip_lookup has no valid search pattern.

M1 (lines 905-906): _build_location_pattern returns "" for pure rack numbers like "3" or "12".
Empty string causes the SQL WHERE clause to be skipped ('' = '' is TRUE).
Fix: return None instead of "". In build_query_params(), check for None and either:
(a) route to location_lookup with an explicit "rack number alone is ambiguous" message, or
(b) build a broader pattern like "%:rack_number:%" to match any data hall.

V13 (lines 898-900): rack.zfill(3) zero-pads to 3 digits. "dh202:41" becomes "dh202%:041:%"
which won't match "dh202:41:10" in the DB. Fix: try both padded and unpadded in an OR
condition, or check the actual data format stored in the DB before padding.

R6 (line 997): execute_query() silently falls back to "general" SQL for unknown qtype.
Add: if qtype not in _SQL_TEMPLATES: log.warning("Unknown qtype %r, falling back to general", qtype)
```

---

## Wave 2 — HIGH (3 terminals, parallel, after Wave 1 merges)

### Terminal 1: Web App Route Fixes
Covers: H1, H2, M4, B3, V8, V10, V19

### Terminal 2: Performance — Excel Parse & iterrows
Covers: N4, N1, N2, B1, B2, B7, B8

### Terminal 3: Routing Gaps & Context
Covers: W5, W6, W1, W15, V14

---

## Wave 3 — MEDIUM (3 terminals, parallel)

### Terminal 1: Cutsheet Pipeline Cleanup
Covers: N3, N5, N6, N7, V18

### Terminal 2: Query Router Refactor
Covers: R4, R3, R5, M2, M3, R1, R2

### Terminal 3: Missing Query Types & Indexes
Covers: W10, W8, W7, W11, W14, W16, W17, G3

---

## Wave 4 — Optimization + Cleanup

### LLM Performance
Covers: Q3 (Anthropic SDK migration), Q1 (SSL context caching), short-circuit simple queries, streaming, V16, V17

### Hardening
Covers: M5, V3, V4, N8-N12, B5, B6, G4, G5, F14

---

## Separate Track: New Cutsheets

Depends on Wave 3 Terminal 1 (N3 status dict unification, N5 per-site profiles).
Load in Claude in Excel, map STATUS columns, generate new STATUS_MAP entries,
verify column layouts, integrate into preprocessor.
