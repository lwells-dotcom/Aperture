# Atlas - Rules (confirmed, apply by default)

## R1: Always propagate new data fields through the full context pipeline
- Confirmed: STATUS field existed in raw data but build_llm_context() didn't surface it.
  LLM correctly reported "not available" because it genuinely wasn't in the context.
- Apply: When adding a new field to the normalizer output, verify it appears in
  build_llm_context() return dict AND in the prebuilt sheet path (build_llm_context_from_prebuilt).

## R2: Check threading context before using signal.SIGALRM
- Confirmed: SIGALRM crashes with "signal only works in main thread" under gunicorn.
- Apply: Always gate SIGALRM with `threading.current_thread() is threading.main_thread()`.

## R3: Set real defaults in docker-compose, not empty strings
- Confirmed: `${DEMO_VERIFY_PIN:-}` sets empty string, overriding Python os.getenv fallback.
- Apply: If the Python code has a meaningful default, mirror it in the compose file.

## R4: .env.example is not .env
- Confirmed: User added API key to .env.example, app couldn't find it.
- Apply: Always remind users to `cp .env.example .env` after setup.

## R5: Test decorator behavior in the actual runtime context
- Confirmed: SIGALRM worked in unit tests (main thread) but failed in production (worker thread).
- Apply: Test decorators under gunicorn/Flask, not just standalone Python.

## R6: Never hardcode status strings in SQL or views
- Confirmed: optic_inventory materialized view used exact match `status = 'LLDP: Passed'`.
  Quincy data used "LLDP Passed" (no colon), V2 used different strings entirely.
  Result: every optic showed 0 in-service until the filter was broadened to ILIKE patterns.
- Apply: Use ILIKE with '%keyword%' patterns in views, and normalize status values at
  ingestion time via cutsheet_profiles.normalize_status().

## R8: cutsheet_normalizer must use cutsheet_profiles as single source of truth
- Confirmed: cutsheet_normalizer.py had its own MODEL_ALIASES (2 entries) and _normalize_model()
  with opposite case logic (uppercase) vs cutsheet_profiles.normalize_model() (lowercase lookup).
  Result: "mellanox-sn4700" resolved to "SN4700" via profiles but "MELLANOX-SN4700" via normalizer.
  Column name lookups also diverged: normalizer used hard-coded "A-SIDE-DNS-NAME", "A-PORT" etc.
  while profiles canonicalized to Canon.A_DEVICE, Canon.A_PORT.
- Apply: All column resolution, model alias mapping, and status normalization must flow through
  cutsheet_profiles.canonicalize(). No duplicate alias dicts in other modules.

## R9: Every query router question type needs a targeted SQL template
- Confirmed: Questions falling through to "general" get site-level aggregates only.
  LLM correctly reports "data not available at this granularity." Adding a specific question
  type with a real SQL template (e.g., rack_summary, lldp_neighbor_mismatch) immediately
  produces precise answers.
- Apply: When the LLM says "missing data" for a question that should be answerable,
  check classify_question() first. If it returns "general", add a new question type.

## R10: Section header detection must exclude known real statuses
- Confirmed: Ellendale cutsheet has rows where STATUS = "Cable Is Ran: Complete" but A-LOC
  and A-DEVICE are both empty (incomplete data rows). _is_section_header_mask() was
  classifying these as section headers, causing "Cable Is Ran Complete" to appear in the
  sections list and pollute section-based queries.
- Apply: After identifying candidate section headers (STATUS filled, A-LOC and A-DEVICE empty),
  exclude any row whose STATUS value matches a known real status from STATUS_NORMALIZATION.

## R11: Real cutsheets across sites share column format (V1)
- Confirmed: Ellendale (US-LZL01) uses identical column names to Quincy (US-SLO01):
  A-SIDE-DNS-NAME, A-MODEL, A-LOC:CAB:RU, STATUS, CABLE, etc.
  The V2 profile (space-separated "A SIDE DEVICE", "INSTALL STATUS") was built from
  assumptions and does not match any real production sheet.
