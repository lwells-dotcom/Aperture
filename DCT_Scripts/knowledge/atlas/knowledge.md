# Atlas - Facts and Patterns

## Architecture
- Flask web app + Anthropic Claude API for grounded Q&A on datacenter cutsheet data
- Two frontends: Tkinter desktop GUI (Optic_Count_GUI.py), Flask web (demo_web_app.py)
- Auth: PIN verification -> HMAC-signed bearer token with TTL and scopes
- LLM calls use raw urllib (no SDK), supports Anthropic (primary) and OpenAI (fallback)

## Data Pipeline
- Raw cutsheet Excel/CSV -> cutsheet_normalizer.py -> build_llm_context() -> JSON context in LLM prompt
- build_llm_context() is the critical bottleneck: any field not aggregated here is invisible to the LLM
- Status counts (LLDP Passed/Failed, Cable Is Ran, Human Verified) were missing from context until 2026-03-28 fix
- Connection cache (_CONNECTION_CACHE) enables instant per-device lookups after upload

## Cutsheet Profile System (added 2026-03-29, hardened 2026-04-19)
- cutsheet_profiles.py defines canonical column names (Canon class) and profile mappings
- CutsheetProfile class: column mapping, status normalization, model aliases, fingerprint detection
- Three built-in profiles: standard_v1 (Quincy AND Ellendale -- same column format), standard_v2 (space-separated fallback), alternate (catch-all for odd naming)
- canonicalize() runs: detect profile -> rename columns -> normalize STATUS values -> normalize MODEL values
- Integrated into both atlas_data_loader.py (Postgres path) and cutsheet_normalizer.py (in-memory path)
- Status normalization handles variants: "LLDP: Passed" vs "LLDP Passed", "Cable Is Ran: Complete" vs "Cable Is Ran Complete"
- Model aliases expanded: NOKIA-*, NVIDIA-*, MELLANOX-* prefixed variants all resolve to base model
- Model normalization now does fuzzy matching: strips revision suffixes (-revB, -v2, etc.) and retries alias lookup, so "SN5610-revB" resolves to "SN5610"
- New profiles can be added by defining a CutsheetProfile and appending to PROFILE_REGISTRY
- Profile used per upload can be serialized via profile_to_dict() for audit trail
- detect_profile() now returns (profile, score) tuple; score is 0.0-1.0 fingerprint match fraction
- Duplicate source columns (e.g. both A-SIDE-DNS-NAME and A-SIDE DEVICE NAME) are now compared row-by-row; conflicts are logged with row index and sample values instead of silently dropped
- ROW:TYPE now maps to Canon.HOST_ROW_TYPE (separate from Canon.HOST_ROLE) to prevent physical placement metadata from polluting functional role queries

## Postgres Pipeline (added 2026-03-29, hardened 2026-04-19)
- Upload now writes to Postgres via atlas_data_loader.load_file() in addition to in-memory context
- QA path: question -> atlas_query_router.classify_question() -> SQL template -> Postgres -> compact result set -> LLM
- 13 question types: optic_count, device_list, device_detail, device_connections, connection_status,
  cable_status, section_summary, lldp_failures, site_overview, data_hall_summary, ip_lookup, node_compute, general
- General questions get a composite context (device summary + status counts + optic summary) instead of full dump
- demo_auth_ai._trim_context_for_llm() priority: Postgres context > normalized cutsheet > legacy summary
- Frontend shows context source badge (POSTGRES vs IN-MEMORY) and token estimates per query
- Graceful degradation: if Postgres is unreachable, falls back to in-memory pandas path automatically
- Missing required canonical columns after canonicalization now raises ValueError (hard fail, not warning)
- Section header derivation now requires positive match (topology name patterns like TIER/SPINE/LEAF/FDP/CDU) in addition to negative match (not a known status). Rejected candidates logged for pattern expansion.
- Sheet selection uses two-pass approach: name match then heuristic scan, both require post-heuristic schema verification (optic + device/port columns must be present)
- cutsheet_connections has unique indexes: (upload_id, cable_id) where cable_id non-empty, and (upload_id, a_device, a_port, z_device, z_port) for rows without cable_id. Duplicates deduped via ON CONFLICT DO NOTHING.
- host_inventory now has row_type column (physical placement from ROW:TYPE), separate from role column (functional role)

