# Terminal Notes

## 2026-04-22

### atlas_postgres_context.py — Optic summary query fix
Replaced the `UNION ALL` subquery in `build_postgres_context_for_general` (line ~184) with a single-pass `COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, ''))` query. The old approach double-counted cables where both sides carried the same optic type. Parameter tuple reduced from 6 elements `(site_id, uid, uid, site_id, uid, uid)` to 3 `(site_id, uid, uid)`. Matches the `optic_count` template pattern already used in `atlas_query_router.py`.

### atlas_web_app.py — Monotonic clock for Postgres cache
Changed `_pg_cache["ts"]` timing in `_check_postgres` from `time.time()` to `time.monotonic()`. Matches `atlas_data_loader.check_postgres` which already used monotonic. Wall clock time is still used correctly elsewhere in the file (TTL/timestamp audit purposes).

### demo_web_app.py — Thread safety and monotonic clock
Six changes to bring `demo_web_app.py` in line with `atlas_web_app.py`:
1. Added `import threading`
2. Added `_state_lock = threading.Lock()` after shared dict declarations
3. Wrapped `_check_rate_limit` body with `with _state_lock:` — prevents iteration-during-mutation on `_RATE_LIMIT_STORE` under concurrent requests
4. Wrapped `_evict_stale_contexts` body with `with _state_lock:`
5. Wrapped `_audit` body with `with _state_lock:`
6. Changed `_check_postgres` to use `time.monotonic()`
7. Wrapped all `USER_CONTEXT` and `USER_SITE` reads/writes in route handlers with narrow `with _state_lock:` — lock not held across slow I/O (file parsing, Postgres calls)

### diagnose_live.py — Remove broken file_hash check
Removed a dead code pattern around line 144 where `cur.execute()` was called inside a list comprehension expecting it to return rows — psycopg2's `execute()` returns `None`. Replaced the entire broken `if` block (which always evaluated the false branch and re-ran the query anyway) with a single unconditional `execute()` + `fetchall()` pair.

### atlas_web_app.py — Postgres-first upload_count context
Confirmed already implemented: `count_all_files_gui` runs first (user-visible output), then `_check_postgres()` gates context building — minimal stub `{"files": [...], "ts": ...}` when Postgres is available, full `build_sheet_context()` fallback otherwise. Eliminates `iterrows()` loops for the common case.

### docker-compose.yml — Four hardening changes
1. `DEMO_VERIFY_PIN` changed from `:-123456` default to `:?err` — `docker compose up` now fails fast if not set in `.env`
2. `restart: unless-stopped` added to web service
3. `shm_size: 256mb` added to db service
4. `env_file: .env` block removed from web service — environment block is now the single source of truth, eliminating the precedence footgun documented in CLAUDE.md

### helm/atlas — schema-configmap.yaml path verified
`files/atlas_schema.sql` path is correct. `helm/atlas/files/atlas_schema.sql` exists and `helm template` output confirms the ConfigMap data block is fully populated (not empty).

### helm/atlas/templates/web-service.yaml — Session affinity
Added `sessionAffinity: ClientIP` with `timeoutSeconds: 3600` to the web Service spec. No-op at 1 replica but future-proofs for scale-out. Verified correct placement on web service only via `helm template` output.

### demo_auth_ai.py — System prompt rework
Rewrote all three system prompts in `_build_grounded_messages` (POSTGRES, RACK_ANALYZER, default paths). Killed forced "Summary / Key Findings" section structure. New prompts: answer directly in first sentence, tables only for 5+ item comparisons, no section headers unless user asks for a report. Updated user message to request specific data citations instead of "evidence references."

### demo_auth_ai.py — Context compression (in-memory path)
`_build_legacy_trimmed_context`: capped optic_locations to top 10 per type, stripped evidence arrays, reduced max_locations from 20 to 10. `_trim_context_for_llm`: progressive token budget drops top_locations, then optic detail, then model locations when context exceeds ~8k words. `_build_normalized_context`: connection list capped at 500 with truncation note. Target: ~6k tokens down from ~19k.

### Define_Optic_Count.py + atlas_web_app.py — Single-pass sheet parse
New `count_and_build_context(files)` function parses xlsx once, returns both count text and context dict. `upload_count` route updated to call single-pass function. Postgres background ingest thread now starts in parallel with count instead of after it. Expected ~40-50% upload time reduction.

### .env — DB_PASSWORD alignment
Renamed `POSTGRES_PASSWORD` to `DB_PASSWORD` so docker-compose.yml picks it up. Compose uses `${DB_PASSWORD:-atlas}` but .env had the wrong var name, causing both services to silently use default password instead of `atlas_dev`.

### Optic undercount root cause — 11,284 rows with mismatched A/Z optics
COALESCE(a_optic, z_optic) picks A-side first, drops Z-side on mismatched rows. 23% of the sheet has different optic types on each side. Biggest pair: OSFP-800G-2DR4 (A) / QSFP112-400G-DR4 (Z) at 10,872 rows. QSFP112-400G-DR4 is Z-side only. Fix: count each side independently.

### STATUS column — 165 junk values are section headers
~200 rows have section labels (e.g. "CON-01 Grid C1", "DH202 :: C2") in the STATUS column. All have empty optic data. 6 real statuses cover 55,983 rows. Claude in Excel generated STATUS_MAP (171 mappings), OPTIC_SUMMARY (A:24,451 + Z:24,416 = 48,867 total), and CUTSHEET_CLEAN (headers stripped, canonical statuses).

### Next: cutsheet_preprocessor.py
Automated normalization at upload time. Applies STATUS_MAP, strips section headers, counts A/Z optics independently. Source-agnostic (works with xlsx now, Google Sheets later). Fixes optic undercount at data layer, not SQL. Full plan in CUTSHEET_CLEANUP_PLAN.md.
