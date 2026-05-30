# Atlas Terminal Work Log
Merged into Obsidian vault periodically. Each entry captures work done in a Claude Code terminal session.

---

## 2026-05-30 - Multi-cutsheet GUI upload + counter/site-code fixes

Session context: Follow-up to the canvas mirror + stress test. User needs the GUI to accept multiple cutsheets (their case: 4 US-LZL01 sheets) and query them together, plus button up the two findings from the stress test.

What changed:
- atlas_data_loader.py: fixed `load_cutsheet` row counter (was reading only the last execute_values page → reported ~111/237; now `SELECT count(*) WHERE upload_id`). Added `load_files(file_paths, site_code, uploaded_by)` that loads MULTIPLE cutsheets into ONE upload_id (deactivate prior once, ANALYZE+backfill+refresh once) + `_read_cutsheet_frames` helper. This is required because build_postgres_context defaults upload_id=None to the LATEST upload — so multiple sheets must share one upload_id to be queried together. Added `deactivate_prior` flag to load_file.
- atlas_web_app.py: `/api/upload-count` now accepts `request.files.getlist("file")` (multiple) + optional `site_code` form field (fixes the all-files-resolve-to-UNKNOWN collision). New `_run_postgres_batch_job` calls load_files; session's USER_SITE points at the one combined upload so /api/ask sees all sheets. GUI HTML: `multiple` on the file input + a "Site code" text field; JS `_buildCountForm()` sends all files + site_code (used by both Count and Count-by-status).

What's different now:
GUI users can select 4 cutsheets at once, label them (e.g. US-LZL01), and ask questions across all of them. Verified: 4 files → 1 upload, 300,138 connections + 27,604 hosts, /api/ask returns "300,138 total connections … 12,419 devices, 236 sections" and aggregated optic types spanning master+RoCE. Verified master/RoCE are disjoint (0 shared connection keys) so combining doesn't double-count.

Minor behavior change: the multi-file path (load_files) does not have load_file's duplicate-file-hash skip, so re-uploading an identical batch reloads it (replaces the prior, since deactivate runs first). Acceptable for the GUI replace-site workflow.

Next up: commit the mirror + these fixes (still uncommitted on fix/atlas-audit-security-routing).

---

## 2026-05-30 - Canvas full-mirror + US-LZL01 stress test (300K rows) + backfill perf fix

Session context: Bring local Optic_Count app up to parity with the canvas monorepo version (`/Users/lwells/canvas/apps/dc-operations/atlas`, branch feat/atlas/ask-ai-and-cython), then stress-test with 4 US-LZL01 cutsheets. Full mirror chosen (session auth, Atlas branding, Cython fast path, IB/RoCE analyzers, hybrid /api/ask routing, BASE_PATH iframe support).

What changed:
Mirrored canvas → local via rsync (excluded .env, .canvas, .omc, __pycache__, *.so). Adopted canvas multi-stage Dockerfile (Cython builder, entry `atlas_web_app:application`) + docker-compose. Rebuilt stack; verified Cython `.so` loads, /api/health ok, standalone routing (BASE_PATH unset → no PrefixMiddleware / SameSite=Lax). Loaded all 4 cutsheets via atlas_data_loader.load_file under distinct site codes (US-LZL01-08A/08B + -ROCE each) because all 4 extract to site_code=UNKNOWN and would otherwise deactivate each other. Final DB: 300,138 connections, 27,604 hosts.
Found + fixed a major ingest perf bug: backfill_device_roles took 348.7s on 08B (vs 24.6s total for the larger 08A) because execute_values leaves stale planner stats, forcing a pathological join plan. Added `ANALYZE cutsheet_connections; ANALYZE host_inventory;` in load_file right after the bulk-load commit, before backfill. Re-verified: 08B load 355s → 12s (backfill 348.7s → 3.8s). atlas_data_loader.py only.

What's different now:
Local is a faithful replica of the deployed canvas app and ingests at production scale (~300K rows) without the multi-minute backfill stall. Query battery (12 types) returns sub-second; HTTP session→upload→ask→LLM path returns grounded answers; RoCE analyzer works (loc dh202:003:37 → 16). pytest: 31 passed, 5 pre-existing failures (test_location_rack_routing ×3, test_model_search_semantics ×2), 1 host-only collection error (test_rack_context_bridge needs full web deps absent from host .venv; imports fine in container).

