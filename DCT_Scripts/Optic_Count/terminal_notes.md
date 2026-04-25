# Terminal Notes — 2026-04-22

## Session Summary

### atlas_query_router.py
- Added documentation comment above `"cross_site_models"` SQL template explaining that cross-site queries intentionally ignore `upload_id` and join across all active uploads (`cu.is_active = TRUE`).

### atlas_web_app.py — Thread safety for shared dicts
- Added `_state_lock = threading.Lock()` after the `USER_CONTEXT`, `USER_SITE`, `AUDIT_LOG` declarations.
- Wrapped `_evict_stale_contexts()` body with `with _state_lock:`.
- Wrapped `_audit()` body with `with _state_lock:`.
- `upload_count` route: narrow lock around `USER_CONTEXT` write; narrow lock around `USER_SITE` write (both after slow I/O, not across it).
- `buildsheet` route: narrow lock around the `USER_CONTEXT` read-modify-write block.
- `ask_ai` route: narrow locks around `USER_SITE.get`, `USER_SITE` write, `USER_CONTEXT.get`, and two indirect mutations of `sheet_context` (which is a reference into `USER_CONTEXT`). No lock held across DB or LLM calls.

### atlas_web_app.py — PIN rate limiting
- Added `_RATE_LIMIT_STORE`, `_RATE_LIMIT_MAX` (10), `_RATE_LIMIT_WINDOW` (60s), `_RATE_LIMIT_MAX_KEYS` (5000) constants.
- Added `_get_client_ip()` — reads `X-Forwarded-For`, falls back to `remote_addr`.
- Added `_check_rate_limit(key)` — sliding window counter using `time.monotonic()`, protected by `_state_lock`. Evicts expired keys when store exceeds 5000 entries.
- `verify_pin` route: rate limit check fires first (before JSON parse or DB), returns 429 on breach.

### diagnose_live.py — Schema state section
- Added `"cutsheet_raw_rows"` to `required_tables` list (section 3).
- Added post-loop `warn()` check: if `raw_row` column still exists on `cutsheet_connections`, flags that the migration to `cutsheet_raw_rows` may not have completed.

---

## Session Summary — 2026-04-22 (continued)

### atlas_query_router.py — ip_lookup SQL fix
- `raw_row` column no longer exists on `cutsheet_connections` (migrated to `cutsheet_raw_rows`).
- Updated `ip_lookup` template: aliased `cutsheet_connections` as `cc`, added `LEFT JOIN cutsheet_raw_rows rr ON rr.connection_id = cc.id`, prefixed all columns with `cc.`, changed `raw_row` reference to `rr.raw_row`. WHERE filter on `rr.raw_row::text ILIKE` preserved (effectively inner join — intentional).