## Cutsheet Structure (Quincy - US-SLO01)
- ~4,300 rows, 53 topology sections
- Device models: SN4700 (1226), SN2201 (1133), SN3700 (968), OM2216-C14 (245), CM8148 (89), 7750-SR-1SE (19), PA-1420 (11), SN3420 (13)
- Primary optics: QSFP28-100G-DR1 (2382), QSFPDD-400G-DR4 (1821)
- Statuses: LLDP Passed (2953), Cable Is Ran Complete (483), LLDP Failed (255), Human Verified (14)
- IP columns: 12 VRF columns (IPv4/IPv6 pairs for DEFAULT-10, MGMT-20, IPMI-30, TSS-40, PUB-90, TNT)

## Cutsheet Structure (Ellendale - US-LZL01, analyzed 2026-04-01)
- 56,183 total rows -> 48,315 data rows, 199 section headers, 7,669 blank rows
- Uses V1 column format (identical headers to Quincy despite being a different site)
- 165 topology sections (3x Quincy) across 2 data halls (DH202, DH204)
- Device models: GPU-GB300-02 (5184), GB300-NVLINK-SW (2592), PS-1RU-03 (2304), SN5610 (1592), SN2201 (848), CDU-4RU-03 (288), STOR-COLD-02 (210), SN3700 (139), plus 12+ more
- Statuses: Cable Not Run (29929), Cable Is Ran Complete (14655), Cable Is Ran Not Terminated (2486), Addition (1233), empty (982), Human Verified (1)
- New status variants: "Cable Not Run: Priority", "Addition", "No Label & Not Yet Run"
- New network models: PTX10002-36QDD (Juniper), NGFW-4245 (FortiGate)
- Heavy infrastructure hardware on Z-side: GPU-GB300-02, GB300-NVLINK-SW, CPU-GP2-*, CDU-4RU-03, STOR-COLD-*, NET-6X100G-*
- 6 extra columns not in Quincy: A/Z-BREAKOUT LOC:CAB:RU, A/Z-BREAKOUT SLOT:PORT, A/Z-PATCH-PANEL LOC:CAB:RU:PORT
- Breakout data populated in 21,688 rows (significant for query coverage)
- normalize_cutsheet() processes all 56k rows in ~4.3 seconds (in-memory pandas path)

## Z-Side Device Data Gap (discovered 2026-04-07, partially addressed 2026-04-19)
- Z-side fields (z_dns, z_model, z_loc, z_port, z_optic) are captured by cutsheet_normalizer._build_connection() and stored in Postgres cutsheet_connections table
- build_llm_context() aggregates devices by model only, discarding A-side vs Z-side distinction entirely
- a_role and z_role columns now exist in cutsheet_connections (backfilled from host_inventory after load)
- host_inventory.role now contains only functional roles (FDP, switch, CDU, TOR etc.); physical placement (ROW:TYPE) stored separately in host_inventory.row_type
- Postgres query path is partially better: atlas_query_router has z_device_list and device_connections SQL templates
- Remaining gap: build_llm_context() still doesn't group devices by A/Z side for the in-memory path
- Net effect: Postgres queries can now answer role-based Z-side questions, but in-memory path still can't