Open findings (not yet fixed): (1) load_file return `connections_loaded` and the "duplicate rows skipped" log badly under-report actual rows (e.g. reported 111/237 vs 174K/42K loaded) — cosmetic but misleading. (2) Real cutsheets carry no detectable site code → load as UNKNOWN and collide via the per-site is_active soft-delete; HTTP upload path has no site_code override.

Next up: decide where the mirror lands in git (currently uncommitted on branch fix/atlas-audit-security-routing) and whether to fix the connections_loaded counter + UNKNOWN site-code handling.

---

## 2026-04-20 - Routing hardening for human phrasing, rack/location precision, and model-count semantics

Session context: Senior-architect review of whether the Flask + Postgres routing path can answer real human questions from the Ellendale cutsheets. Focus was on natural phrasing users actually type (`SN2201s`, `human verified cables`, `rack 41`, `dh2 041`) and on preventing the SQL layer from returning technically valid but misleading results.

What changed:
Reviewed the real Ellendale workbook at `/Users/lwells/Atlas/MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx` to ground the review against actual fields (`STATUS`, `A-LOC:CAB:RU`, `A-SIDE-DNS-NAME`, `A-MODEL`, `A-PORT`, `A-OPTIC`, `Z-LOC:CAB:RU`, `Z-SIDE-DNS-NAME`, `Z-MODEL`, `Z-PORT`, `Z-OPTIC`, `CABLE`, plus `SITE-HOSTS` fields like `LOC:CAB:RU`, `DNS-A-RECORD`, `NETBOX MODEL`, `ROLE`). Verified top Ellendale status counts (`Cable Not Run` 29,822, `Cable Is Ran: Complete` 14,655, `Cable Is Ran: Not Terminated` 2,486, `Addition` 1,233) and model counts (`SN5610` 34,468, `SN2201` 25,574, `GPU-GB300-02` 20,736, `SN3700` 3,206).

**query_extractors.py** — `extract_location()` now preserves rack-scoped forms: exact `LOC:CAB:RU` (`dh202:041:10`), rack tokens without RU (`dh202:041`), and spaced shorthand (`dh2 041` -> `dh2:041`). This prevents `rack dh202:041` from collapsing to just `dh202`. `extract_model()` now singularizes plural hardware phrasing when the token ends in `digit+s`, so `SN2201s` and `SN5610s` normalize to `SN2201` / `SN5610` before SQL params are built.

**query_intent.py** — Verification semantics now win earlier in `route_status_intent()`. Phrases with `passed`, `verified`, or `unverified` route to `connection_status` even when the user says `cables`, so `How many human verified cables are there?` no longer lands in `cable_status`.

**atlas_query_router.py** — `rack_summary` was rebuilt to operate at actual rack level instead of full `LOC:CAB:RU`, and it now unions both A-side and Z-side endpoints. It computes true rack totals via `COUNT(DISTINCT rack_loc)` and site-wide unique connection totals via `COUNT(DISTINCT connection_key)`, then returns top-50 racks with the real total counts attached. Formatter now prints `Total racks: N` with a `(showing top 50...)` note when truncated instead of pretending row count equals site total. `location_lookup` SQL now projects the side that actually matched the location pattern instead of always returning the A-side columns; Z-side matches now return `z_device`, `z_model`, `z_port`, and `z_loc_cab_ru` correctly. Added `_build_location_pattern()` helper for scoped lookup patterns:
- exact `dh202:041:10` -> exact match
- rack `dh202:041` -> `dh202%:041:%`
- shorthand `dh2:041` -> `dh2%:041:%`
- bare rack number `41` now returns no SQL pattern
Added an early `route_question()` guard for `location_lookup` when the extracted location is too broad. Questions like `What devices are in rack 41?` now return an actionable low-confidence prompt asking for `dh202:041`, `dh2 041`, or full `dh202:041:10` instead of silently blending every `041` rack across the site.

Also fixed prior routing/semantics gaps from the same review cycle:
- `model_search` now has `raw_count`, `unique_count`, and `list` modes.
- `How many SN2201s appear in the cutsheet?` now means raw cutsheet appearances, not capped distinct-device rows.
- `How many unique SN2201s appear in the cutsheet?` now routes to `model_search` instead of `rack_summary`.
- `upload_diff` now requires both upload IDs before SQL executes; partial or missing IDs return guidance instead of empty diffs.
- `connection_status` and `cable_status` are now meaningfully different: `complete` removed from `connection_status`, retained on the cable-run side.
- `build_postgres_context()` now preserves `confidence`/`classification_reason` through the `general` composite context path.
- Postgres prompt path in `demo_auth_ai.py` now treats SQL context as trusted preformatted text, and the `context` key no longer gets mangled by injection sanitization.