- Apply: Default to V1 profile for all new sites. Only create a new profile when actual
  column headers demonstrably differ. Don't build profiles from assumptions.

## R12: Python float NaN is truthy -- always guard normalize functions
- Confirmed: pandas reads empty CSV cells as float NaN. Python's `not float('nan')` is
  False (NaN is truthy). normalize_status(NaN) returned the string "nan" instead of "".
  This caused ~6,700 blank rows to be classified as section headers (STATUS="nan" passed
  the non-empty check), drowning out the real 199 headers.
- Apply: Every normalize_* function must explicitly check for NaN via math.isnan()
  before any string conversion. Also use .fillna("") BEFORE .astype(str) on DataFrame
  columns to prevent "nan" strings from propagating.

## R13: The Postgres data loader must derive sections, not just read them
- Confirmed: atlas_data_loader.load_cutsheet() read Canon.SECTION from the DataFrame,
  but no SECTION column exists in real cutsheets. Section info is embedded as header rows
  in the STATUS column. cutsheet_normalizer.py derived sections via forward-fill, but the
  Postgres path never did. Result: all 49k connections loaded with empty section strings.
- Apply: Both ingestion paths (in-memory normalizer AND Postgres data loader) must derive
  sections using the same header-detection + forward-fill logic. Keep them in sync.

