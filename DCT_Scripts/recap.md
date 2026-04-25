# Atlas Session Recap - 2026-03-28

## What Atlas Is
Flask web app that lets users upload datacenter cutsheet Excel files and ask an LLM (Claude via Anthropic API) questions about the infrastructure. Answers are grounded strictly in cutsheet data only. No hallucination allowed.

## What We Built Today

### 1. PostgreSQL Schema (atlas_schema.sql)
9 tables + 2 materialized views, written to `Optic_Count/atlas_schema.sql`.
Tables: sites, data_halls, site_vars, cutsheet_uploads, devices, ip_assignments, topology_sections, connections, node_data.
Views: optic_inventory (optic counts by site/type/status), cable_status_summary (cable completion by site/section/status).
IP columns flattened from 20+ Excel columns into ip_assignments rows using Postgres INET type.
node_data is separate from devices (only ~360 of ~793 devices are compute nodes).
cutsheet_uploads tracks file hashes for versioning and delta detection.

### 2. Data Loader (atlas_data_loader.py)
Reads Excel files, detects sheets (CUTSHEET, SITE-HOSTS, SITE-VARS, SITE-IP-DATA, SITE-NODE-DATA), populates Postgres in one transaction. Reuses normalizer's model alias resolution and section header detection.
CLI: `python atlas_data_loader.py --file path/to/cutsheet.xlsx --site QCY`
Also importable via `load_file()` for Flask integration.