**atlas_web_app.py** — Rack Analyzer wording was clarified to reduce mistrust when racks have cross-rack or cross-hall links:
- `Devices Physically in Rack`
- `Cables Staying Inside This Rack`
- `Cables Leaving This Rack`
Summary line now reads `Total cables touching this rack`, `Staying inside rack`, `Leaving rack`.

Tests added and run:
- `test_build_sheet_processor.py` for Z-side-only / `SITE-HOSTS` rack inventory behavior and shorthand hall ambiguity
- `test_model_search_semantics.py` for `unique SN2201` routing and raw-vs-unique model counts
- `test_query_followups.py` for upload-diff ID guarding, `general` confidence passthrough, and `complete` removal from `connection_status`
- `test_location_rack_routing.py` for rack-level summary, both-side rack aggregation, and precise location patterns
- `test_human_phrasing_routing.py` for plural model names, verification-vs-cable routing, and bare-rack ambiguity handling
Also ran `python3 -m py_compile` against all touched router/context/extractor files after each patch set.

What's different now:
The routing layer is significantly more aligned with how humans actually ask questions about these sheets. `SN2201s` no longer misses singular model values in SQL. Verification questions no longer flip domains based on whether the user says `cables` or `connections`. Rack summaries count both sides and report real rack totals instead of top-50 rows as if they were the full site. Location lookups either answer a specific rack/device question precisely or ask for a more specific hall-qualified rack instead of returning blended site-wide results.

Next up:
Run the rebuilt image in the Kind/Helm environment and re-test the natural-language prompts in the live app: `How many SN2201s appear in the cutsheet?`, `How many unique SN2201s appear in the cutsheet?`, `How many human verified cables are there?`, `What devices are in rack dh202:041?`, and `What devices are in rack 41?`. If users still want bare rack numbers to work automatically, the next design decision is whether to fan out across halls intentionally or ask a disambiguation question in the UI.

---

## 2026-04-20 - Flask/SQL routing false positives + LLM output quality fixes (7 bugs)

Session context: Debugging why SQL queries route to wrong templates and why LLM answers are poor even when test questions appear to work. Code audit across query_intent.py, atlas_query_router.py, atlas_postgres_context.py, demo_auth_ai.py.

What changed:
**query_intent.py** — Added `_STRONG_DIFF_SIGNALS` frozenset (`compare`, `diff`, `delta`, `versus`, `vs`, `difference`, `modified`). `route_diff_intent` now requires a strong diff signal for the `upload_diff` path instead of firing on generic DIFF_WORDS tokens like `"missing"`, `"new"`, `"added"` combined with UPLOAD_WORDS like `"last"`, `"latest"`, `"recent"`. Added a third condition for weak diff words + explicit temporal scoping (`between`, `since`, `from`, `two`). Prevented false positives like "What connections are missing from the last upload?" routing to `upload_diff`.

**atlas_query_router.py** — Added early-exit guard in `route_question()`: when `qtype == "upload_diff"` and `extract_upload_ids()` returns `(None, None)`, return an actionable guidance message instead of executing SQL that silently produces empty results (`= NULL` is never TRUE in Postgres). Message tells user to specify upload IDs or run "list uploads". Updated `connection_status` SQL to filter by `status_normalized IN ('lldp_passed', 'lldp_failed', 'human_verified', 'complete')`. Updated `cable_status` SQL to filter by `status_normalized IN ('not_run', 'not_terminated', 'complete', 'in_progress', 'addition', 'pending')`. Updated formatter for both types to show totals with percentages and normalized labels — the two types were previously identical SQL returning mixed statuses.

**atlas_postgres_context.py** — `build_postgres_context()` now includes `[confidence: medium/low]` tag in context header for non-high-confidence classifications, and returns `confidence` + `classification_reason` fields in the dict. Added routing: when `route_question()` classifies as `"general"`, now calls `build_postgres_context_for_general()` (which returns top-20 devices, full status breakdown, optic summary) instead of using the basic 3-metric `_SQL_TEMPLATES["general"]`. The richer function was previously dead code in the Q&A path.

**demo_auth_ai.py** — `_sanitize_context_dict()` now passes the `"context"` key through unmodified. Previously it applied `_PROMPT_INJECT_RE` substitutions to generated SQL output, which could corrupt device names or section labels containing matched patterns. `_build_grounded_messages()` now detects `source == "POSTGRES"` and uses a Postgres-aware system prompt that tells the LLM the `context` key is pre-formatted plain-text query results (not structured JSON fields). Low-confidence classifications add a hedge instruction. In-memory path retains original system prompt and device connection injection logic.