## Excel Performance Layer (added 2026-04-19)
- Large cutsheets (Ellendale: 49,860 rows, 19MB, 14 tabs) caused 60+ second upload times
- Root cause 1: `pd.read_excel(input_file, sheet_name=None)` loaded ALL tabs including 9 junk/backup tabs
- Root cause 2: Same file parsed 3-4 times per upload (count_all_files_gui + build_sheet_context each independently open and read)
- Root cause 3: Row-by-row `iterrows()` on 50k rows
- Fix 1: `_should_skip_sheet()` regex filter skips backup/copy/legend/overhead/SheetN tabs. Applied to all sheet-iterating functions.
- Fix 2: `_cached_excel_file()` and `_cached_read_sheet()` module-level cache. ExcelFile opened once, DataFrames parsed once per (file, sheet, header) combo. Returns `.copy()` to prevent mutation. `clear_excel_cache()` called in finally block of upload endpoint.
- Fix 3: Gunicorn timeout bumped 60s -> 180s in Dockerfile
- Net effect: Upload parse drops from ~60s to ~15-20s for the Ellendale file
- Frontend now shows elapsed timer ("Processing... 12s") instead of static "Processing..."
- All `pd.ExcelFile()` and `pd.read_excel()` calls in Define_Optic_Count.py go through cache (26 call sites replaced)
- Zero `sheet_name=None` calls remain anywhere in the project

## Helm/Kind Deploy Gotchas (confirmed 2026-04-19)
- values-local.yaml: demoTokenSecret must be a real hex string, NOT `$(openssl rand ...)` -- Helm does not expand shell commands
- After `kubectl rollout restart`, port-forward dies. Must restart it manually.
- `kind load docker-image` required after every `docker build` -- Kind nodes have their own image store separate from Docker Desktop
- Pod labels from helm chart use `app.kubernetes.io/name`, not bare `app=atlas-web`. Use pod name directly for `kubectl logs`.

## Docker Stack
- Postgres 16 on host port 9000 (5432 occupied on dev machine)
- Flask on host port 5050
- Schema auto-initializes via initdb.d mount
- Multi-stage Dockerfile per project conventions

## Query Router (refactored 2026-04-19)
- atlas_query_router.py is now a thin facade: imports classify from query_intent, extractors from query_extractors
- query_lexicon.py: frozen keyword sets (COUNT_WORDS, STATUS_WORDS, OPTIC_WORDS, etc.) shared by all routers
- query_extractors.py: focused regex extractors (device, location, optic, section, model, role/side, IP). Run once per question via QuestionContext.
- query_intent.py: 15 domain routers in priority order, each returns IntentResult or None. QuestionContext dataclass pre-computes all tokens and extractor results.
- query_debug.py: debug_classify() returns full audit trail (question_type, confidence, reason, matched_domain, matched_signals, extractor results)
- 29 question types total; 99/99 parity test passing (80 original + 19 new type cases)
- route_question() now logs classification confidence and reason, and returns confidence/domain/reason in response dict
- Domain router priority: diff > cross_site > burndown > lldp > role > location > optic > status > section > trend > data_hall > site > node_compute > device > ip > general
- Old _PATTERNS regex list and inline extractors fully removed from atlas_query_router.py
- Trend deferral pattern: status/lldp/section routers defer to trend when trend words (trend/progression/progressed/trajectory/etc.) detected
- Cross-site queries join across all sites with is_active=TRUE (no single site_id filter)

