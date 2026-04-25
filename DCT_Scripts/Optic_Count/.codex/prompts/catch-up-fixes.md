Catch up on the current Optic_Count fixes without making assumptions.

Session notes from prior review and live testing:
- The repo was reviewed for parser, upload, mapping, query-routing, and Rack Analyzer issues.
- Previously confirmed issues included:
  - pandas `ExcelFile` compatibility problems in `Define_Optic_Count.py`
  - upload routes getting stuck or failing unclearly in `atlas_web_app.py` / `demo_web_app.py`
  - cutsheet parsing failures tied to breakout header variants
  - incorrect in-service vs not-in-service classification for cutsheet statuses
  - Rack Analyzer requiring exact `CUTSHEET` tab names
  - cutsheet-to-Postgres mapping ambiguity from profile detection and duplicate-column resolution
  - overly large, order-dependent regex routing in `atlas_query_router.py`
- Some of those fixes are already in progress or landed in the current working tree.
- A live Flask end-to-end test was run against `atlas_web_app.py`:
  - verify PIN worked
  - main count flow previously failed on breakout-header mismatch, then succeeded after tree updates
  - `Count, Sort by In Service` previously misclassified complete rows, and related fixes appear to be in progress
  - `Ask AI` worked end to end
  - Rack Analyzer worked with a valid cutsheet after tab-detection improvements
  - token expiry can still surface as a raw `401 Token expired` UX issue during longer sessions
- Treat the current working tree as the source of truth. Do not assume earlier findings are still open until you verify them in the code.

Steps:
1. Read scoped AGENTS.md instructions and respect them.
2. Run `git status --short` and identify files currently being modified.
3. Review the main Python files involved in recent fixes:
   - `Define_Optic_Count.py`
   - `atlas_web_app.py`
   - `demo_web_app.py`
   - `atlas_data_loader.py`
   - `cutsheet_profiles.py`
   - `atlas_query_router.py`
   - `build_sheet_processor.py`
   - `demo_auth_ai.py`
4. Compare the current code against known issue areas:
   - Excel parsing / pandas compatibility
   - cutsheet header mapping
   - status normalization and in-service classification
   - cutsheet-to-Postgres canonicalization
   - regex / question routing ambiguity
   - Rack Analyzer tab-name assumptions
   - upload/session handling
5. Distinguish clearly between:
   - already fixed
   - partially fixed
   - still open
   - new regressions introduced
6. Return findings first, ordered by severity, with file and line references.
7. Do not change code unless explicitly asked.
8. Do not repeat resolved issues unless the implementation is incomplete or risky.
