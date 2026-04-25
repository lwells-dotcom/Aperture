# Atlas - Hypotheses (need more data)

## H1: Postgres query templates will reduce token usage by 60-80%
- Rationale: Current approach sends entire summarized context (~8k tokens) per question.
  Targeted SQL queries should return only relevant rows, cutting context to 1-2k tokens.
- Test: Compare token counts before/after Postgres integration on the same question set.
- Status: CONFIRMED (2026-04-15). bench_token_usage.py ran 25 questions against ELD (47k rows).
  In-memory context: 9,063 tokens per question (constant).
  Postgres avg: 968 tokens. Median: 321. Min: 72. Max: 3,318.
  Average reduction: 89.3% — exceeds 60-80% claim.
  By type: status/LLDP queries 98-99%, model_search 95%, optic_count 97%,
  rack_summary 91%, section_summary 88%, section_completion/device_list 63-65%.
  Full-prompt reduction (including fixed overhead): 86.7%.
  Promoting to rule R31. See token_benchmark_ELD.txt for full results.

## H2: LLM-generated SQL is too risky for production; template-based routing is safer
- Rationale: Letting the LLM write arbitrary SQL opens injection risk and hallucination.
  Pre-built query templates mapped to question types (Option A) are more predictable.
- Test: Try both approaches on the same 10-15 question types, compare accuracy and safety.
- Status: IMPLEMENTED (2026-03-29). atlas_query_router.py uses regex classification + template SQL.
  13 question types covered. No LLM-generated SQL. Still need accuracy testing vs edge cases.

## H3: Materialized views will handle 90% of summary questions without hitting base tables
- Rationale: optic_inventory and cable_status_summary cover the most common question patterns.
- Test: Categorize 20+ real user questions, check how many can be answered from views alone.
- Status: TESTABLE (2026-03-29). Query router routes optic_count to optic_inventory view and
  cable_status to cable_status_summary view. Need real question logs to measure coverage.

## H4: Multi-site loads will expose normalizer performance issues
- Rationale: cutsheet_normalizer iterates row-by-row with Python loops. At 100k+ rows
  this could become a bottleneck during upload. Postgres bulk inserts may be faster.
- Test: Time the normalizer on a 100k row synthetic cutsheet vs. direct Postgres load.
- Status: PARTIALLY TESTED (2026-04-01). Ellendale (56k rows) normalizes in 4.3s via
  in-memory pandas. Acceptable for single-site uploads but two sites (60k+) combined
  would push 8-10s. Token limits for the LLM context are the real bottleneck, not
  normalization time. Postgres path is required for multi-site.

## H6: Automated 100-question test suite will surface 5-10 missing question types
- Rationale: Q37 (tier connections) and Q67 (LLDP neighbor mismatch) both failed because
  classify_question() fell through to "general". Pattern suggests more question types
  are missing. Risk/anomaly questions (Q91-Q100) are the most likely to fail.
- Test: Run all 100 questions from test_questions.md through classify_question() and
  then against live Atlas. Bucket results: passed / routed wrong / data gap.
- Status: CONFIRMED (2026-04-01). Ran full 100-question suite via test_classify_100.py.
  Baseline: 67/100 correct, 19 fell to general. After targeted pattern + extraction fixes:
  98/100 correct, 0 general fallbacks. 2 remaining misses are functionally acceptable
  (Q26 cable_status vs connection_status, Q80 device_list vs model_search — both return
  correct data). All 19 original general fallbacks resolved. H2 also further confirmed:
  template routing with 18 question types now covers the full 100-question set.