## New Query Types (added 2026-04-19)
- upload_diff: CTE-based diff comparing two uploads by (a_device, a_port, z_device, z_port) composite key. Categories: removed, added, status_changed, optic_changed.
- upload_list: Lists all uploads for a site ordered by created_at DESC.
- cross_site_models: Model distribution across all active sites with connection counts.
- cross_site_optics: Optic inventory across sites with status breakdown (in_service, failed, pending).
- cross_site_status: Connection status_normalized breakdown per site with percentages.
- trend_status: Per-upload status counts with completion % ordered by created_at. Includes delta indicators and trajectory summary.
- trend_section: Per-upload per-section progression with optional section name filter.
- query_extractors.py additions: extract_upload_ids() (regex patterns for "upload N vs M" etc.), extract_site_codes() (US-XXX## format + known abbreviations).
- query_lexicon.py additions: DIFF_WORDS, UPLOAD_WORDS, CROSS_SITE_WORDS, TREND_WORDS frozensets.
- Safety regex in demo_auth_ai.py strips "ignore previous"/"system"/"assistant" globally, can distort legitimate sheet text (not yet addressed)

## Resilience (added 2026-03-28)
- tenacity for retry with exponential backoff on 429/5xx
- SIGALRM timeout guard (main thread only, skips in gunicorn workers)
- Automatic Anthropic -> OpenAI fallback chain
- TTL response cache keyed on question+context hash

## Rack Analyzer + Model Query Verification (confirmed 2026-04-20)
- Rack Analyzer is workbook-driven, not LLM-driven: `atlas_web_app.py` `/api/buildsheet` calls `build_sheet_processor.process_rack()` directly. No Anthropic/OpenAI call and no Postgres query in this path.
- `build_sheet_processor.process_rack()` originally built rack device lists from A-side-owned cables only; this caused incomplete rack contents when a rack device appeared only on the Z-side or only in `SITE-HOSTS`.
- Rack device assembly now scans both A and Z sides across all cutsheet rows and supplements with `SITE-HOSTS`, so physical rack inventory is fuller and more faithful to the workbook.
- Ellendale master files live in `/Users/lwells/Atlas/`, not repo root. Verified files:
  - `MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx`
  - `MASTER-US-CENTRAL-08B-US-LZL01-ELLENDALE.xlsx`
- `DH2` shorthand in Rack Analyzer is ambiguous for some rack numbers because multiple exact halls can share the same rack number:
  - `08A`: `dh202` and `dh204`
  - `08B`: `dh201` and `dh203`
- For rack `201`, a `DH2` query can legitimately collide across halls; the resolver now raises an ambiguity error instead of silently merging halls into one rack view.
- For rack `041` in `08A`, cross-hall cables to `dh201:041:33` are real workbook rows, not hallucinations:
  - `dh202:041:33 1/1/c13 -> dh201:041:33 1/1/c13`
  - `dh202:041:33 1/1/c14 -> dh201:041:33 1/1/c14`
- Rack Analyzer UX is clearer after relabeling:
  - `Devices Physically in Rack`
  - `Cables Staying Inside This Rack`
  - `Cables Leaving This Rack`
  - Summary now says `Total cables touching this rack`, `Staying inside rack`, `Leaving rack`
- Model-count questions had two separate bugs:
  - `How many SN2201s appear in the cutsheet?` routed to `model_search` but answered with distinct-device semantics, not raw appearances
  - `How many unique SN2201s appear in the cutsheet?` misrouted to `rack_summary`
- Root cause 1: `model_search` SQL grouped by `device_name, model` and capped results with `LIMIT 200`, so count answers could report truncated distinct-device results as if they were totals.
- Root cause 2: location intent rule treated `unique` as enough to trigger `rack_summary` even without `location/locations`, stealing `unique SN2201` questions from `model_search`.
- `query_intent.py` fix: `unique` now only triggers `rack_summary` when paired with explicit `location/locations`; `unique + model token` now routes to `model_search`.
- `atlas_query_router.py` fix: `model_search` now has three modes:
  - `raw_count` for questions like `How many SN2201s appear in the cutsheet?`
  - `unique_count` for questions like `How many unique SN2201s appear in the cutsheet?`
  - `list` for non-count model/device listing questions
- New raw-count formatter reports:
  - total cutsheet appearances
  - A-side appearances
  - Z-side appearances
  - unique devices represented in cutsheet
- New unique-count formatter reports:
  - total unique devices matching pattern
  - unique devices in cutsheet connections
  - unique devices in host inventory only
- Direct workbook verification showed why the old `200` answer was untrustworthy:
  - `08B`: `SN2201` raw occurrences = `25048`, unique device names = `842`
  - `08A`: `SN2201` raw occurrences = `25574`, unique device names = `847`
- Therefore:
  - old answer `200` for `How many SN2201s appear...` was wrong semantically and likely truncated
  - old answer `0` for `How many unique SN2201s appear...` was invalid because it came from `rack_summary`
- Regression coverage added:
  - `test_build_sheet_processor.py` for Z-side-only / `SITE-HOSTS` rack inventory behavior and ambiguous shorthand room handling
  - `test_model_search_semantics.py` for `unique SN2201` routing and raw-vs-unique count formatting
