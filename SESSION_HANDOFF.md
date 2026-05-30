# Session Handoff — 2026-05-30 (end of day)

Pick-up notes for a fresh Claude Code session. Auto-memory at
`~/.claude/projects/-Users-lwells-Atlas/memory/` already reflects the Atlas state
(MEMORY.md index + aperture-rebrand / lzl01-stress-test / atlas-run-ops). This file
adds detail + the North Pole investigation thread.

---

## ATLAS / APERTURE — DONE & SHIPPED ✅

**Repo renamed `Atllas_Coreweave` → `Aperture`**: https://github.com/lwells-dotcom/Aperture
(old URL auto-redirects). Local repo: `/Users/lwells/Atlas`, default branch `main`,
everything merged (PR #3 audit + **PR #4** mirror/multi-file). Plain `git clone` gets it all.

**App source:** `DCT_Scripts/Optic_Count/` (NOT repo root).
- Run: from that dir, `docker compose up -d --build` → web **:5050**, db **:9000** (db `atlas`/user `atlas`).
- **Auth is now Flask session-based** (no PIN/bearer) — first request sets a `user_id` cookie; reuse a cookie jar across upload→ask.
- Dockerfile moved to `docker/Dockerfile` (multi-stage, compiles Cython `cutsheet_normalizer_fast.pyx`); gunicorn entry `atlas_web_app:application`. `BASE_PATH` unset → standalone (no prefix, SameSite=Lax). Verify Cython: `docker compose exec web python -c "import cutsheet_normalizer as c; print(c._FAST_PROCESS_ROWS)"`.
- `.env` is gitignored — each user copies `.env.example` (needs ANTHROPIC_API_KEY, DB_PASSWORD, DEMO_VERIFY_PIN, DEMO_TOKEN_SECRET to satisfy compose `:?` guards even though session auth doesn't use the PIN).

**What this session did (all merged):**
1. **Full canvas mirror** (`/Users/lwells/canvas/apps/dc-operations/atlas`, branch `feat/atlas/ask-ai-and-cython`) → local. Brought session auth, Cython, IB+RoCE analyzers, 4-tab UI, hybrid `/api/ask` (meta + low-confidence demote), BASE_PATH iframe support.
2. **Re-skinned GUI to "Aperture"** (display text only — title/header/Ask buttons). Internal names stay `atlas` (DB, `ATLAS_*` env, `/api` routes, module files) to match the canvas target.
3. **Stress test**: 4 US-LZL01 cutsheets → **one upload, 300,138 connections + 27,604 hosts**. Query battery sub-second; HTTP upload→ask→LLM verified.
4. **R41 fix** (`atlas_data_loader.load_file`): `ANALYZE cutsheet_connections; ANALYZE host_inventory;` after bulk load, before `backfill_device_roles`. Stale `execute_values` stats forced a pathological join plan — full load **355s → 12s** (backfill 349s → 4s). See `DCT_Scripts/knowledge/atlas/rules.md` R41.
5. **Row-counter fix** (`load_cutsheet`): was reading only the last `execute_values` page's count (~111); now counts actual upload rows.
6. **Multi-cutsheet GUI upload**: `load_files()` loads N sheets into ONE `upload_id` (required — `build_postgres_context` defaults `upload_id=None` to the *latest* upload, so sheets must share one upload to be queried together). `/api/upload-count` takes `getlist("file")` + optional `site_code` form field. GUI: multi-file picker + Site code field.

**Current live state:** stack up, 4 cutsheets loaded as `US-LZL01` upload_id=1 (300,138 conns).
Cutsheet files: `/Users/lwells/Downloads/ATLAS-CUTSHEETS/` (Atlas-08a_copy, Atlas-08b, 08A-ROCE-CUTSHEETS, "08B ROCE-CUTSHEETS").
Pre-mirror backup: `/Users/lwells/optic_count_premirror_backup_20260530_115354`.

**pytest** (from `DCT_Scripts/`, `PYTHONPATH=Optic_Count Optic_Count/.venv/bin/python -m pytest test_*.py`):
31 pass, 5 pre-existing fails (`test_location_rack_routing` ×3, `test_model_search_semantics` ×2), 1 host-only collection error (`test_rack_context_bridge` needs full web deps absent from host `.venv`; imports fine in container). No mirror regressions.

**Open / optional (Atlas):**
- `_extract_site_code` returns `UNKNOWN` for real cutsheets (no SITE-VARS/locode). Worked around by the GUI Site-code field; could auto-derive. NOT done.
- `load_files` has no duplicate-file-hash skip (re-uploading a batch reloads/replaces). Acceptable.
- Port the **R41 fix + multi-file feature** back into the canvas repo — blocked on user's canvas permissions.

---

## NORTH POLE thread — INVESTIGATION ONLY, PREMISE WAS WRONG ⚠️

User started a task to make a "North Pole data center" resemble real GB300 racks, then
said **"I gave you the wrong information"** and pulled back. **Re-confirm the premise before
acting.** No changes were made (read-only investigation). Facts discovered (accurate regardless):

- **"North Pole" = a Snipe-IT location**, not Atlas. Instance: `snipe-it-recovered` (container `snipe-it-recovered-app-1` on **:8088**, db `snipe-it-recovered-db-1`). DB client is **`mariadb`** (not `mysql`); creds via `docker exec snipe-it-recovered-app-1 printenv DB_USERNAME DB_PASSWORD DB_DATABASE` (db=`snipeit`).
- Location **`US-ARCTIC-NORTH_POLE_01 - DH1`** (id 49): **57,600 assets, all model `GB300`** ("all the same part"), DH1 only. Real sites (e.g. `US-LZL01 - DH1`) have a *mix* (SN5610/MSN2201/MSN3700/OM2216/CM8148). Whole instance is bulk-seeded round numbers (807,601 assets total).
- **Packing slip** `/Users/lwells/BOL_PACKINGSLIPBUILDER/packing_slip_F6NP1H4.html` = one GB300 rack BOM: **18 GPU-GB300-02 + 9 GB300-NVLINK-SW + 8 PS-1RU-03 + 2 MSN2201 + 1 CDU-4RU-03 = 38 assets**, rack serial **F6NP1H4**, per-asset `d######` tags. Only ONE packing slip exists.
- Snipe-IT `assets` table has custom fields: **`_snipeit_rack_serial_3`** (rack serial), `_snipeit_asset_location_2` (OU/position), `_snipeit_cw_region_4`, `_snipeit_cw_locode_5`. No schema change needed for rack serials.
- Only models `GB300`(id 6) + `MSN2201-CB2FC`(id 2) exist; the other 4 GB300-rack component SKUs would need creating.
- Interesting: the **Atlas** `host_inventory` already models a real GB300 rack perfectly (rack `dh202:003` = exactly 18/9/8/2/1 = 38, RU positions aligned with the slip) — just lacks serials. Could be a reference for the BOM/positions.

**Unanswered (the questions I asked got "wrong info" answers — disregard them):** which system/location is really the target, what the racks should be, how many, how to treat the 57,600.
