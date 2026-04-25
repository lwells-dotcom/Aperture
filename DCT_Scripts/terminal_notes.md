# Atlas Terminal Notes

---

## 2026-04-21 - Rack Analyzer context bridge, model in-service counts, and workbook in-service sorting

Session context:
Focused on the gap between Rack Analyzer and Ask Atlas, then on model-count correctness for queries like `How many SN5610s are in service?`, and finally on the workbook-side `sort by in service` split that was showing a blank left column and pushing everything into `Not In Service`.

What changed:

**Rack Analyzer -> Ask Atlas bridge**
- Confirmed the mismatch was architectural, not hallucination: `/api/buildsheet` read the uploaded workbook directly through `build_sheet_processor.process_rack()`, while `/api/ask` only used normal upload/Postgres/in-memory sheet context.
- Added a bridge in `atlas_web_app.py` so Rack Analyzer caches the last rack result for the authenticated user and `/api/ask` can reuse that cached rack-analysis context when the question matches the same rack and Postgres is empty or weak.
- Updated the Rack Analyzer frontend request to send the bearer token so the rack result can be tied to the same verified user session.
- Added a dedicated Rack Analyzer prompt/context path in `demo_auth_ai.py` so the LLM reads the cached rack result as preformatted grounded text instead of falling back to unrelated sheet context.

**Ask Atlas routing hardening**
- Continued hardening `query_extractors.py` and `query_intent.py` for real phrasing:
  - `dh202 rack 41`
  - `rack 41 in dh202`
  - `dh2 rack 041`
  - plural model forms like `7750-SR-1SEs`
- Prevented location-like tokens (`dh202`, rack identifiers) from being misread as models/devices.
- Tightened cross-site routing so generic words like `across`, `both`, and `entire cutsheet` do not steal single-site optic/status/device questions.
- Tightened status-router priority so section/model/site-total questions defer to their more specific routers instead of getting swallowed by generic status handling.
- Rebased the 100-question classifier harness on the current public extractor surface and updated stale expectations (`role_lookup`, `section_completion`) to match the current intended router behavior.
- Result: `python3 test_classify_100.py` finished at `100/100 correct`.

**Model count semantics**
- Confirmed that `model_search` still had a gap for status-qualified model questions:
  - it recognized `SN5610 in service` as `model_search`
  - but did not apply any status filter
  - and list-mode output could still make `LIMIT 200` look like the full answer
- Added `extract_model_status_filter()` in `query_extractors.py` and a new `status_count` mode in `atlas_query_router.py`.
- `model_search` now understands model-scoped status phrases such as:
  - `in service`
  - `LLDP passed`
  - `Human Verified`
  - `Complete`
  - `Not Run`
  - `Not Terminated`
- Added a dedicated status-count SQL path that reports:
  - unique device locations matching the filter
  - unique hostnames
  - matching cutsheet rows
  - A-side and Z-side row counts
- Also fixed list-mode formatting so `LIMIT 200` is presented as a truncated display cap, not as the true total.

**Workbook-side in-service split**
- Reproduced the exact bug on the real Ellendale masters using `count_all_files_gui_by_status()`:
  - `Optic Count - In Service` was all zeros
  - `Device Count - In Service` was all zeros
  - everything fell into `Not In Service`
- Root cause was in `Define_Optic_Count.py`: workbook-side `sort_by_status=True` only treated `LLDP Passed` as in service.
- Broadened workbook-side `in service` semantics with `_is_in_service_status()` so the split now treats the completed/verified family as in service:
  - `LLDP Passed`
  - `Human Verified`
  - `Complete`
  - `Cable Is Ran: Complete`
- Applied that broader helper to:
  - `count_cutsheet()`
  - `count_roce()`
  - `count_devices_cutsheet()`
  - `count_devices_roce()`
- Aligned Ask Atlas model-status extraction so `in service` now maps to the same broader status family rather than only `LLDP Passed`.

Validation run:
- `python3 -m unittest test_human_phrasing_routing.py test_location_rack_routing.py test_model_search_semantics.py test_query_followups.py test_router_priority_regressions.py test_rack_context_bridge.py`
- `python3 -m unittest test_define_optic_count_in_service.py test_model_search_semantics.py test_human_phrasing_routing.py test_location_rack_routing.py test_query_followups.py test_router_priority_regressions.py test_rack_context_bridge.py`
- `python3 -m py_compile` on all touched router, web, auth, and workbook-count files
- `python3 test_classify_100.py` -> `100/100 correct`

Real workbook checks:
- Reproduced the broken workbook split on:
  - `/Users/lwells/Atlas/MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx`
  - `/Users/lwells/Atlas/MASTER-US-CENTRAL-08B-US-LZL01-ELLENDALE.xlsx`
- After the workbook-side fix, `08B` changed from effectively `0` in-service optics / `0` in-service devices to:
  - in-service optics total: `12433`
  - not-in-service optics total: `34336`
  - in-service devices total: `2569`
  - not-in-service devices total: `11000`
  - `SN5610` in-service devices on `08B`: `811`
- `08A` now shows `SN5610` in-service devices: `626`

What is different now:
- Asking Atlas about the same rack right after using Rack Analyzer can use the actual rack result instead of unrelated or empty sheet context.
- Status-qualified model questions no longer collapse into a misleading capped `200` answer.
- Workbook `sort by in service` no longer leaves the left column blank on Ellendale-style cutsheets where the dominant successful status is `Cable Is Ran: Complete`.

Open note:
- The browser/UI path was not re-run end-to-end after the final workbook-side patch in this terminal session, so the remaining confirmation step is to reload Atlas and verify the updated in-service split and model query behavior through the live app.