- Fixes applied (2026-04-01) to atlas_query_router.py:
  - LLDP+fail beats LLDP+count in pattern ordering
  - LLDP ratio/percentage → connection_status (prevents lldp_failures over-fire)
  - Burndown keyword → link_status (before connection_status)
  - Optic mismatch, empty, populated, "which sections + optics" patterns added
  - Section patterns: management plane, tiers represented, sections where, GG1-X, locode,
    NET-AGG, COMP-AGG, NET-DIST, COMP-DIST, STOR section names
  - data halls (plural) → data_hall_summary
  - Rack count "across data halls" → rack_summary (wins over data_hall)
  - Model patterns: numeric-prefix (7750-SR-1SE), alphanumeric (CPU-GP2-02, NET-6X100G-01),
    digit-prefix (1U-1N-GEN5-1NIC), multi-segment (PROLIANT-DL360-GEN10-PLUS)
  - "which/what device model", "device model inventory/sorted/most" → model_search
  - "inconsistent casing/naming", "z-model" → model_search
  - Unique devices, FDP/fiber distribution panel → device_list
  - Total connections in cutsheet → site_overview
  - Cable completion rate → cable_status
  - Unverified/remaining gap → connection_status
  - _extract_model_pattern: added CPU-GP2-02 and 1U-1N-GEN5 style patterns
  - _extract_section_name: capped ROCE greedy match
  - _extract_location_pattern: excluded bare RU/LOC/CAB from location results

## H7: device_list-family queries without LIMIT will blow token budgets on large sites (confirmed 2026-04-02)
- Confirmed: z_device_list with no LIMIT returned 13,838 rows for ELD, producing 69,208 tokens
  of context and 437,941 total input tokens on a single question. Answer was correct but cost
  was ~200x what a properly capped query would produce.
- Fix applied: LIMIT 200 added to z_device_list, a_device_list, and device_list. Window function
  COUNT(DISTINCT z_device) OVER () returns true total count even when rows are capped, so the
  formatter can report "13,838 total (showing top 200)" without a second query.
- Status: CONFIRMED → promoting to rule. All unbounded SELECT-from-large-table queries must have
  a LIMIT. Any template that returns a device list must include total_unique via window function.

## H8: Joining host_inventory roles to cutsheet_connections will unlock Z-side role queries
- Rationale: Users ask "what FDPs are on the Z-side" but the LLM can't answer because device
  role (FDP, switch, server, CDU, etc.) only lives in host_inventory/DEVICE_INVENTORY, not in
  cutsheet_connections. The z_model column has the model name but not the functional role.
  build_llm_context() further flattens everything by model, losing A/Z side distinction.
- Status: IMPLEMENTED (2026-04-15). All three changes done:
  (1) a_role/z_role TEXT columns added to cutsheet_connections (schema + ALTER TABLE migration
      + partial indexes WHERE NOT NULL).
  (2) backfill_device_roles() in atlas_data_loader.py runs two UPDATE...FROM JOINs after
      load_site_hosts() completes. Called only when hosts_loaded > 0. Non-fatal on failure.
  (3) build_llm_context() now computes model_by_side (a_only/z_only/both per model) from
      seen_as sets. build_llm_context_from_prebuilt() gets model_by_side: {} for key parity.
  (4) atlas_query_router.py: new role_lookup question type with patterns for FDP/CDU/PDU/TOR/
      spine/leaf/fabric with optional A/Z side filter. FDP removed from device_list.
      _extract_role_and_side() extracts both role keyword and side. Two-mode formatter:
      summary (no role filter) vs device list (specific role).
- Test: Upload a cutsheet with a SITE-HOSTS tab that has a ROLE column, then ask
  "What devices are listed as FDP on the Z-side?" — expect device list, not "data not available."
  Check roles_backfilled in load_file() return dict; should be > 0.
- Remaining risk: SITE-HOSTS tab may not have a ROLE column in all sites. If role is empty,
  backfill silently updates 0 rows. The empty-row formatter explains the gap clearly.
- Discovered: 2026-04-07

## H5: The 5-minute response cache (LLM_CACHE_TTL_SECONDS=300) is the right default
- Rationale: Long enough to catch repeated questions during demos, short enough that
  re-uploads with new data don't serve stale answers.
- Test: Monitor cache hit rate and stale-answer complaints over multiple demo sessions.
- Status: Untested. Cache was just implemented.