What's different now:
Questions like "show missing cables from the last upload", "any new devices in the recent cutsheet" no longer misroute to `upload_diff`. Asking for an upload diff without specifying IDs returns a clear "specify upload IDs" message. General questions get the full composite context (devices + statuses + optics) instead of 3 bare metrics. Cable vs connection status queries now return meaningfully different data subsets. The LLM gets a context-appropriate system prompt and no longer has SQL output corrupted by injection regex. Low-confidence matches now include a hedge signal in the prompt.

Next up:
Test the diff router with the parity suite — verify no regressions on legitimate `upload_diff` questions. Verify "general" questions hit `build_postgres_context_for_general()` in a live session. Consider adding confidence badge to the frontend UI next to the context source badge.

---

## 2026-04-19 - Query router new types, diff false positive fix, Postgres context fallback

Session context: Finishing integration of 7 new query types (upload_diff, upload_list, cross_site_models, cross_site_optics, cross_site_status, trend_status, trend_section) and hardening the classification pipeline.

What changed:
Integrated SQL templates, build_query_params cases, and format_results_for_llm handlers for all 7 new query types into atlas_query_router.py. Ran 99-question parity test suite and fixed 19 routing bugs across query_intent.py routers: added trend deferral checks to status/lldp/section routers so trend questions don't get eaten early; added optic word deferral in role router so "optics in SPINE section" routes correctly; fixed cross_site router to detect "by/per/each site" patterns and check COMPLETION_WORDS; fixed section router to check completion/fail words before "sections where" handler; added "overall status", "how many connections/cables" patterns to status router; added section + list words handler. Found and fixed a false positive in route_diff_intent where "across all uploads" in cross-site questions triggered upload_list. Replaced loose token matching with phrase-based regex plus scoping preposition guard. Fixed atlas_web_app.py "No sheet loaded" bug: root cause was /api/ask only checking in-memory USER_CONTEXT with no Postgres fallback, and /api/upload-count never writing to Postgres. Added USER_SITE dict, Postgres dual-write on upload via atlas_data_loader.load_file(), and full Postgres context fallback chain on /api/ask (matching demo_web_app.py pattern). Cross-validated user's DH2 rack 38 CSV exports (110/110 cables matched labels, zero discrepancies). Created deploy.md runbook in vault.

What's different now:
29 question types with 99/99 parity tests passing. Atlas web app now survives gunicorn worker restarts (Postgres context recovery). Upload endpoint writes to both in-memory dict and Postgres (non-fatal if PG down). Diff router no longer false-positives on "across all uploads" phrasing. Deploy runbook covers both Kind/Helm and docker-compose paths.

Next up:
Live deploy with the updated code. Run a full upload-to-question cycle in the Kind cluster to verify the Postgres dual-write and context fallback work end-to-end.

---

## 2026-04-15 - H1 token benchmark harness + three bug fixes

Session context: H1 hypothesis (Postgres templates reduce token usage 60-80% vs in-memory path)

What changed:
Created `DCT_Scripts/bench_token_usage.py`: runs 25 questions from the 100-question test suite through both the in-memory pandas path and the Postgres query router, measures context token count (chars/4), and outputs a side-by-side comparison report with per-question breakdown and summary stats by question type. Report saved to `token_benchmark_SITE.txt`. Fixed `atlas_data_loader.py` line 382: added `default=str` to `json.dumps()` for raw row storage — ELD's A-BREAKOUT SLOT:PORT column has `datetime.time` values that killed the load silently after the upload record was already committed (R33). Fixed `atlas_query_router.py` z_device_list and a_device_list SQL: `COUNT(DISTINCT col) OVER ()` inside GROUP BY raises `FeatureNotSupported` in Postgres; moved `total_unique` to the outer SELECT using `COUNT(*) OVER ()` (R32).

What's different now:
H1 confirmed: 89.3% average token reduction on ELD (9,063 in-memory vs 968 Postgres avg, 321 median, 72 min). Full results in `token_benchmark_ELD.txt`. Any cutsheet with Excel time/date values in breakout columns now loads correctly. ELD data (47,672 rows) is loaded in the running Postgres container.

Next up:
Wire Flask upload endpoint to call `atlas_data_loader.load_file()` automatically so Postgres is populated on upload without a manual step.

---

## 2026-04-15 - Google Sheets fetcher script + atlas-status slash skill

Session context: New data source integration (Sheets API) and team onboarding tooling.