## R14: Pattern order in classify_question() determines routing correctness
- Confirmed (2026-04-01): Baseline 100-question test showed 19/100 fell to "general" and
  14/100 routed to wrong types purely due to pattern ordering. Key ordering rules:
  - LLDP+fail patterns must come BEFORE generic LLDP+count patterns (otherwise "how many
    LLDP Failed" routes to connection_status instead of lldp_failures)
  - LLDP ratio/percentage → connection_status must come BEFORE LLDP+fail catch-all
  - "burndown" keyword → link_status must come BEFORE connection_status (which catches
    "link status" as a phrase)
  - Section patterns (sections where, tiers represented) must come BEFORE cable_status
  - Rack count "across data halls" → rack_summary must come BEFORE data_hall_summary
  - model_search patterns for device model inventory/sorted/most must come BEFORE device_list
- Apply: When adding new patterns, always consider what earlier pattern might steal the match.
  Run the 100-question test suite (test_classify_100.py) to validate any changes.
  Target: 0 general fallbacks, 95+ correct classifications.

## R15: Model pattern extraction must cover alphanumeric and digit-prefix formats
- Confirmed (2026-04-01): _extract_model_pattern() originally only matched [A-Z]{2,}-?\d{3,}
  (letters then 3+ digits). Real-world models include:
  - 7750-SR-1SE (digit-prefix, classify pattern also needed)
  - CPU-GP2-02 (letters-letters-digits with 2-digit suffix)
  - NET-6X100G-01 (alphanumeric segment in middle)
  - 1U-1N-GEN5-1NIC (digit-prefix with letter suffix)
  - PROLIANT-DL360-GEN10-PLUS (multi-segment, >3 hyphen groups)
- Apply: When a model_search question routes correctly but extraction returns empty or
  wrong value, add a new candidate regex to _extract_model_pattern(). Keep a stop list
  of false-positive terms (how-many, tell-me, etc.).

## R18: All device-list SQL templates must have LIMIT and a total_unique window function (confirmed 2026-04-02)
- Confirmed: z_device_list without LIMIT returned 13,838 rows for ELD = 69,208 context tokens,
  437,941 total input tokens. Answer correct but ~200x more expensive than necessary.
- Apply: Every query that returns a list of devices must include LIMIT 200 and use a window
  function (COUNT(DISTINCT col) OVER ()) to carry the true total through the result set.
  Formatter uses rows[0]['total_unique'] for the header count and adds "(showing top 200)"
  when truncation occurs.

## R16: Side-specific device queries need dedicated question types (confirmed 2026-04-02)
- Confirmed: device_list SQL template UNIONs a_device and z_device into a single flat list.
  Q32 ("unique devices on the Z-side") routed to device_list but the context had no way to
  split by side — the LLM correctly diagnosed this gap.
- Apply: Route any question containing "z-side"/"a-side" + "devices" to z_device_list or
  a_device_list (new types added 2026-04-02). These templates query only z_device or a_device
  columns respectively and include a "Side: Z-side only / A-side only" header in the formatted
  context so the LLM knows what it's looking at.

## R17: lldp_failures empty result needs an explicit message, not a generic "No results" (confirmed 2026-04-02)
- Confirmed: ELD has no LLDP statuses at all (install-phase site). lldp_failures returns 0 rows.
  The generic "No results found." message caused the LLM to fall back to in-memory
  connection_status context, producing a site-level aggregate instead of a clear "0 failures" answer.
- Apply: format_results_for_llm() must return a site-context-aware message for empty lldp_failures:
  "No LLDP: Failed connections found at this site. This site may use non-LLDP verification workflows."
  This anchors the LLM's answer without leaking unrelated context.

## R19: _extract_section_name() stop list must cover common English near "section" (confirmed 2026-04-03)
- Confirmed: Q24, Q25, Q27 all returned 0 rows from section_summary. Root cause was NOT
  missing data — sections are correctly loaded into Postgres via forward-fill. The bug was
  in _extract_section_name(): the regex `\bsections?\s+([word])\b` extracted "has", "any",
  "have" from general questions ("Which section has the highest..."), which became ILIKE
  filters like `section ILIKE '%has%'` matching zero sections.
- Apply: The _STOP set must include common verbs and adjectives that appear adjacent to
  "section" in general questions: has, have, had, any, some, each, every, best, worst,
  highest, lowest, most, least, zero, where, there, percentage, number, rate, with,
  without, complete, incomplete, concentration, show, shows, contain, contains.
  When adding new question patterns that use _extract_section_name(), always test with
  general "which section..." questions to verify the extractor returns empty string.

## R20: Use status_normalized enum column instead of ILIKE patterns on status (confirmed 2026-04-03)
- Confirmed: Materialized views used ILIKE '%passed%', '%failed%' etc. on the text status
  column. B-tree index on status cannot be used with leading % wildcards, causing full
  sequential scans at every view refresh. At 49k rows (ELD) this is tolerable; at 100k+
  multi-site it degrades.
- Apply: atlas_data_loader populates status_normalized with compact enum values (lldp_passed,
  lldp_failed, complete, not_terminated, not_run, human_verified, addition, etc.).
  Materialized views filter on status_normalized with equality checks (IN (...)), which
  hits the B-tree index. The human-readable status column is kept for LLM display.

## R21: optic_inventory UNION ALL double-counts cables where both sides have optics (confirmed 2026-04-03)
- Confirmed: A-side and Z-side optics are unioned into separate rows. A naive SUM(total)
  counts a cable twice if both sides have optics. The LLM will confidently return inflated
  numbers.
- Apply: Two views: optic_inventory_by_side (keeps A/Z separate for side-specific queries)
  and optic_inventory_combined (deduplicated, 1 row per cable using COALESCE(a_optic, z_optic)).
  LLM queries that ask "how many X optics?" should use optic_inventory_combined.

## R22: device_summary MAX(model) picks arbitrary model when A/Z sides disagree (confirmed 2026-04-03)
- Confirmed: MAX(model) returns the lexicographically last model string. If a device appears
  as "SN5610" on 50 A-side connections and "" on 2 Z-side connections, MAX picks "SN5610"
  (correct by luck). But if two real model strings compete, MAX picks the wrong one.
- Apply: Use MODE() WITHIN GROUP (ORDER BY model) FILTER (WHERE model != '') to pick the
  most frequently occurring non-empty model. MODE() is a Postgres ordered-set aggregate
  that returns the most common value.

## R23: Index a_optic, z_optic, cable_id, and burndown z_device for ad-hoc LLM queries (confirmed 2026-04-03)
- Confirmed: The LLM query router runs ad-hoc SQL on cutsheet_connections for optic lookups
  and cable-specific questions. These columns lacked indexes, causing sequential scans.
  burndown_connections.z_device was also missing an index (A-side was indexed, Z-side was not).
- Apply: Add B-tree indexes on a_optic, z_optic, cable_id in cutsheet_connections and
  z_device in burndown_connections.

## R24: cable_status_summary must include section as a dimension (confirmed 2026-04-03)
- Confirmed: The original cable_status_summary only grouped by site_id + status.
  Section-level completion questions (Q24, Q25, Q27) could not use the view and had
  to fall back to raw queries on cutsheet_connections.
- Apply: GROUP BY includes section so the view supports both site-level and section-level
  status queries without hitting the base table.

## R25: raw_row JSONB bloats the hot table; move to a separate table (confirmed 2026-04-03)
- Confirmed: Storing full raw row as JSONB in cutsheet_connections forces sequential scans
  (triggered by ILIKE patterns) to read through large JSONB blobs even when not needed.
  At scale this wastes I/O.
- Apply: cutsheet_raw_rows table joined by connection_id. Migration script moves existing
  data and drops the column from the main table.

## R26: Materialized views are stale until explicitly refreshed (confirmed 2026-04-03)
- Confirmed: refresh_atlas_views() must be called after every data load. If the LLM queries
  optic_inventory, cable_status_summary, or device_summary before a refresh, it gets
  stale/empty results. There is no trigger or automatic refresh.
- Apply: view_refresh_log table tracks when views were last refreshed. views_are_stale()
  function compares last upload timestamp vs last refresh timestamp. Callers can check
  staleness before returning query results.

## R27: All ad-hoc queries must use status_normalized, not ILIKE on status (confirmed 2026-04-03)
- Confirmed: build_postgres_context_for_general() optic summary query used ILIKE '%%passed%%'
  etc. on the text status column even though R20 added status_normalized specifically to
  avoid sequential scans. The materialized views were fixed but this ad-hoc query was missed.
- Apply: Any new or existing query that filters by connection status must use
  `status_normalized IN (...)` with equality checks, never ILIKE on the text status column.
  When adding new status filters, grep the codebase for `ILIKE.*status` to catch stragglers.

## R28: get_latest_upload() and all upload lookups must filter is_active = TRUE (confirmed 2026-04-03)
- Confirmed: B9 added soft-delete via is_active flag on cutsheet_uploads. The materialized
  views and build_postgres_context() both filter on is_active = TRUE, but get_latest_upload()
  did not. Any caller using get_latest_upload() directly could get a deactivated upload.
- Apply: Every query against cutsheet_uploads that returns "the current upload" must include
  `AND is_active = TRUE`. When adding new functions that reference uploads, always include
  the is_active filter.

## R29: All loader functions must vectorize column cleaning before the row loop (confirmed 2026-04-03)
- Confirmed: load_cutsheet() was vectorized (B2) but load_burndown() still used per-cell
  _clean() in an iterrows loop. This creates an inconsistency where one loader is 3-5x
  faster than the other for no reason.
- Apply: Every load_* function that iterates over DataFrame rows must pre-clean all text
  columns with `fillna("").astype(str).str.strip()` before the loop. The loop should read
  pre-cleaned values from to_dict("records"), not call _clean() per cell.

## R30: json.dumps() on pandas data must explicitly filter float NaN (confirmed 2026-04-03)
- Confirmed: B4 removed the column-count guard and always stores raw rows as JSON.
  The empty-value filter `{k: v for k, v in row.items() if v}` catches empty strings
  and None, but float('nan') is truthy (R12 again). json.dumps() serializes it as bare
  `NaN`, which is not valid JSON. Postgres rejects the INSERT with:
  `InvalidTextRepresentation: invalid input syntax for type json ... Token "NaN" is invalid.`
  This silently killed the entire cutsheet load (0 rows in cutsheet_connections).
- Apply: Any dict comprehension that filters pandas row values before json.dumps() must
  include an explicit NaN check: `if v and not (isinstance(v, float) and math.isnan(v))`.
  This is the third time R12 (NaN is truthy) has surfaced in a different context.
  Treat any code path that serializes pandas data to JSON/SQL as a NaN risk.

## R31: Postgres path reduces context tokens by ~89% vs in-memory path (confirmed 2026-04-15)
- Confirmed: bench_token_usage.py, 25 questions, ELD site (47k rows).
  In-memory: 9,063 tokens per question (same for all). Postgres avg: 968, median: 321.
  Worst case: section_completion and device list queries return 3,100-3,300 tokens (65% reduction).
  Best case: status/LLDP/site_overview queries return 72-110 tokens (98-99% reduction).
- Apply: Postgres path is always preferred. Even the heaviest question types (section_completion,
  full device list) are 65% smaller than the in-memory dump.

## R32: COUNT(DISTINCT col) OVER () is invalid in Postgres window functions (confirmed 2026-04-15)
- Confirmed: a_device_list and z_device_list SQL templates used COUNT(DISTINCT z_device) OVER ()
  inside the GROUP BY subquery. Postgres raises FeatureNotSupported.
- Apply: Move total_unique to the outer SELECT using COUNT(*) OVER () after GROUP BY has already
  deduplicated rows. COUNT(*) OVER () on the outer query = COUNT(DISTINCT col) before LIMIT
  because GROUP BY already produces one row per distinct value.

## R33: json.dumps on pandas Excel data needs default=str for time/date objects (confirmed 2026-04-15)
- Confirmed: ELD cutsheet A-BREAKOUT\nSLOT:PORT column contained datetime.time values read by
  openpyxl. json.dumps raised TypeError: Object of type time is not JSON serializable, killing
  the entire load silently (exception caught by load_file(), upload record committed but 0 rows).
- Apply: All json.dumps() calls on pandas-originated row dicts must include default=str to handle
  datetime.time, datetime.date, Decimal, and other non-JSON-serializable Excel cell types.
  This is an extension of R30 (NaN guard) — treat any pandas→JSON path as a serialization risk.

## R34: Missing canonical columns must hard-fail, not warn (confirmed 2026-04-19)
- Confirmed: load_cutsheet() and load_site_hosts() logged a warning when required canonical
  columns were missing after profile canonicalization, then continued ingestion. This allowed
  "almost right" cutsheets to store rows with blanks in device/port/model/status fields,
  making later queries look sparse or contradictory.
- Apply: Both functions now raise ValueError on missing required columns. The pipeline
  rejects ambiguous data at the gate. If a cutsheet format doesn't match a profile well
  enough to produce all required columns, it must not be ingested.

## R35: Duplicate source columns must be compared, not silently dropped (confirmed 2026-04-19)
- Confirmed: When both A-SIDE-DNS-NAME and A-SIDE DEVICE NAME existed in a cutsheet,
  apply_profile() kept the first-mapped column and silently dropped the other. If both
  columns had non-empty values that disagreed, one version was discarded with no audit trail.
- Apply: apply_profile() now compares values row-by-row when duplicate source columns map
  to the same Canon target. Conflicts are logged with count, row index, and sample values.
  First-mapped column still wins priority, but the decision is visible.

## R36: Section header derivation requires positive topology name match (confirmed 2026-04-19)
- Confirmed: Section derivation accepted any row where STATUS was filled and not a known
  status value. Random text (notes, instructions, partial data) could be promoted to section
  headers, causing connections to be written under wrong sections.
- Apply: Candidate section headers must also match _SECTION_HEADER_PATTERNS (topology
  keywords like TIER, SPINE, LEAF, FDP, CDU, GPU, NVLINK, etc.). Rejected candidates are
  logged so patterns can be expanded for site-specific naming.

## R37: Sheet selection must verify schema after heuristic pick (confirmed 2026-04-19)
- Confirmed: A non-cutsheet tab with enough overlapping headers (e.g. a tab with OPTIC
  in one column name) could be selected and canonicalized. The heuristic was necessary but
  not sufficient.
- Apply: After heuristic tab selection, _verify_cutsheet_schema() checks that the picked
  tab has optic columns AND device/port columns. A tab named "CUTSHEET" but lacking real
  cutsheet structure gets rejected with a warning.

## R38: Connection rows must be deduplicated at insert time (confirmed 2026-04-19)
- Confirmed: cutsheet_connections had no unique constraint on cable/port identity.
  Duplicate rows in source data were both inserted, causing double counts in queries.
- Apply: Unique indexes on (upload_id, cable_id) where cable_id non-empty, and
  (upload_id, a_device, a_port, z_device, z_port) for rows without cable_id.
  INSERT uses ON CONFLICT DO NOTHING. Duplicate count logged.

## R39: ROW:TYPE is physical placement, not functional role (confirmed 2026-04-19)
- Confirmed: Both "Role" and "ROW:TYPE" mapped to Canon.HOST_ROLE. If ROW:TYPE contained
  physical placement metadata (row/rack type), role-based queries (FDP, CDU, TOR) became
  ambiguous because different concepts were stored in the same DB column.
- Apply: ROW:TYPE maps to Canon.HOST_ROW_TYPE (new). host_inventory has a separate
  row_type column. Functional role queries only use the role column.

## R40: Model normalization must handle revision suffixes (confirmed 2026-04-19)
- Confirmed: normalize_model() did exact lowercase lookup only. Variants like "SN5610-revB",
  "sn5610", "SN5610 " remained split in the DB, fragmenting model-based queries.
- Apply: normalize_model() now strips known revision suffixes (-revB, -v2, -r1) and retries
  alias lookup. Both the scalar and vectorized versions use the same two-pass resolution.

## R7: In-memory dicts don't survive across gunicorn workers
- Confirmed: USER_CONTEXT and USER_SITE populated on worker 1 during upload, but QA
  routed to worker 2 which had empty dicts. "No uploaded sheet context" error.
- Apply: When Postgres is available, always recover state from the database rather
  than relying on per-process memory. Postgres is the shared state layer.

## R41: ANALYZE after every bulk load, before backfill_device_roles (confirmed 2026-05-30)
- Confirmed: execute_values bulk inserts leave cutsheet_connections/host_inventory with
  stale reltuples/histograms. As the DB grows, backfill_device_roles' UPDATE...FROM join
  picks a pathological plan. Measured on a 300k-row DB: a 42k-row backfill took 348.7s;
  after `ANALYZE` the same backfill took 3.8s (total load 355s -> 12s, ~30x). The first
  load into an empty table got lucky (24.6s); every subsequent load stalled for minutes.
- Apply: load_file runs `ANALYZE cutsheet_connections; ANALYZE host_inventory;` in its own
  committed transaction immediately after the main load commit and before backfill. ANALYZE
  is cheap (~0.4s) and also keeps downstream user-query plans healthy. Any new bulk-load path
  must ANALYZE before a stats-sensitive join. Do not rely on autovacuum/autoanalyze timing.
- Related open bug: load_file's `connections_loaded` return value and the "duplicate rows
  skipped" log badly under-report actual inserted rows (reported 111/237 vs 174k/42k actually
  present). Counting bug only; data is intact (cutsheet_connections == cutsheet_raw_rows).