### atlas_query_router.py — ip_lookup format block
- `format_results_for_llm` ip_lookup block now renders `a_device:a_port -> z_device:z_port [status]` instead of the useless `a_device -> z_device (status)`.
- Appends up to 3 shortest `key:value` pairs from `raw_row` (if it's a dict) whose values contain a token from the question (≥4 chars), to surface what matched without dumping the whole row.

### atlas_query_router.py — trend_status / trend_section comments
- Added documentation comment above `trend_status` template: notes that ALL uploads (active and inactive) are included intentionally for historical timeline; how to add `is_active` filter if desired.
- Added documentation comment above `trend_section` template: same intent noted.
- No SQL changed.

### atlas_web_app.py — Security headers middleware
- Added `set_security_headers` `@app.after_request` decorator immediately after `app = Flask(__name__)` (before `UPLOAD_DIR`), matching `demo_web_app.py`.
- Sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Content-Security-Policy` on every response.

---

## 2026-04-22 12:33:24 — diagnose_live.py verification (omc team)

Ran `/omc team 1:claude` to verify two fixes to `diagnose_live.py`. Both were already applied earlier in the session; no code changes were needed.

- **Fix 1 (line 121–142):** `"cutsheet_raw_rows"` present in `required_tables`; `raw_row` migration warning fires after the `critical_cols` loop.
- **Fix 2 (line 144–150):** Dead `if "file_hash" not in [r[0] for r in cur.execute(...) or []]` guard removed; replaced with plain `cur.execute()` + `fetchall()` matching the `cc_cols` pattern above it.

---

## 2026-04-22 14:35:41

### atlas_web_app.py — upload_count Postgres load moved to background thread
- Extracted `_pg_load_background(save_path, site_code, username)` helper; runs `atlas_data_loader.load_file` in a daemon thread.
- Route returns immediately with `"pg_loaded": "pending"` instead of blocking on the DB write.
- `USER_SITE` still populated under `_state_lock` once background load completes; `ask_ai` recovery path handles the window where it isn't ready yet.
- Removed `pg_rows` and `pg_error` from the response (no longer meaningful for an async load).

### atlas_web_app.py — upload_count: skip build_sheet_context() when Postgres is available
- If `_check_postgres()` is True at upload time, `build_sheet_context()` is skipped entirely; a minimal stub `{"files": [...], "ts": ...}` is stored in `USER_CONTEXT`.
- If Postgres is down, falls back to existing behavior: `build_sheet_context()` runs and full in-memory context is stored.
- Eliminates the expensive `iterrows()` loops in `_extract_cutsheet_column_c_locations` and `_extract_device_models` for the common (Postgres-up) case.
- `clear_excel_cache()` still runs in `finally` in both paths.

### .dockerignore created
- New file at `Optic_Count/.dockerignore` excluding `.env`, `__pycache__`, `uploads/`, `*.xlsx`, `*.csv`, test files, `diagnose_*.py`, `query_debug.py`, `knowledge/`, `helm/`, and `*.md` from Docker build context.

### helm/atlas/values.yaml — web.replicaCount set to 1
- Changed from `2` to `1`. Added comment explaining single-replica constraint: RWO PVC mount conflicts and cross-pod `USER_CONTEXT` isolation. Documents the ReadWriteMany + session affinity path for future multi-replica scaling.

### helm/atlas/templates/web-deployment.yaml — container securityContext added
- Added `securityContext` to the `web` container spec after `imagePullPolicy`: `runAsNonRoot: true`, `runAsUser/Group: 1000`, `readOnlyRootFilesystem: false` (gunicorn tmp + uploads writes), `allowPrivilegeEscalation: false`.

---

## 2026-04-22 18:14:34

### helm/atlas/templates/web-deployment.yaml — wait-for-postgres retry limit
- Added `retries` / `max_retries=30` counter to the init container shell script.
- After 30 attempts (~60s at 2s sleep), script exits 1 → pod enters `CrashLoopBackOff` with Kubernetes backoff instead of looping forever silently.
- Log line now shows `($retries/$max_retries)` on each wait iteration.

### atlas_schema.sql + helm/atlas/files/atlas_schema.sql — documentation cleanup
- **Second `cutsheet_raw_rows` block:** Replaced single-line migration comment with 3-line version explaining the duplication is intentional and `IF NOT EXISTS` makes it safe.
- **ALTER TABLE `host_inventory`:** Replaced comment with `-- (included in CREATE TABLE above; kept for manual upgrades against existing databases)`.
- **Helm copy brought in sync:** Added `row_type TEXT` column to `host_inventory` CREATE TABLE definition + matching ALTER TABLE + comment. Helm copy was missing these entirely.

---

## Session Recap — April 22, 2026

### What we did

Full audit and hardening pass on the Atlas codebase (DCT_Scripts/Optic_Count). 11 issues identified, all resolved across two waves using 3 parallel Claude Code terminals via oh-my-claudecode team mode.

### Bugs fixed (breaking or data-correctness)

1. **ip_lookup SQL template crash** — Referenced `raw_row` column on `cutsheet_connections`, but that column was migrated to `cutsheet_raw_rows` table. Query would crash on any post-migration deployment. Fixed: JOIN to `cutsheet_raw_rows`, updated formatter to show port detail.

2. **Optic double-count in atlas_postgres_context.py** — The optic summary UNION ALL'd `a_optic` and `z_optic` counts separately, double-counting cables with the same optic on both sides. Fixed: replaced with COALESCE dedup approach matching the `optic_count` template in `atlas_query_router.py`.

3. **diagnose_live.py dead code path (line 139)** — `cur.execute()` returns None in psycopg2, so the list comprehension always evaluated to `[]`. Worked by accident due to fallback re-query. Cleaned up.

### Security fixes

4. **Missing security headers in atlas_web_app.py** — No `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, or CSP headers. `demo_web_app.py` already had them. Added matching `@app.after_request` middleware.

5. **PIN brute-force vulnerability** — `verify_pin` endpoint had no rate limiting. PIN is a 6-digit string, trivially brutable. Added rate limiter: 10 attempts per 60s window per client IP, using `time.monotonic()` and `_state_lock` for thread safety.

### Thread safety and correctness

6. **Race conditions in atlas_web_app.py** — `USER_CONTEXT`, `USER_SITE`, `AUDIT_LOG` dicts had no thread locking. `_evict_stale_contexts` iterates and mutates the dict. Race conditions under concurrent Flask requests. Fixed: added `_state_lock`, wrapped all shared dict access.

7. **Same thread safety issues in demo_web_app.py** — Applied matching fixes including `_check_rate_limit` protection.

8. **time.time() vs time.monotonic()** — `_check_postgres` in `atlas_web_app.py` used `time.time()` instead of `time.monotonic()`. Fixed to match `atlas_data_loader.py`. Same fix applied to `demo_web_app.py`.

### Documentation and diagnostics

9. Added `cross_site` query documentation comment explaining these templates intentionally ignore `upload_id` and filter via `cu.is_active = TRUE` across all sites.

10. Added `trend_status` and `trend_section` documentation noting they intentionally include all uploads (active and inactive) for historical timeline view.

11. `diagnose_live.py` now checks for `cutsheet_raw_rows` table existence and warns if `raw_row` column still exists on `cutsheet_connections` (migration incomplete).

### Test results

25/30 tests pass. All routing, classification, and SQL logic tests clean. 5 errors are pre-existing and unrelated to today's work:

- `test_rack_context_bridge`: Flask not installed in test runner's Python
- `test_build_sheet_processor`: stale test referencing renamed/moved function
- `test_define_optic_count_in_service` (3 errors): tests reference deleted API (`count_cutsheet`, `count_devices_cutsheet`, `_is_in_service_status`)

---

## Open items (prioritized)

### High priority — fix next session

1. **OPTIC COUNT UNDERCOUNT (COALESCE dedup too aggressive)** — Verified 2026-04-22 via independent pandas count against raw CUTSHEET tab. Raw count: 48,867 total optics. LLM/Postgres context reported: 48,677. Delta of 190. Worst offenders: QSFPDD-400G-DR4 off by 114 (566 real vs 452 reported), QSFP28-100G-DR1 off by 48, OSFP-800G-2DR4 off by 24. Root cause: the COALESCE(NULLIF(a_optic,''), NULLIF(z_optic,'')) query in `atlas_postgres_context.py` picks the first non-null optic per row. When A-OPTIC and Z-OPTIC on the same row are DIFFERENT optic types, only one gets counted. The old UNION ALL overcounted (duped when both sides matched), but the new COALESCE undercounts (drops the second side when they differ). Fix: count A-OPTIC and Z-OPTIC independently and sum them, or use UNION ALL but with the individual side values (not COALESCE). Status counts were close (within 10-107 rows, likely due to "Cable Not Run: Priority" grouping).

2. **STALE TESTS: test_define_optic_count_in_service** — References functions that no longer exist in `Define_Optic_Count`. Either delete these tests or rewrite them against the current API. Dead weight that makes test runs look broken when they're not.

3. **STALE TEST: test_build_sheet_processor** — References a function at an import path that doesn't exist. Verify whether `process_rack` moved or was renamed, then fix the import or delete the test.

4. **GUNICORN WORKER ISOLATION** — `USER_CONTEXT` and `USER_SITE` are per-process dicts. With multiple gunicorn workers, a user who uploads on worker A gets "no sheet loaded" on worker B. The CLAUDE.md mentions this (rule R7). The real fix is to always recover from Postgres (the `ask_ai` route already tries this as a fallback). Verify the fallback path actually works reliably and consider removing the in-memory dependency for the Postgres path entirely.

### Medium priority

5. **DUPLICATE CODE** — `atlas_web_app.py` and `demo_web_app.py` share ~80% of the same logic (site extraction, auth, upload handling, ask_ai). `demo_web_app.py` is described as "the older standalone demo" but still serves the health endpoint for Helm liveness probes. Consider whether `demo_web_app.py` can be deprecated or merged.

6. **TEST ENVIRONMENT** — The 5 test errors all stem from running tests outside the Docker venv. Add a Makefile target or script that runs pytest inside the container, or add a `requirements-test.txt` so developers can set up a local venv easily.

7. **OPENAI FALLBACK** — `_call_openai` in `demo_auth_ai.py` has no retry logic, while `_call_anthropic` has 4 retries with exponential backoff. If you ever actually hit the OpenAI fallback path, transient 500s will fail immediately.

### Low priority / nice to have

8. **Inline HTML blob** — `atlas_web_app.py` is ~1567 lines with a giant HTML string inline. The HTML/JS could move to a `templates/` directory or a separate static file. Not urgent but it makes the file harder to navigate.

9. **Unbounded connection cache** — `_CONNECTION_CACHE` in `cutsheet_normalizer.py` is a module-level dict with no size bound or TTL. If many files get loaded across the process lifetime, memory grows unbounded. Low risk in practice since Docker restarts clear it.

---

## Session — 2026-04-22 (continued)

### Dockerfile — gunicorn worker model
- Changed from `--workers 4` to `--workers 1 --threads 4`.
- Single worker keeps `USER_CONTEXT`/`USER_SITE` in one process; `_state_lock` protects concurrent thread access. Atlas is I/O bound so threads give equivalent throughput.
- **Closes open item #3 (GUNICORN WORKER ISOLATION).**

### helm/atlas/values.yaml — demoVerifyPin
- Changed `demoVerifyPin` from hardcoded `"123456"` to `""` with `# REQUIRED: set via --set secrets.demoVerifyPin=<value>` comment. No default PIN ships in the chart.

### helm/atlas/templates/secret.yaml — demoVerifyPin validator
- Added `required "secrets.demoVerifyPin must be set"` to `DEMO_VERIFY_PIN` line. `helm install/upgrade` now fails fast with a clear error if the PIN is not explicitly supplied, matching the pattern already used for `dbPassword` and `demoTokenSecret`.

### helm/atlas/templates/web-service.yaml — session affinity
- Already had `sessionAffinity: ClientIP` with `timeoutSeconds: 3600`. No change needed.

### helm/atlas/templates/postgres-statefulset.yaml — volumes block
- Removed outer `{{- if .Values.schemaInit.enabled }}` / `{{- end }}` that wrapped the entire `volumes:` block.
- `volumes:` now always renders; only the `schema-init` configMap entry inside it is conditional.
- An empty volumes list is valid Kubernetes YAML; this prevents a template rendering gap when `schemaInit.enabled=false`.

---

## 2026-04-22 ~19:00 — LLM Response Quality, Context Compression, Parse Speed

Three parallel Claude Code terminals, all targeting `DCT_Scripts/Optic_Count`.

### Terminal 1: System Prompt Rework (demo_auth_ai.py)
- Rewrote all three system prompts in `_build_grounded_messages` (POSTGRES, RACK_ANALYZER, default/in-memory paths).
- Removed the forced "Always respond with exactly two sections: Summary and Key Findings" structure that made every response read like a corporate report.
- New prompts give the LLM identity ("You are Atlas, a datacenter infrastructure assistant"), tell it to answer the question directly in the first sentence, and only use tables when comparing 5+ items.
- Updated user message prefix from "Include brief evidence references" to "Cite specific counts or values from the data when relevant."

### Terminal 2: Context Compression — In-Memory Path (demo_auth_ai.py)
- `_build_legacy_trimmed_context`: capped optic_locations to top 10 locations per optic type, added `other_locations_count` for omitted entries. Stripped `evidence` arrays from `cutsheet_location_c_index` (row-number references meaningless to the LLM). Reduced default `max_locations` from 20 to 10.
- `_trim_context_for_llm`: added progressive token budget enforcement. If serialized context exceeds ~8000 words (~10k tokens), drops `top_locations` first, then `optic_locations` detail (keeps totals only), then `device_model_summary` locations (keeps counts only). Logs warning when trimming fires.
- `_build_normalized_context`: added connection list cap at 500 entries with truncation note when exceeded.
- Target: in-memory path under ~6k tokens for Ellendale-sized sheets (was ~19k).

### Terminal 3: Sheet Parse Optimization (Define_Optic_Count.py + atlas_web_app.py)
- Added `count_and_build_context(files)` to `Define_Optic_Count.py`: single-pass function that parses the xlsx once via `_cached_excel_file`, runs optic count logic, builds sheet context, returns `(count_text, context_dict)`. Eliminates the double-parse where `count_all_files_gui` and `build_sheet_context` each opened the file independently.
- Updated `upload_count` route in `atlas_web_app.py` to call the new single-pass function instead of two separate calls.
- Moved `_pg_load_background` thread start to right after `f.save(save_path)` so Postgres ingest runs in parallel with the count/context build instead of after it.
- Expected improvement: ~40-50% reduction in upload time for large workbooks.

### docker-compose.yml / .env alignment fix
- Renamed `POSTGRES_PASSWORD` to `DB_PASSWORD` in `.env` so docker-compose picks it up correctly. The compose file references `${DB_PASSWORD:-atlas}` but `.env` had `POSTGRES_PASSWORD=atlas_dev`, causing both services to silently use the default password `atlas` instead of `atlas_dev`.

---

## 2026-04-22 ~20:00 — Cutsheet Data Quality Analysis & Preprocessing Plan

### Root cause analysis: optic undercount
- Independent pandas verification against raw CUTSHEET tab confirmed 48,867 total optics vs LLM-reported 48,677 (delta of 190).
- Root cause identified: **11,284 rows (23% of data) have DIFFERENT optic types on A-OPTIC vs Z-OPTIC.** COALESCE picks A-OPTIC first and drops Z-OPTIC entirely on mismatched rows.
- Largest mismatch pair: A: OSFP-800G-2DR4 / Z: QSFP112-400G-DR4 (10,872 rows). QSFP112-400G-DR4 appears exclusively on the Z-side.
- Fix direction: count A-OPTIC and Z-OPTIC independently and sum. No deduplication. Each side is a separate physical optic.

### Root cause analysis: STATUS column pollution
- 6 real status values account for 55,983 rows: Cable Is Ran: Complete (14,665), Cable Not Run (29,822), Cable Is Ran: Not Terminated (2,487), Addition (1,233), Cable Not Run: Priority (106), blank (7,669).
- 165 additional unique STATUS values are section headers/labels pasted into the wrong column (e.g., "CON-01 Grid C1", "DH202 :: C2", "TIER-1 TO TIER-0 C2", "RACK 51 NET + CON").
- All 165 junk statuses have empty A-OPTIC and Z-OPTIC — they are visual dividers, not cable data.
- ~200 total section header rows polluting counts.

### Claude in Excel — normalization tabs generated
Used Claude in Excel on `MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx` to create three new tabs:

1. **STATUS_MAP** (171 rows): Maps every raw STATUS value to one of 6 canonical statuses (COMPLETE, NOT_TERMINATED, NOT_RUN, NOT_RUN_PRIORITY, ADDITION, BLANK) plus SECTION_HEADER. Zero UNKNOWN rows — every non-canonical status had empty optic data. This becomes the config for the Atlas preprocessor.

2. **OPTIC_SUMMARY**: Independent A-side/Z-side counts per optic type. Totals: A-side 24,451 + Z-side 24,416 = 48,867 grand total. Matches our Python verification exactly. SECTION_HEADER rows excluded from all counts.

3. **CUTSHEET_CLEAN** (55,983 rows): CUTSHEET with all ~200 section header rows removed. STATUS column replaced with canonical values. Original STATUS preserved in STATUS_ORIGINAL column (col U).

### Preprocessing pipeline plan
- New file: `cutsheet_preprocessor.py` — automated normalization at upload time
- `normalize_cutsheet_df(df)`: applies STATUS_MAP, strips SECTION_HEADER rows
- `count_optics_independently(df)`: counts A-OPTIC and Z-OPTIC as separate columns, returns per-type a_side/z_side/total
- `preprocess_upload(filepath)`: full pipeline, returns clean df + accurate counts
- Wired into `atlas_web_app.py` upload_count route before any existing processing
- Source-agnostic: works with xlsx upload now, Google Sheets via gsheet_fetcher.py later (both produce a DataFrame, preprocessor takes a DataFrame)
- Fixes open item #1 (optic undercount) at the data layer, not in SQL
- STATUS_MAP starts as hardcoded dict from Claude in Excel output, later becomes per-site JSON config

### Verified claims from LLM responses
- SN5610 exclusively uses OSFP-800G-2DR4: CONFIRMED (28,404 entries, all SN5610)
- Status breakdown percentages: CONFIRMED within rounding (10-107 row variance from header grouping)
- Total optic count: OFF by 190 due to COALESCE (will be fixed by preprocessor)