What changed:
Created `Optic_Count/gsheet_fetcher.py`, a standalone CLI script that pulls cutsheet data from Google Sheets via the Sheets API (service account auth), auto-detects the CUTSHEET/CONNECTIONS tab, and feeds the resulting DataFrame through `cutsheet_normalizer.normalize_cutsheet()` for the in-memory path or `atlas_data_loader.load_file()` for the Postgres path. Supports multiple sheet/site pairs in one invocation (Quincy + Ellendale). Updated `.env.example` with `GOOGLE_SA_KEY_PATH`, `GSHEET_QCY_ID`, `GSHEET_ELD_ID` config vars. Added `google-auth` and `google-api-python-client` to `requirements.txt`. Also created `DCT_Scripts/.claude/skills/atlas-status/SKILL.md` slash skill for project onboarding with safety rails (no git, no rm -rf) and automatic terminal_work.md logging.

What's different now:
Cutsheet data can be pulled directly from live Google Sheets instead of requiring local Excel file downloads. Team members can run `/atlas-status` in Claude Code to get a project briefing without reading through all the knowledge files manually.

Next up:
Get the GCP service account key set up, share the Quincy and Ellendale sheets with the service account email, then run a live test against both sheets to validate normalization output matches the Excel-based results.

---

## 2026-04-15 - H8: Z-side role queries — a_role/z_role columns + role_lookup question type

Session context: H8 hypothesis — "Joining host_inventory roles to cutsheet_connections will unlock Z-side role queries"

What changed:
`atlas_schema.sql`: Added `a_role TEXT` and `z_role TEXT` columns to `cutsheet_connections`, partial B-tree indexes (WHERE NOT NULL), and ALTER TABLE migrations for existing deployments. `atlas_data_loader.py`: Added `backfill_device_roles(conn, upload_id, site_id)` that runs two UPDATE...FROM queries joining `cutsheet_connections` to `host_inventory` on `LOWER(TRIM(device)) = LOWER(TRIM(hostname))`, called in `load_file()` after `load_site_hosts()` when `hosts_loaded > 0`. `atlas_query_router.py`: Added `role_lookup` question type with patterns for FDP/CDU/PDU/TOR/spine/leaf with optional side filter, SQL template using CTEs + `MODE() WITHIN GROUP`, `_extract_role_and_side()` extractor, and a two-mode formatter (summary vs device-list). FDP removed from `device_list` — now routes to `role_lookup`. `cutsheet_normalizer.py`: `build_llm_context()` now computes `model_by_side` (a_only/z_only/both counts per model) from `seen_as` sets; `build_llm_context_from_prebuilt()` gets `model_by_side: {}` for key consistency.

What's different now:
"What FDPs are on the Z-side?" routes to `role_lookup`, queries `z_role ILIKE '%FDP%'` rows, and returns device names with models and connection counts instead of "data not available." In-memory path now surfaces which device models appear only on the Z-side via `model_by_side`.

Next up:
Run a real upload with a SITE-HOSTS tab that has a ROLE column and verify `roles_backfilled` in the response has > 0 rows. If SITE-HOSTS has no ROLE column, backfill silently produces 0 rows (non-fatal). Check host_inventory coverage for Quincy and Ellendale.

---

## 2026-04-15 - Helm chart for Kind deployment

Session context: Production deployment (COO approved). Atlas going to prod on K8s via Kind.

What changed:
Created `Optic_Count/helm/atlas/` with full Helm v2 chart. Chart.yaml, values.yaml defaulted for Kind (local image, pullPolicy: Never, standard storageClass). Templates: Postgres StatefulSet with PVC and health probes, Flask web Deployment (2 replicas, rolling update, init container waits for PG readiness), headless Postgres Service, ClusterIP web Service, K8s Secret for API keys/DB password/auth tokens, ConfigMap for non-sensitive env vars, schema-init ConfigMap that bundles atlas_schema.sql for Postgres initdb, uploads PVC, optional Ingress (disabled by default), and NOTES.txt with post-install and psql exec instructions. Postgres data stored on PVC via volumeClaimTemplate. Web pods auto-restart on config/secret changes via checksum annotations. Schema SQL bundled in `helm/atlas/files/`.

What's different now:
Atlas can be deployed to Kind with `docker build`, `kind load docker-image`, then `helm install`. Port-forward for access, kubectl exec into Postgres for user management. No remote registry needed.

Next up:
Morning deploy: build image, load into Kind, helm install, port-forward, verify /api/health and run a test upload + question.

---