### 3. Docker Stack
Dockerfile (multi-stage build, non-root user, gunicorn), docker-compose.yml (Postgres 16 + Flask), .dockerignore.
Postgres on host port 9000 (5432 is occupied on Lamar's machine), Flask on port 5050.
Schema auto-runs on first boot via initdb.d mount.

### 4. LLM Resilience Layer (llm_resilience.py)
Inspired by a KDnuggets article on Python decorators for AI agents.
@with_retry: tenacity exponential backoff on 429/5xx/network errors. Won't retry 400/401/403.
@with_timeout: SIGALRM hard ceiling (45s default). Skips when not on main thread (gunicorn workers).
@with_fallback: Anthropic -> OpenAI automatic fallback chain.
@with_cache: TTL-based response cache keyed on question+context hash (300s default).
Graceful degradation: if tenacity isn't installed, all decorators become no-ops.

### 5. Context Pipeline Fix (cutsheet_normalizer.py)
build_llm_context() was missing connection status aggregation. The STATUS column (LLDP Passed, LLDP Failed, Cable Is Ran Complete, Human Verified) existed in raw data but was never surfaced to the LLM. Added connection_status_counts and status_by_section to the context dict. This was the biggest grounding quality fix of the session.

### 6. Config Overhaul (.env.example)
Reorganized into sections: LLM Providers, LLM Resilience, SSL/Compliance, Demo Auth, Web App, Database. All new resilience knobs documented with sane defaults.

### 7. Knowledge System
Created DCT_Scripts/knowledge/ folder structure per CLAUDE.md instructions.
knowledge/atlas/knowledge.md - facts and patterns about the codebase.
knowledge/atlas/hypotheses.md - 5 hypotheses to test (token savings from Postgres, template vs LLM SQL, etc).
knowledge/atlas/rules.md - 5 confirmed rules from today's bugs.
CLAUDE.md updated with Atlas-specific rules and typo fixes.

### 8. Architecture Diagram
atlas_architecture.puml + atlas_architecture.svg showing current state (in-memory pandas, full context dump per question) vs future state (Postgres-backed, query router with targeted SQL templates).

## Bugs We Hit and Fixed
- DEMO_VERIFY_PIN defaulted to empty string in docker-compose, overriding Python fallback. Fixed by setting real default: `${DEMO_VERIFY_PIN:-123456}`
- signal.SIGALRM crashed in gunicorn worker threads. Fixed by adding `threading.current_thread() is threading.main_thread()` check.
- API key was in .env.example but not .env. The app only reads .env.
- build_llm_context() didn't aggregate STATUS field, so LLM correctly reported "data not available" even though the raw data had it.

## What's Next
1. Wire Flask upload endpoint to run atlas_data_loader.py (write to Postgres on upload instead of holding in-memory).
2. Build the query router: classify incoming questions, map to SQL templates, run targeted queries against Postgres, feed small result sets to LLM as context.
3. Query templates for the 10-15 most common question types (optic counts, device connections, cable status, LLDP failures, cross-site comparisons).
4. Update ask_grounded() to accept Postgres query results as context instead of (or alongside) the pandas-based context.
5. React+TypeScript frontend (future).
6. Helm chart for K8s deployment (future, COO approval pending).

## Key Files Changed/Created
- Optic_Count/atlas_schema.sql (new)
- Optic_Count/atlas_data_loader.py (new)
- Optic_Count/llm_resilience.py (new)
- Optic_Count/atlas_architecture.puml (new)
- Optic_Count/atlas_architecture.svg (new)
- Optic_Count/Dockerfile (new)
- Optic_Count/docker-compose.yml (new)
- Optic_Count/.dockerignore (new)
- Optic_Count/requirements.txt (updated - added tenacity)
- Optic_Count/.env.example (updated - reorganized, added resilience config)
- Optic_Count/demo_auth_ai.py (updated - retry/timeout/fallback/cache decorators on LLM calls)
- Optic_Count/cutsheet_normalizer.py (updated - added connection_status_counts and status_by_section)
- DCT_Scripts/CLAUDE.md (updated - Atlas rules, typo fixes)
- DCT_Scripts/knowledge/Index.md (new)
- DCT_Scripts/knowledge/atlas/knowledge.md (new)
- DCT_Scripts/knowledge/atlas/hypotheses.md (new)
- DCT_Scripts/knowledge/atlas/rules.md (new)

## Quincy Cutsheet Stats (for reference)
~4,300 rows, 53 topology sections.
Statuses: LLDP Passed (2953), Cable Is Ran Complete (483), LLDP Failed (255), Human Verified (14).
Models: SN4700 (1226), SN2201 (1133), SN3700 (968), OM2216-C14 (245), CM8148 (89), 7750-SR-1SE (19), PA-1420 (11), SN3420 (13).
Optics: QSFP28-100G-DR1 (2382), QSFPDD-400G-DR4 (1821).

## Tech Stack
Python, Flask, PostgreSQL, Docker, pandas, tenacity, gunicorn. Future: React+TypeScript frontend, Kubernetes.

## Lamar's Preferences
Direct casual tone, no fluff, no bullet points unless asked. Likes Python, Docker, Excel/Sheets. Stack is NetSuite, Jira, Slack, Google Cloud. Usually writes Flask + Docker + Postgres + React/TypeScript.

---

# Atlas Session Recap - 2026-04-19 (Session 2): Ingestion Strictness Hardening

## Context
Picked up from the previous session's performance/bug hardening. This session focused on 7 findings about the ingestion pipeline being too permissive, allowing "almost right" cutsheets to silently store ambiguous data.

## What We Fixed

### Finding 1 (High): Missing canonical columns now fail hard
Changed load_cutsheet() and load_site_hosts() to raise ValueError instead of logging a warning when required canonical columns are missing after profile canonicalization. The pipeline now rejects bad data at the gate instead of storing blanks.

### Finding 2 (High): Duplicate source column conflicts are now auditable
When two source columns map to the same Canon target (e.g. A-SIDE-DNS-NAME and A-SIDE DEVICE NAME both targeting Canon.A_DEVICE), apply_profile() now compares their values row-by-row. Conflicts are logged with count, row index, and sample values. First-mapped column still wins, but the decision is visible.

### Finding 3 (High): Section header detection tightened
Section derivation now uses positive match patterns (TIER, SPINE, LEAF, FDP, CDU, GPU, NVLINK, etc.) in addition to the existing negative match (not a known status). Random non-status text no longer gets promoted to section headers. Rejected candidates are logged so patterns can be expanded.

### Finding 4 (Medium): Fuzzy model normalization
normalize_model() and normalize_model_column() now strip revision/version suffixes (-revB, -v2, -r1) before retrying alias lookup. "SN5610-revB", "sn5610", and "SN5610 " all resolve to "SN5610".

### Finding 5 (Medium): ROW:TYPE separated from ROLE
ROW:TYPE now maps to Canon.HOST_ROW_TYPE instead of overloading Canon.HOST_ROLE. New row_type column added to host_inventory schema. Physical placement metadata no longer pollutes functional role queries.

### Finding 6 (Medium): Sheet selection schema verification
After heuristic tab selection, the loader now verifies the picked tab has optic columns AND device/port columns. A tab named "CUTSHEET" but lacking real cutsheet structure gets rejected with a warning instead of silently processed.

### Finding 7 (Low): Connection uniqueness guard
Added unique indexes on cutsheet_connections: (upload_id, cable_id) for rows with cable IDs, and (upload_id, a_device, a_port, z_device, z_port) for rows without. INSERT uses ON CONFLICT DO NOTHING. Duplicate count logged.

## Files Changed
- cutsheet_profiles.py: detect_profile() return type, apply_profile() conflict detection, normalize_model() fuzzy matching, Canon.HOST_ROW_TYPE, ROW:TYPE mapping
- atlas_data_loader.py: hard fail on missing columns, section header positive match, sheet schema verification, ON CONFLICT dedup, row_type in host insert
- atlas_schema.sql: host_inventory.row_type column, unique indexes on cutsheet_connections

## Known Remaining Gaps
- build_llm_context() still doesn't group devices by A/Z side (in-memory path)
- _SECTION_HEADER_PATTERNS may need expansion for site-specific topology naming
- .iterrows() bottlenecks still present in cutsheet_normalizer (vectorization TODO)
- atlas_web_app.py still missing security headers (demo_web_app.py has them)

## Query Router Refactor (IMPLEMENTED 2026-04-19)
- Replaced ~90 ordered regex patterns with 12 domain routers in query_intent.py
- New modules: query_lexicon.py (keyword sets), query_extractors.py (focused extractors), query_intent.py (domain routers + QuestionContext/IntentResult), query_debug.py (audit trail)
- atlas_query_router.py now a thin facade importing from new modules
- 86/86 parity test passing against old regex classification
- Extractors run once per question (QuestionContext), reused by all routers
- route_question() now logs and returns classification confidence, domain, and reason
- Fixes applied during parity testing: CDU/PDU/TOR/FDP plural forms, link health vs link status, section+completion vs cable+completion priority, node_compute before device_list in router chain

## Consolidated Finding Status (as of 2026-04-19)
Ingestion findings 1-7: ALL FIXED (rules R34-R40)
Query router findings 1-5: ALL IMPLEMENTED via domain router refactor
