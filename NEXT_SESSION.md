# Next Session Briefing — New Cutsheet Integration

**Last updated:** 2026-04-26
**Context:** Waves 1-3 of the GitNexus audit attack plan are complete (70+ findings fixed across 15 files). All changes are uncommitted on the working tree.

---

## What's Done

Three waves of parallel terminal fixes completed:
- **Wave 1 (Critical):** DB stability (managed_connection rollback), security hardening, SQL routing fixes
- **Wave 2 (High):** Web app route fixes, performance (iterrows removal), routing gaps
- **Wave 3 (Medium):** Cutsheet pipeline cleanup (single status dict, per-site headers), query router refactor (sql_templates.py extraction, formatter registry), new query types (cable_type_summary, data_hall filtering), missing indexes

Key architectural change: `atlas_query_router.py` dropped from 1620 to 923 lines. SQL templates live in `sql_templates.py`. STATUS_NORMALIZATION is now auto-derived from STATUS_MAP (one source of truth).

---

## What's Next: Multi-File Cutsheet Ingestion

### The Problem
Two Ellendale MASTER cutsheets exist:
- `MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx` (~18.7 MB) — already loaded as site ELD
- `MASTER-US-CENTRAL-08B-US-LZL01-ELLENDALE.xlsx` (~19.8 MB) — **NOT YET LOADED**

Both are in `/Atlas/` root (and duplicated in `/Atlas/raw/`).

### Step-by-Step Plan

#### Phase 1: Parse & Map (Claude in Excel)
1. Open `MASTER-US-CENTRAL-08B` in Claude in Excel
2. Identify all sheet/tab names — the cutsheet tab may not be named "CUTSHEET"
3. Map the column layout against `PROFILE_STANDARD_V1` in `cutsheet_profiles.py`
4. Extract all unique STATUS column values — compare against `STATUS_MAP` in `cutsheet_preprocessor.py`
5. Extract all section header values — compare against `SITE_SECTION_HEADERS["ELD"]` in `cutsheet_preprocessor.py`
6. Document any new status values or section headers that need to be added

#### Phase 2: Update Preprocessor Config
7. Add any new STATUS_MAP entries to `cutsheet_preprocessor.py`
8. Add any new section headers to `SITE_SECTION_HEADERS["ELD"]` dict
9. If column layout differs from V1, either extend V1 or create a new profile

#### Phase 3: Load & Verify
10. Deploy (see `DEPLOY_STEPS.md` for full Kind/Helm deploy)
11. Load 08B via: `python atlas_data_loader.py --file /app/uploads/ELD02.xlsx --site ELD`
12. Verify both uploads coexist: `SELECT id, filename, is_active FROM cutsheet_uploads WHERE site_id = (SELECT id FROM sites WHERE site_code = 'ELD');`
13. Test upload_diff between 08A and 08B uploads
14. Verify query results include data from both uploads (or active upload only, depending on is_active)

### Key Files to Read First
- `cutsheet_preprocessor.py` — STATUS_MAP, SITE_SECTION_HEADERS, classify_status()
- `cutsheet_profiles.py` — PROFILE_STANDARD_V1, Canon column constants
- `atlas_data_loader.py` — load_file() pipeline, managed_connection()
- `DEPLOY_STEPS.md` — Full deploy guide
- `ATTACK_PLAN.md` — Remaining Wave 4 items

### Multi-Upload Architecture (Already Built)
- `cutsheet_uploads` table tracks each upload with `is_active` flag
- All SQL templates support `upload_id` scoping: `AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)`
- `upload_diff` query type can compare any two uploads
- `upload_list` shows all uploads for a site
- Re-uploading the same file (same hash) triggers soft-delete of the old upload

---

## Wave 4 (Deferred — Do After Cutsheet Integration)

### LLM Performance
- Q3: Migrate from raw urllib.request to Anthropic SDK (streaming)
- Q1: SSL context caching
- Short-circuit simple queries (skip LLM for count/list)

### Cleanup & Hardening
- M5, V3, V4, N8-N12, B5, B6, G4, G5, F14
- See ATTACK_PLAN.md Wave 4 section

---

## Deploy Reminder

All Waves 1-3 changes are uncommitted. Before any new work:
1. Review changes: `git diff --stat`
2. Consider committing the Wave 1-3 work as a checkpoint
3. Do a clean deploy + test cycle (see DEPLOY_STEPS.md)
4. Then proceed with new cutsheet work
