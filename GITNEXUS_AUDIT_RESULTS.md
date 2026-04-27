# Atlas — Flask/Postgres Connection & Routing Analysis

> Generated: 2026-04-25 | Scope: `DCT_Scripts/Optic_Count/` | Method: GitNexus symbol/flow analysis + static code review

---

## Executive Summary

| ID | Severity | File | Lines | Description |
|----|----------|------|-------|-------------|
| C1 | CRITICAL | `atlas_data_loader.py` | 117–125 | `managed_connection()` returns dirty conn to pool on exception (no rollback) |
| H1 | HIGH | `atlas_web_app.py` | 647–664 | Inline raw SQL in `/api/ask` route bypasses router; inherits C1 |
| H2 | HIGH | `atlas_web_app.py` | 462–463 | `/api/count-by-status` calls file I/O with no try/except — stale paths crash with 500 |
| H3 | HIGH | `atlas_query_router.py` | 968–974 | `ip_lookup` fallback uses last word of question, or `"%"` matching all rows |
| M1 | MEDIUM | `atlas_query_router.py` | 905–906 | Pure rack-number input returns `""` location filter — silently returns full-site data |
| M2 | MEDIUM | `atlas_query_router.py` | 1006, 1011 | `execute_query()` uses `time.time()` — violates confirmed codebase rule, can go negative |
| M3 | MEDIUM | `atlas_postgres_context.py` | 65–77, 136–147 | Double `upload_id` lookup on every general question (redundant DB round-trip) |
| M4 | MEDIUM | `atlas_web_app.py` | 526–527, 575–576, 609–610 | `buildsheet` routes return HTTP 500 for `ValueError` (user input error should be 400) |
| M5 | MEDIUM | `atlas_data_loader.py` | 138–156 | `check_postgres()` TTL cache global unguarded — thread race under 4-thread Gunicorn |
| L1 | LOW | `atlas_data_loader.py` | 128–135 | `get_connection()` / `return_connection()` are dead code — unsafe pool leak pattern |

---

## CRITICAL

### C1 — `managed_connection()` Missing Rollback on Exception
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:117–125`

```python
@contextmanager
def managed_connection() -> Generator:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)   # ← always returns conn, even mid-transaction
```

**What breaks:** When any code inside `with managed_connection()` raises an exception during an open transaction, the connection is returned to the pool **without rolling back**. The next caller that receives this connection will immediately fail with:

```
psycopg2.errors.InFailedSqlTransaction: current transaction is aborted,
commands ignored until end of transaction block
```

This affects every DB call in the app — `atlas_query_router.execute_query()`, `atlas_postgres_context.build_postgres_context()`, `atlas_data_loader.load_file()`, and the inline query in `/api/ask`. Under concurrent load, one failing request can poison a pool connection and cause a cascade of failures for subsequent users.

**Recommended fix:**
```python
@contextmanager
def managed_connection() -> Generator:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
```

---

## HIGH

### H1 — Inline Raw SQL in `/api/ask` Route
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:647–664`

The "site recovery" fallback inside `ask_ai()` embeds a raw parameterized query directly in the Flask route handler:

```python
from atlas_data_loader import managed_connection
with managed_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.id AS site_id, s.site_code, cu.id AS upload_id
               FROM cutsheet_uploads cu
               JOIN sites s ON cu.site_id = s.id
               WHERE cu.uploaded_by = %s AND cu.is_active = TRUE
               ORDER BY cu.created_at DESC LIMIT 1""",
            (username,),
        )
        row = cur.fetchone()
```

**Issues:**
1. **Inherits C1.** If the cursor operation fails, the connection returns dirty to the pool.
2. **Schema coupling.** Hardcodes `cutsheet_uploads.uploaded_by` and the JOIN to `sites`. A column rename or schema migration silently breaks this — the outer `except Exception` catches it and returns a vague `pg_warning` field in the JSON response instead of an actionable error.
3. **Architectural inconsistency.** All other DB queries go through `atlas_data_loader` or `atlas_query_router`. This inline SQL in a route handler creates a hidden maintenance surface.

**Recommended fix:** Extract to `atlas_data_loader.get_latest_upload_for_user(conn, username)` and call it from the route.

---

### H2 — `/api/count-by-status` Unhandled File-Not-Found
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:462–463`

```python
files = [f["file_path"] for f in USER_CONTEXT[claims["sub"]].get("files", [])]
result_text = Define_Optic_Count.count_all_files_gui_by_status(files)
```

**What breaks:** `USER_CONTEXT` stores the absolute path to the uploaded `.xlsx` at upload time. If the container restarts, the 2-hour session TTL evicts the context and a new file is uploaded, or `ATLAS_UPLOAD_DIR` is cleaned, the stored path points to a deleted file. `count_all_files_gui_by_status()` raises `FileNotFoundError` with a raw Python traceback surfaced in the 500 response — exposing internal file paths to the client.

No `try/except` wraps this call. The same path-staleness risk exists in any code that reads back `USER_CONTEXT["files"]`.

**Recommended fix:** Wrap in `try/except (FileNotFoundError, OSError)` and return a structured 400 with a message like `"File no longer available — re-upload to refresh"`.

---

### H3 — `ip_lookup` Fallback Pattern Matches All Rows
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:968–974`

```python
if qtype == "ip_lookup":
    if _ip:
        params["search_pattern"] = f"%{_escape_ilike(_ip)}%"
    else:
        words = re.findall(r"\b[a-zA-Z0-9]{3,}\b", question)
        search_term = words[-1] if words else ""
        params["search_pattern"] = f"%{_escape_ilike(search_term)}%" if search_term else "%"
```

**Two failure modes:**

1. **Last-word noise.** When no IP is extracted, `words[-1]` uses the final word of the natural language question. Questions like *"show me the IP table"* or *"list IPs please"* produce `search_pattern = "%table%"` or `"%please%"` — either returning garbage rows to the LLM or matching on coincidental column data.

2. **Wildcard full-table scan.** If `words` is empty (e.g., question is purely punctuation or very short), `search_pattern = "%"` runs against `cutsheet_raw_rows` with no filter — returning every row for the site. For a large site this can be thousands of rows, overflowing the LLM context window and producing an unusable response.

**Recommended fix:** If no IP is extracted for an `ip_lookup` query, route to `general` instead of executing a catchall pattern.

---

## MEDIUM

### M1 — Pure Rack Number Silently Ignored as Location Filter
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:905–906`

```python
def _build_location_pattern(location: str) -> str:
    ...
    if re.fullmatch(r"\d{1,4}", loc):
        return ""   # ← bare rack number returns empty string
```

When a user asks *"how many optics in rack 3?"* or *"show rack status for rack 12"*, `extract_location()` returns `"3"` or `"12"`. `_build_location_pattern` matches the `\d{1,4}` branch and returns `""`.

For `optic_count` and `rack_summary` the SQL template evaluates:

```sql
AND (%(location_filter)s = '' OR a_loc_cab_ru ILIKE %(location_filter)s)
```

With `location_filter = ""`, the condition is `'' = ''` → TRUE — the clause is **skipped entirely**. The query returns full-site data as if no location was specified. The LLM answers a site-wide question believing it answered a rack-specific one. No error, no warning.

The `location_lookup` type is guarded (line 1543 checks for empty `location_pattern`), but `optic_count` and `rack_summary` are not.

**Recommended fix:** Return `None` (not `""`) for unresolvable rack numbers, and have `build_query_params()` surface a warning or route to `location_lookup` with an explicit error when the pattern cannot be built.

---

### M2 — `execute_query()` Uses `time.time()` (Confirmed Rule Violation)
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:1006, 1011`  
**Also:** `atlas_postgres_context.py:149`

```python
t0 = time.time()
# ... DB call ...
elapsed = round(time.time() - t0, 4)
```

CLAUDE.md confirmed rule: *"Use `time.monotonic()` (not `time.time()`) for all cache TTL checks. `time.time()` can jump during NTP sync."*

During an NTP clock correction that steps the system clock backward while a query is in-flight, `time.time() - t0` goes negative. This produces `query_elapsed_seconds: -0.002` in the `/api/ask` JSON response and in log lines — confusing monitoring and alerting.

**Recommended fix:** Replace both occurrences with `time.monotonic()`.

---

### M3 — Double `upload_id` Lookup on Every General Question
**File:** `DCT_Scripts/Optic_Count/atlas_postgres_context.py:65–77` and `136–147`

`build_postgres_context()` resolves `upload_id` via:
```python
# Lines 65-77
cur.execute(
    "SELECT id FROM cutsheet_uploads WHERE site_id = %s AND is_active = TRUE "
    "ORDER BY created_at DESC LIMIT 1", (site_id,)
)
```

For `general` question type, it then calls `build_postgres_context_for_general()` which **repeats the identical query** at lines 136–147 before opening the composite context connection.

Every general question makes two sequential `SELECT id FROM cutsheet_uploads ...` round-trips to Postgres before any actual data query runs. Since `upload_id` is already resolved in the caller, it should be passed as a parameter to avoid the redundant lookup.

---

### M4 — `buildsheet` Routes Return HTTP 500 for User Input Errors
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:526–527, 575–576, 609–610`

```python
except (FileNotFoundError, ValueError, OSError) as exc:
    return jsonify({"error": str(exc)}), 500
```

Same pattern in `buildsheet()`, `buildsheet_layout()`, and `buildsheet_dh()`.

`ValueError` is raised by `build_sheet_processor` for invalid `room` or `rack` values — these are user input errors, not server faults. Returning HTTP 500 for user errors:
- Triggers false-positive server error alerts in monitoring
- Tells the client "retry this request" when the correct action is "fix your input"
- Masks actual server errors (OSError, FileNotFoundError) that ARE 500-worthy

**Recommended fix:** Split the except:
```python
except ValueError as exc:
    return jsonify({"error": str(exc)}), 400
except (FileNotFoundError, OSError) as exc:
    return jsonify({"error": str(exc)}), 500
```

---

### M5 — `check_postgres()` TTL Cache Not Thread-Safe
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:138–156`

```python
_pg_ok_at: float = 0.0

def check_postgres() -> bool:
    global _pg_ok_at
    now = time.monotonic()
    if (now - _pg_ok_at) < _PG_TTL:
        return True
    try:
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        _pg_ok_at = now      # ← unguarded write
        return True
    except Exception:
        _pg_ok_at = 0.0
        return False
```

`_pg_ok_at` is a module-level float with no lock. The app runs with Gunicorn's 4-thread worker. When the 10-second TTL expires, all 4 threads simultaneously fail the `(now - _pg_ok_at) < _PG_TTL` check and each fires an independent `SELECT 1` health-check connection.

Normally benign (4 harmless pings), but during a Postgres outage or pool exhaustion event this pattern causes every incoming request to attempt a connection check, multiplying failed connection attempts by 4× and potentially starving the pool during recovery.

**Recommended fix:** Add a simple threading lock around the read-modify-write:
```python
_pg_check_lock = threading.Lock()

def check_postgres() -> bool:
    global _pg_ok_at
    now = time.monotonic()
    if (now - _pg_ok_at) < _PG_TTL:
        return True
    with _pg_check_lock:
        if (now - _pg_ok_at) < _PG_TTL:   # double-checked
            return True
        ...
```

---

## LOW

### L1 — `get_connection()` / `return_connection()` Are Dead Code
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:128–135`

```python
def get_connection():
    """Get a connection from the pool. Caller must call return_connection()."""
    return _get_pool().getconn()

def return_connection(conn) -> None:
    """Return a connection obtained via get_connection() back to the pool."""
    _get_pool().putconn(conn)
```

No callers found anywhere in the codebase. These functions expose raw pool get/put without a context manager, meaning any caller that forgets `return_connection()` (or has an exception path that skips it) permanently leaks a connection from the pool until the app restarts.

Since `managed_connection()` already provides the safe alternative, these functions are unnecessary. If left in place they are a trap for future contributors.

**Recommended fix:** Remove both functions. If a raw connection is ever needed externally, the caller should use `managed_connection()` as a context manager.

---

## Remediation Priority

```
Immediate (before next production deploy):
  C1  ← affects every DB call; one bad query poisons pool connections

Short-term (next sprint):
  H1  ← inherits C1; inline SQL in route is fragile
  H2  ← unhandled exception surfaces internal paths to client
  H3  ← can flood LLM context with full-table scan

Next cycle:
  M1  ← silent wrong-result bug for rack-number queries
  M4  ← false server error alerts
  M5  ← thread safety under outage conditions

Cleanup:
  M2  ← monotonic timer correctness
  M3  ← one extra DB round-trip per general question
  L1  ← remove dead code
```

---

## Section 2: atlas_query_router.py — Code Review Verification

> User-submitted review dated 2026-04-25. Each claim verified against source.

### Summary of Verdicts

| Claim | Verdict | Notes |
|-------|---------|-------|
| `classify_question` imported but never used | NUANCED — see R1 | It's an accidental re-export relied on by 6 test files |
| `QUESTION_TYPES` defined but never referenced | CONFIRMED — see R2 | Zero references anywhere in the codebase |
| `format_results_for_llm` is ~300+ lines god function | CONFIRMED (worse) — see R3 | Actually 480 lines, not ~300 |
| Parser-hostile structure / huge SQL strings | CONFIRMED — see R4 | File is 1,584 lines; SQL templates up to 150+ lines each |
| Broad `except Exception` in `route_question()` | CONFIRMED — see R5 | Line 1578; `log.exception` used (mitigates somewhat) |
| Additional: silent fallback on unknown qtype | NEW — see R6 | `execute_query()` runs "general" SQL with no warning logged |

---

### R1 — `classify_question` Import: Accidental Re-Export, Not Dead Code
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:25`

```python
from query_intent import classify_question, classify_with_context, IntentResult, QuestionContext
```

**Verdict: NUANCED — the original claim is incomplete.**

`classify_question` is never called inside `atlas_query_router.py` (only `classify_with_context` is used at line 1508). However, it is **not** dead — 6 test files import it directly from `atlas_query_router`:

```python
# test_router_priority_regressions.py, test_classify_100.py,
# test_view_coverage.py, test_model_search_semantics.py,
# test_human_phrasing_routing.py, test_location_rack_routing.py
from atlas_query_router import classify_question
```

The real definition lives in `query_intent.py:996`. The import in `atlas_query_router` creates an undocumented re-export. This is fragile: any developer who removes the import as "unused" silently breaks the entire test suite with no warning from linters (since it *is* exported).

**Recommended fix:** Have all test files import from `query_intent` directly, then remove from `atlas_query_router`. Alternatively, make the re-export explicit with a comment.

---

### R2 — `QUESTION_TYPES` List Is Dead Code
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:35–65`

**Verdict: CONFIRMED.**

Defined at line 35, `QUESTION_TYPES` has zero references in any production or test file across the entire `DCT_Scripts/` tree. It is never used to validate `qtype` inputs, drive routing logic, or generate documentation.

Its absence from the validation path is itself a secondary issue (see R6). It should either be wired in as a validator or removed.

---

### R3 — `format_results_for_llm()` Is a 480-Line God Function
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:1015–1495`

**Verdict: CONFIRMED — and larger than reported.**

The function spans lines 1015–1495: **480 lines**, not the ~300 estimated in the review. It contains a deep `if-elif` chain covering all 27+ question types, with distinct formatting logic, empty-result messages, and edge-case handling per branch.

Problems this creates:
- Any new question type requires editing this single function
- Unit-testing a single formatter requires exercising the full dispatch
- The function mixes business rules (e.g. special empty-result messages for `lldp_failures`, `role_lookup`) with presentation logic

**Recommended fix:** A formatter registry pattern — one function per question type, registered in a dict:
```python
_FORMATTERS: Dict[str, Callable] = {
    "optic_count": _format_optic_count,
    "rack_summary": _format_rack_summary,
    ...
}
def format_results_for_llm(qtype, rows, question=""):
    return _FORMATTERS.get(qtype, _format_default)(rows, question)
```

---

### R4 — Monolithic File Structure Breaks GitNexus Indexing
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py` (1,584 lines, 74KB)

**Verdict: CONFIRMED.**

The `_SQL_TEMPLATES` dict contains 27 multi-line SQL strings, several spanning 100–150+ lines (e.g. `rack_summary`, `upload_diff`, `trend_section`). Tree-sitter, used by GitNexus for scope extraction, struggles with deeply nested large string literals in Python — this is the root cause of the `"scope extraction failed / Invalid arg"` warnings visible in GitNexus hook output during this session.

The three standalone SQL constants (`_MODEL_SEARCH_RAW_COUNT_SQL`, `_MODEL_SEARCH_STATUS_COUNT_SQL`, `_MODEL_SEARCH_UNIQUE_COUNT_SQL`, lines 779–859) add further bulk.

**Recommended fix:** Extract all SQL to `sql_templates.py` as module-level string constants, then import them. This also makes `atlas_query_router.py` parseable and allows GitNexus to fully index symbols like `route_question`, `build_query_params`, and `execute_query`.

---

### R5 — Broad `except Exception` in `route_question()`
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:1578–1584`

```python
    except Exception as exc:
        log.exception("Query routing failed for type=%s", qtype)
        return {
            "ok": False,
            "question_type": qtype,
            "error": str(exc),
        }
```

**Verdict: CONFIRMED — but partially mitigated.**

`log.exception` logs the full traceback (good). However the broad catch swallows:
- `psycopg2.OperationalError` (connection lost mid-query) — returned as `{"ok": False}` rather than propagating to trigger reconnect logic
- `psycopg2.InterfaceError` (closed connection from a dirty pool conn — see C1) — masked as a routing failure
- `KeyboardInterrupt` / `SystemExit` are not caught since those don't inherit `Exception`, so that edge is safe

The main risk is that a pool-level error (C1 cascading failures) surfaces to the caller as a question-routing failure, making it harder to distinguish between "wrong question type" and "database is down."

**Recommended fix:** Catch `psycopg2.Error` separately and re-raise (or return a distinct error code) so the caller can differentiate DB errors from routing logic errors.

---

### R6 — NEW: `execute_query()` Silently Falls Back to "general" for Unknown `qtype`
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:997`

```python
sql = _SQL_TEMPLATES.get(qtype, _SQL_TEMPLATES["general"])
```

**Verdict: NEW ISSUE — not in original review.**

If an unknown or misspelled `qtype` is passed to `execute_query()` (e.g. `"rack_sumary"` typo), the function silently runs the `"general"` 3-metric fallback SQL with no warning logged. The caller receives plausible-looking rows (3 aggregate metrics) with no indication that the intended query never ran.

`QUESTION_TYPES` (R2) exists but is never used to validate `qtype` at this callsite, making it both dead and failing at its implied purpose.

**Recommended fix:** Add a guard:
```python
if qtype not in _SQL_TEMPLATES:
    log.warning("Unknown qtype %r, falling back to general", qtype)
```
Or wire `QUESTION_TYPES` as the validation set and raise `ValueError` for unknown types.

---

## Combined Remediation Priority (All Sections)

```
Immediate (before next production deploy):
  C1  ← managed_connection() missing rollback; root of cascading DB failures

Short-term (next sprint):
  H1  ← inline SQL in /api/ask route; inherits C1
  H2  ← unhandled FileNotFoundError in /api/count-by-status
  H3  ← ip_lookup wildcard full-table scan
  R4  ← extract SQL templates to fix GitNexus indexing (quick win)

Next cycle:
  M1  ← silent wrong-result for rack-number location queries
  M4  ← HTTP 500 returned for user input errors in buildsheet routes
  M5  ← check_postgres() thread race
  R5  ← distinguish DB errors from routing errors in route_question()
  R6  ← silent qtype fallback in execute_query()

Cleanup:
  M2  ← time.time() → time.monotonic() in execute_query()
  M3  ← redundant upload_id lookup in general context path
  R1  ← fix accidental classify_question re-export
  R2  ← remove QUESTION_TYPES dead code (or wire as validator for R6)
  R3  ← break format_results_for_llm into per-type formatter registry
  L1  ← remove get_connection() / return_connection() dead code
```

---

*Analysis performed via GitNexus symbol traversal (Atllas_Coreweave index) + static review of atlas_data_loader.py, atlas_web_app.py, atlas_query_router.py, atlas_postgres_context.py.*

---

## Section 4: build_sheet_processor.py — Code Review Verification

> File: `DCT_Scripts/Optic_Count/build_sheet_processor.py` (707 lines)  
> User-submitted review dated 2026-04-25. Each claim verified against source.

### Summary of Verdicts

| ID | Verdict | Claim | Notes |
|----|---------|-------|-------|
| B1 | CONFIRMED (worse) | Multiple `load_workbook()` calls per request | `process_rack` opens cutsheet 3×; `generate_layout_workbook` opens template N× per cab type |
| B2 | CONFIRMED | No caching on `_overhead_rack_to_cab_type()` | Called twice per `process_rack`; no `@lru_cache` |
| B3 | CONFIRMED (nuanced) | Light error handling around Excel ops | Web app catches wrong exception types for corrupt files |
| B4 | PARTIAL | Large functions | 177 and 143 lines — long but not extreme; overstated vs `format_results_for_llm` (480 lines) |
| B5 | CONFIRMED | Magic strings for sheet names | `'OVERHEAD'`, `'SITE-HOSTS'`, `f'ELEV {cab_type}'` etc. inline |
| B6 | CONFIRMED | `Alignment` imported but never used | Zero usages in file |
| B7 | NEW | `cables_raw` scanned 3× in `process_rack` | Three separate O(n) passes; could be one |
| B8 | NEW | `_lookup_elevation` called per-cab-type inside loop | Opens template workbook N× for N cab types in `generate_layout_workbook` |

---

### B1 — Workbook Opens Per Request Are Worse Than Described
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py`

**`process_rack()` — 4 workbook opens per call:**

| Call site | Line | File opened |
|-----------|------|-------------|
| `openpyxl.load_workbook(cutsheet_path)` | 352 | cutsheet (main load) |
| `_lookup_cab_type(cutsheet_path, ...)` → `_overhead_rack_to_cab_type(cutsheet_path)` | 259 → 234 | cutsheet (2nd open) |
| `_cab_type_summary(cutsheet_path, ...)` → `_overhead_rack_to_cab_type(cutsheet_path)` | 268 → 234 | cutsheet (3rd open) |
| `_lookup_elevation(template_path, cab_type)` | 369 → 296 | template (1st open) |

Every `POST /api/buildsheet` request opens the cutsheet 3× via openpyxl before any caching. For an Ellendale-scale 50 MB file, openpyxl takes 3–10s per load — meaning up to 30s of redundant I/O per rack request.

**`generate_layout_workbook()` — 2+N workbook opens per call:**

```python
wb_cut = openpyxl.load_workbook(cutsheet_path, ...)          # line 586
rack_to_cab = _overhead_rack_to_cab_type(cutsheet_path)      # line 592 → opens cutsheet AGAIN

for cab_type in sorted(cab_racks.keys()):                    # N iterations
    elevation = _lookup_elevation(template_path, cab_type)   # line 627 → opens template each time
```

The template is opened **once per unique cab type**. A data hall with 5 cab types (e.g. `2POST`, `4POST-OPEN`, `4POST-ENCLOSED`, `WALL`, `UPS`) = 5 template loads. Combined with 2 cutsheet loads, a single layout export opens 7 workbooks.

**Recommended fix:** Pass the already-loaded workbook or the pre-parsed data into `_overhead_rack_to_cab_type` rather than a path. For `_lookup_elevation`, load the template once before the loop and pass the sheet data in.

---

### B2 — `_overhead_rack_to_cab_type()` Has No Caching
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py:229–250`

```python
def _overhead_rack_to_cab_type(cutsheet_path):
    wb = openpyxl.load_workbook(cutsheet_path, read_only=True, data_only=True)
    ...
    wb.close()
    return mapping
```

No `@lru_cache` or memoization. In a single `process_rack()` call, this function is invoked twice with the same path — once from `_lookup_cab_type()` (line 259) and once from `_cab_type_summary()` (line 268) — opening and scanning the OVERHEAD sheet twice.

Since `cutsheet_path` is a string (hashable), adding `@functools.lru_cache(maxsize=32)` would reduce both `process_rack` and `generate_layout_workbook` to a single OVERHEAD scan per unique file path, with the result reused across the request lifetime.

**Note:** `lru_cache` on a module-level function persists across requests in the same Gunicorn worker. The OVERHEAD mapping is static per cutsheet file, so cross-request caching is safe here — the cache key is the full file path, and different uploads produce different paths.

---

### B3 — Error Handling Catches Wrong Exception Types for Corrupt Files
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:526–527`

The `buildsheet` route wraps processor calls with:
```python
except (FileNotFoundError, ValueError, OSError) as exc:
    return jsonify({"error": str(exc)}), 500
```

openpyxl raises two exception types for corrupt or invalid Excel files that are NOT caught here:

- `openpyxl.utils.exceptions.InvalidFileException` — raised for non-xlsx files, encrypted workbooks, or structurally invalid zip archives. Inherits from `Exception`, not `ValueError` or `OSError`.
- `zipfile.BadZipFile` — raised when the xlsx (which is a zip archive) is truncated or corrupted. Also inherits from `Exception`.

Both will propagate past the `except` clause, producing an unhandled exception with a raw Python traceback in the 500 response — exposing internal file paths and stack frames to the API caller.

**Recommended fix:** Add `openpyxl.utils.exceptions.InvalidFileException` and `zipfile.BadZipFile` to the caught exception types in `atlas_web_app.py`, or add a try/except inside `process_rack()` around the initial `load_workbook()` call with a clear user-facing error message.

---

### B4 — Large Functions (Partially Confirmed)
**Verdict: PARTIALLY CONFIRMED — overstated relative to other files.**

- `process_rack()`: lines 341–517 = **177 lines** — long, handles loading + filtering + enrichment + optic counting + sorting + serialization
- `generate_layout_workbook()`: lines 564–707 = **143 lines**

These are sizeable but not extreme. For comparison, `format_results_for_llm()` in `atlas_query_router.py` is 480 lines (R3). The review's characterization of `generate_layout_workbook` as "even heavier" than `process_rack` is accurate by complexity but not by line count.

The real concern in `process_rack()` is the three separate passes over `cables_raw` (see B7), not the function length itself.

---

### B5 — Magic Strings for Sheet Names Are Inline
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py:235, 295, 353, 358`

**Verdict: CONFIRMED.**

```python
if 'OVERHEAD' not in wb.sheetnames:          # line 235
sheet_name = f'ELEV {cab_type}'              # line 295
hosts_raw = _read_sheet(wb_cut, 'SITE-HOSTS', HOSTS_COLS)  # line 358
_find_cutsheet_tab(wb_cut) or 'CUTSHEET'    # line 353
```

These sheet name strings appear in 4 different locations across helper functions. If a cutsheet uses a different casing or spelling (e.g. `"Site-Hosts"` instead of `"SITE-HOSTS"`, or `"Overhead"` instead of `"OVERHEAD"`), the lookups silently return empty data with no error. The `CUTSHEET_COLS` and `HOSTS_COLS` dicts are properly defined as module-level constants, but the sheet names they apply to are not.

**Recommended fix:** Define module-level constants:
```python
_SHEET_CUTSHEET  = "CUTSHEET"
_SHEET_HOSTS     = "SITE-HOSTS"
_SHEET_OVERHEAD  = "OVERHEAD"
_SHEET_ELEV_PFX  = "ELEV "
```

---

### B6 — `Alignment` Imported but Never Used
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py:13`

**Verdict: CONFIRMED.**

```python
from openpyxl.styles import Font, PatternFill, Alignment
```

`Alignment` has zero usages anywhere in the file. `Font` and `PatternFill` are both actively used in `generate_layout_workbook()`. `Alignment` is a dead import — minor, but will trigger linter warnings and is potentially confusing to a reader who wonders where cell alignment is being set.

---

### B7 — NEW: `cables_raw` Scanned Three Times in `process_rack()`
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py`

Not mentioned in the original review.

`process_rack()` iterates over `cables_raw` in three separate loops:

| Pass | Lines | Purpose |
|------|-------|---------|
| 1 | 376–383 | Filter cables where A-side is in the rack → build `rack_cables` |
| 2 | 415–427 | Scan all cables to build `loc_statuses` and `devices` dict |
| 3 | 456–472 | Scan all cables again to build `optic_counts` and `optic_locations` |

All three passes scan the **full** `cables_raw` list (not just `rack_cables`). For a large Ellendale cutsheet with 20,000+ rows, this is 3 × O(n) Python iterations where a single pass would suffice. The data from all three passes could be accumulated in one loop.

---

### B8 — NEW: `_lookup_elevation()` Called Per-Cab-Type in Loop (No Caching)
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py:608–661`

Not mentioned in the original review.

```python
for cab_type in sorted(cab_racks.keys()):
    ...
    if has_template:
        elevation = _lookup_elevation(template_path, cab_type)   # line 627
```

`_lookup_elevation()` opens the template workbook with `openpyxl.load_workbook()` each time it is called. Since it's inside the cab_type loop, the template is opened once per unique cab type in the room. A large data hall may have 4–6 distinct cab types, so a single `generate_layout_workbook()` request opens the template 4–6 times.

The template workbook is read-only and static per request — it should be opened once before the loop and the relevant sheet data extracted in a single pass.

---

### build_sheet_processor.py Remediation Priority

```
Short-term (performance):
  B1  ← reduce load_workbook() calls; process_rack opens cutsheet 3×
  B2  ← add @lru_cache to _overhead_rack_to_cab_type(); trivial fix
  B8  ← load template once before cab_type loop, not per iteration
  B7  ← merge 3 cables_raw passes into one loop in process_rack()

Next cycle:
  B3  ← add InvalidFileException + BadZipFile to web app exception handler
  B5  ← centralize sheet name strings as module constants

Cleanup:
  B4  ← consider splitting process_rack() into load + filter + format phases
  B6  ← remove unused Alignment import
```

---

## Section 3: Cutsheet Pipeline Analysis

> Files reviewed: `cutsheet_profiles.py` (674 lines), `cutsheet_normalizer.py` (769 lines), `cutsheet_preprocessor.py` (659 lines)

### Summary of Findings

| ID | Severity | File | Lines | Description |
|----|----------|------|-------|-------------|
| N1 | HIGH | `cutsheet_normalizer.py` | 166–183 | `iterrows()` loop in `normalize_cutsheet()` — dominant upload bottleneck at scale |
| N2 | HIGH | `cutsheet_normalizer.py` | 555–556 | `_CONNECTION_CACHE` is unbounded — memory leak across uploads in Gunicorn worker |
| N3 | HIGH | `cutsheet_profiles.py` / `cutsheet_preprocessor.py` | — | Dual status normalization dicts already diverged; new statuses require updating both |
| N4 | HIGH | `cutsheet_preprocessor.py` / `demo_auth_ai.py` / `atlas_data_loader.py` | — | Same Excel file parsed up to 4× in a single upload pipeline |
| N5 | MEDIUM | `cutsheet_preprocessor.py` | 117–282 | `STATUS_MAP` hardcodes ~160 Ellendale-specific section headers; breaks for new sites |
| N6 | MEDIUM | `cutsheet_preprocessor.py` | 651–659 | `_find_cutsheet_tab()` silent wrong-tab selection for non-standard layouts |
| N7 | MEDIUM | `cutsheet_normalizer.py` | 627–628 | `lookup_device_connections()` hostname heuristic misses single-hyphen names |
| N8 | MEDIUM | `cutsheet_normalizer.py` | 358, 402, 472 | `load_prebuilt_sheets()` uses `iterrows()` in 3 places |
| N9 | LOW | `cutsheet_profiles.py` | 610–663 | `canonicalize()` runs up to 12 full-column passes on the same DataFrame |
| N10 | LOW | `cutsheet_profiles.py` | 599–604 | `normalize_model_column()` boolean-index Series mutation — pandas 2.x warning risk |
| N11 | LOW | `cutsheet_normalizer.py` | 283–284 | Nested function `_brk` allocated on every row in `_build_connection()` |
| N12 | LOW | `cutsheet_preprocessor.py` | 51–282 | `STATUS_MAP` has no duplicate-key guard; silent overwrite risk on future edits |

---

### N1 — `normalize_cutsheet()` `iterrows()` Loop Is the Upload Bottleneck
**File:** `DCT_Scripts/Optic_Count/cutsheet_normalizer.py:166–183`

```python
for idx, row in data_rows.iterrows():
    section = row.get("_section", "UNKNOWN")
    status = _normalize_cell(row.get(Canon.STATUS))
    a_device = _extract_device(row, "A")
    z_device = _extract_device(row, "Z")
    if a_device:
        _register_device(devices, a_device, section, "A")
    if z_device:
        _register_device(devices, z_device, section, "Z")
    conn = _build_connection(row, a_device, z_device, section, status, breakout_seen)
    if conn:
        connections.append(conn)
```

`pandas.iterrows()` is 100–1000× slower than vectorized operations — it converts each row to a Python `Series` object on every iteration. For a 4,300-row Quincy cutsheet this calls `_normalize_cell()` 4+ times per row (~17,000 Python calls), `_extract_device()` twice (~8,600 calls), and `_build_connection()` once per row (~4,300 calls).

For a larger site like Ellendale (~20,000 rows), this loop is the dominant bottleneck in the entire upload pipeline. The in-memory path already hits token limits at Quincy scale (~4,300 rows); if this path is ever extended to larger sites, `iterrows()` will be a hard blocker.

The section-tagging above the loop is already vectorized (`ffill()`, boolean masking) — the row loop was not converted to match.

**Recommended fix:** Vectorize using `DataFrame.apply()` with `axis=1` or restructure to use bulk column operations. At minimum, replace `iterrows()` with `itertuples()` for a 3–10× speedup with minimal code change.

---

### N2 — `_CONNECTION_CACHE` Is Unbounded — Memory Leak
**File:** `DCT_Scripts/Optic_Count/cutsheet_normalizer.py:555–556, 569–615`

```python
_CONNECTION_CACHE: Dict[str, pd.DataFrame] = {}      # module-level, never cleared
_CONNECTION_CACHE_COLS: Dict[str, Dict[str, str]] = {}
```

Every call to `preload_connections(file_path)` adds a new DataFrame entry keyed by the full file path. Upload filenames are unique per-upload: `{timestamp}_{username}_{filename}` — so each upload adds a new cache entry that is never evicted.

A large cutsheet DataFrame can be 50–200 MB in memory. After 10 uploads in a Gunicorn worker, the worker holds 500 MB–2 GB of cached DataFrames indefinitely. Since Gunicorn workers are long-lived, this is a slow memory leak that will eventually trigger OOM kills in Kubernetes.

The cache check (`if file_path in _CONNECTION_CACHE`) correctly skips re-loading the same path, but the eviction path is missing entirely.

**Recommended fix:** Add a maximum cache size (e.g. 3 entries, LRU eviction) or tie cache lifetime to the session TTL:

```python
from functools import lru_cache
# Or: use a simple ordered dict with a max-size guard
_MAX_CACHE = 3
def _evict_if_full():
    while len(_CONNECTION_CACHE) >= _MAX_CACHE:
        _CONNECTION_CACHE.pop(next(iter(_CONNECTION_CACHE)))
```

---

### N3 — Dual Status Normalization Dictionaries Are Already Diverged
**Files:** `cutsheet_profiles.py:90–133`, `cutsheet_preprocessor.py:51–282`

There are two independent status normalization dictionaries:

| Dict | Location | Maps to |
|------|----------|---------|
| `STATUS_NORMALIZATION` | `cutsheet_profiles.py:90` | Mixed-case canonical strings (`"LLDP Passed"`, `"Cable Is Ran Complete"`) |
| `STATUS_MAP` | `cutsheet_preprocessor.py:51` | SCREAMING_SNAKE_CASE enum constants (`LLDP_PASSED`, `COMPLETE`) |

They serve different consumers — `STATUS_NORMALIZATION` feeds the Postgres ingest path (`atlas_data_loader._to_status_enum`), while `STATUS_MAP` drives the upload-time `cutsheet_preprocessor`. But they cover the same raw input strings and must stay in sync.

**They have already drifted:**
- `STATUS_NORMALIZATION` maps `"in progress"` → `"In Progress"` and `"pending"` → `"Pending"` (mixed-case output)
- `STATUS_MAP` has `IN_PROGRESS` and `PENDING` enum constants but they are only reachable via `_STATUS_MAP_LOWER` (lowercase fallback) — the exact-match entry uses the enum, not the canonical string
- `cutsheet_preprocessor.py` defines `SECTION_HEADER` as a canonical status value but `STATUS_NORMALIZATION` has no concept of section headers — they're handled by `_is_section_header_mask()` in the normalizer

Adding a new raw status variant (e.g. a new site's spelling) requires editing **both files**. Missing one means the Postgres ingest and the in-memory upload count see different canonical values, causing status mismatch bugs between the two data paths.

**Recommended fix:** Derive `STATUS_MAP` from `STATUS_NORMALIZATION` at module load (or vice versa) so there is one authoritative source. Alternatively, add a test that asserts both dicts cover the same input keys.

---

### N4 — Same Excel File Parsed Up to 4× Per Upload
**Files:** `cutsheet_preprocessor.py:638,541`, `demo_auth_ai.py:265,280`, `atlas_data_loader.py:793`

Tracing a single `POST /api/upload-count` request:

| Step | Call | Parser open |
|------|------|-------------|
| 1 | `cutsheet_preprocessor._find_cutsheet_tab()` | `pd.ExcelFile(filepath)` — opens to scan sheet names, then closes |
| 2 | `cutsheet_preprocessor.preprocess_upload()` | `pd.read_excel(filepath, sheet_name=tab)` — full parse |
| 3 | `demo_auth_ai.build_llm_context()` (in-memory path) | `pd.ExcelFile(file_path)` + `pd.read_excel(...)` — second full parse of same file |
| 4 | `atlas_data_loader.load_file()` (background thread) | `pd.ExcelFile(file_path)` — third full parse for Postgres ingest |

For a large xlsx (Ellendale-scale, ~50 MB), each `pd.read_excel()` call through openpyxl takes 3–10 seconds. Steps 3 and 4 happen concurrently (background thread + synchronous path), so the I/O is partially overlapped — but the total parsing work is 3–4× the minimum needed.

**Recommended fix:** Parse the Excel file once and pass the resulting DataFrame(s) through the pipeline. A shared `parsed_workbook` structure passed as a parameter would eliminate redundant I/O.

---

### N5 — `STATUS_MAP` Hardcodes ~160 Ellendale-Specific Section Headers
**File:** `DCT_Scripts/Optic_Count/cutsheet_preprocessor.py:117–282`

```python
"CON-01 Grid A Pod 1": SECTION_HEADER,
"CON-01 Grid A Pod 2": SECTION_HEADER,
# ... ~160 more Ellendale/DH202/DH204/TIER-*/NET-01 entries ...
```

These hardcoded values come directly from the Ellendale (US-LZL01) cutsheet. For any other site (new or existing) whose section headers don't match these exact strings AND don't match the `_TOPOLOGY_PATTERN` regex, section detection will fail silently — those rows stay `UNKNOWN` and are included as data rows rather than stripped.

The downstream effect: section header text ends up in the `STATUS` column of ingested connections, polluting status counts and potentially causing rows with bogus device names to be inserted into `cutsheet_connections`.

The `_TOPOLOGY_PATTERN` regex (line 321) was added as a fallback but it only fires for the `UNKNOWN + no-optics` case — it doesn't replace the hardcoded dict.

**Recommended fix:** Move the Ellendale-specific section header list into a per-site profile override in `cutsheet_profiles.py`, rather than a hardcoded dict in the preprocessor. General-purpose section detection should rely only on `_is_section_header_mask()` logic + `_TOPOLOGY_PATTERN`.

---

### N6 — `_find_cutsheet_tab()` Silent Wrong-Tab Selection
**File:** `DCT_Scripts/Optic_Count/cutsheet_preprocessor.py:651–659`

```python
# Fallback: first non-junk tab
for name in names:
    lower = name.lower()
    if not any(pat in lower for pat in _SKIP_TAB_PATTERNS):
        return name
return names[0]
```

`_SKIP_TAB_PATTERNS = ("legend", "backup", "copy of", "sheet", "overhead")` — this does not include `"summary"`, `"status_map"`, `"host_inventory"`, `"burndown"`, or `"site-hosts"`. If a multi-tab file has the actual cutsheet in a tab named `"US-LZL01 MASTER"` or `"Main Cutsheet"` and those appear after a `"Summary"` tab, the fallback would select `"Summary"` (since it doesn't contain a skip pattern) and pass it to `_verify_cutsheet_schema()`.

The schema verification would then raise `ValueError("Tab 'Summary' has no optic columns...")` with a confusing error message listing Summary tab column names. The user sees no indication of which tab was actually selected.

**Recommended fix:** Log the selected tab name at `INFO` level before returning from `_find_cutsheet_tab()` so the user and operator can see which tab was chosen. Expand `_SKIP_TAB_PATTERNS` to include common non-data tab names.

---

### N7 — `lookup_device_connections()` Hostname Heuristic Is Fragile
**File:** `DCT_Scripts/Optic_Count/cutsheet_normalizer.py:627–628`

```python
tokens = question.replace(",", " ").replace(";", " ").split()
candidates = [t.strip("'\"?.,") for t in tokens if t.count("-") >= 2 and len(t) > 10]
```

The heuristic `t.count("-") >= 2 and len(t) > 10` will:

- **Miss** hostnames with one hyphen: `SN4700-1`, `sw-spine1`, `tl-mgmt-01` (only 2 hyphens but short)
- **Miss** hostnames shorter than 10 characters: `r760-01a` (8 chars)
- **Match false positives**: `"step-by-step"`, `"well-known-issue"`, `"cable-is-ran-complete"` pass the filter but aren't hostnames
- Always use `candidates[0]` — if the question mentions two devices and the non-target one appears first, the lookup silently returns wrong results

When no candidates match, the function returns `None` silently — the caller (`demo_auth_ai.build_llm_context()`) receives no connection data without any indication of why the lookup failed.

---

### N8 — `load_prebuilt_sheets()` Uses `iterrows()` in Three Places
**File:** `DCT_Scripts/Optic_Count/cutsheet_normalizer.py:358, 402, 472`

```python
for _, row in inv_df.iterrows():   # DEVICE_INVENTORY sheet
for _, row in conn_df.iterrows():  # CONNECTIONS sheet
for _, row in sum_df.iterrows():   # SUMMARY sheet
```

Same performance issue as N1. For large prebuilt sheets, these loops are the bottleneck of the prebuilt context path. The CONNECTIONS loop (line 402) processes every connection row to build optic summaries and mismatch detection — for an Ellendale-scale file with 20K connections, this loop will be noticeably slow.

---

### N9 — `canonicalize()` Runs Up to 12 Full-Column Passes
**File:** `DCT_Scripts/Optic_Count/cutsheet_profiles.py:610–663`

Each call to `canonicalize()` executes in sequence:
1. `apply_profile()` — column rename + conflict detection (vectorized comparison per duplicate target)
2. `normalize_status_column()` — full STATUS column map pass
3. Per-profile status overrides — second STATUS pass (if overrides exist)
4. `normalize_model_column()` × up to 4 columns (`MODEL`, `HOST_MODEL`, `A_MODEL`, `Z_MODEL`)
5. Per-profile model overrides × up to 4 columns — 4 more passes
6. Optic strip loop × 2 columns (`A_OPTIC`, `Z_OPTIC`)

Total: up to 12 full-column passes. For a 4,000-row DataFrame this is still sub-second, but passes 4–9 always iterate even when the columns don't exist (guarded by `if col in df.columns`, which is fast). The larger concern is that `canonicalize()` is called **per-sheet-type** and potentially multiple times per file (cutsheet + host + burndown tabs), multiplying the pass count.

---

### N10 — `normalize_model_column()` Pandas 2.x Copy Warning Risk
**File:** `DCT_Scripts/Optic_Count/cutsheet_profiles.py:599–604`

```python
mapped = lowered.map(MODEL_ALIASES)
unresolved = mapped.isna() & (cleaned != "")
if unresolved.any():
    stripped = lowered[unresolved].str.replace(_MODEL_SUFFIX_RE, "", regex=True).str.strip()
    mapped[unresolved] = stripped.map(MODEL_ALIASES)   # ← boolean-indexed Series mutation
```

`mapped[unresolved] = ...` sets values on a Series via boolean indexing. In pandas 2.0+ with Copy-on-Write semantics, this triggers `FutureWarning: ChainedAssignmentError` and in pandas 3.0 it will silently have no effect (the mutation will not propagate to the caller). This means `normalize_model_column()` would stop applying suffix-stripped alias resolution without any runtime error.

**Recommended fix:** Use `mapped.where(~unresolved, stripped.map(MODEL_ALIASES))` or rebuild the full series: `mapped = mapped.fillna(stripped.map(MODEL_ALIASES))`.

---

### N11 — Nested Function Allocated Every Row in `_build_connection()`
**File:** `DCT_Scripts/Optic_Count/cutsheet_normalizer.py:283–284`

```python
def _build_connection(row, a_device, z_device, section, status, breakout_seen):
    ...
    def _brk(col_space, col_nl):   # ← new function object created on every call
        return _normalize_cell(row.get(col_space)) or _normalize_cell(row.get(col_nl))
```

`_brk` is a closure defined inside `_build_connection`. Python creates a new function object for every call. Since `_build_connection` is called once per row in the `iterrows()` loop (N1), this creates ~4,300 throwaway function objects per Quincy upload. Minor allocation overhead, but unnecessary. `_brk` only uses `row` from the enclosing scope — it can be trivially extracted as a module-level helper.

---

### N12 — `STATUS_MAP` Has No Duplicate-Key Guard
**File:** `DCT_Scripts/Optic_Count/cutsheet_preprocessor.py:51–282`

The `STATUS_MAP` dict contains both title-case and lowercase variants of many status strings. Python silently accepts duplicate dict keys — the last occurrence wins. For example, if a contributor adds `"cable is ran: complete": COMPLETE` (lowercase) after `"Cable Is Ran: Complete": COMPLETE` (title case, already present at line 53), the lowercase entry silently overwrites the title-case entry. No error, no warning. The `_STATUS_MAP_LOWER` dict at line 285 then builds a second lowercase copy of the (now-mutated) dict, compounding the confusion.

**Recommended fix:** Add a CI-level test that asserts `len(STATUS_MAP) == len(set(STATUS_MAP.keys()))` — though Python dicts by definition have unique keys, so the real protection is asserting that the lowercase dict `_STATUS_MAP_LOWER` doesn't have fewer entries than expected (which would indicate a collision in the lowercase pass).

---

## Section 5: Grok Senior Engineer Review — Claim Verification

> Source review dated 2026-04-25, covering atlas_data_loader.py, atlas_postgres_context.py, atlas_schema.sql, atlas_query_router.py, atlas_web_app.py.

### Overall Assessment Verdict

The Grok review rates risk as **"Low-to-Medium"** with verdict **"already close to production quality."**

**This is significantly understated.** The codebase has a CRITICAL defect (C1: `managed_connection()` missing rollback) that affects every single DB call in the app — one failed query poisons a pool connection and can cascade failures to all subsequent users. The Grok review does not mention this at all. True risk level is **Medium-High**.

Other issues the review missed entirely: the unbounded `_CONNECTION_CACHE` memory leak (N2), the 4× Excel parse per upload (N4), the `ip_lookup` full-table-scan fallback (H3), and the rack-number location filter silent no-op (M1).

---

### Claim-by-Claim Verdicts

| Claim | Verdict | Notes |
|-------|---------|-------|
| `_build_status_enum_map()` runs at import time | CONFIRMED (LOW) | Correct — line 276; raises `ValueError` on slug collision which crashes the entire import |
| `load_file()` is a long monolithic transaction | CONFIRMED (MEDIUM) | Connection held open during full Excel parse; pool exhaustion risk under concurrent uploads |
| Repeated `pd.read_excel(nrows=5)` during sheet selection | CONFIRMED | Lines 860, 870, 885 — already documented as part of N4 |
| Cable ID vs Type heuristic (B11) could misclassify | UNVERIFIABLE | Internal reference not traceable from current reads |
| Hard-coded `LIMIT 200` in many templates | CONFIRMED | 8 templates use `LIMIT 200`; also `LIMIT 100`, `50`, `10` — design choice, not a bug |
| Long `format_results_for_llm()` function | CONFIRMED | Already documented as R3 (480 lines) |
| `upload_diff` special-casing is fragile | OVERSTATED | Guard at line 1516 handles missing IDs cleanly; not obviously fragile |
| `USER_CONTEXT`/`USER_SITE` lost on restart | CONFIRMED | Lines 52–53 — module-level dicts, confirmed operational risk |
| No automatic old upload cleanup → disk growth | CONFIRMED | `ATLAS_UPLOAD_DIR` grows forever; tied to H2 (stale file path bug) |
| Long functions and embedded JS | CONFIRMED | `HTML_PAGE` string starts at line 767; maintainability concern |
| Temp file handling could leak | OVERSTATED | `os.unlink()` runs in `finally` blocks (lines 530, 579, 613); only leaks on SIGKILL |

---

### G1 — `_build_status_enum_map()` Import-Time Crash Risk
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:257–276`

```python
def _build_status_enum_map() -> Dict[str, str]:
    ...
    raise ValueError(
        f"Status enum slug collision: '{slug}' produced by both ..."
    )

_STATUS_ENUM_MAP = _build_status_enum_map()   # ← runs at import time
```

**Verdict: CONFIRMED.**

If any two canonical status values in `STATUS_NORMALIZATION` produce the same slug after `re.sub(r'\W+', '_', ...)`, this raises `ValueError` at import time — before Flask even starts. The Gunicorn worker would fail to start with a cryptic traceback. In practice the current status values are distinct, but any future addition to `STATUS_NORMALIZATION` that creates a slug collision silently breaks the entire app deployment.

The fix is to validate the map in a test rather than at import time, or to catch the `ValueError` and log it rather than crash.

---

### G2 — `load_file()` Holds Pool Connection During Excel Parsing
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:766–922`

```python
with managed_connection() as conn:       # line 766 — pool connection acquired
    site_id = upsert_site(conn, ...)     # line 767 — commits
    # ... duplicate check ...
    xls = pd.ExcelFile(file_path)        # line 793 — starts heavy I/O
    # ... nrows=5 schema checks ...      # lines 860, 870, 885
    df = pd.read_excel(xls, ...)         # line 898 — full Excel parse (3–30s)
    # ... canonicalize (CPU work) ...
    conn.commit()                        # line 922 — connection finally released
```

**Verdict: CONFIRMED — the framing in the review is imprecise but the concern is real.**

The pool connection is held open for the entire Excel parsing + canonicalization + soft-delete sequence. For a 50 MB Ellendale cutsheet, `pd.read_excel()` can take 10–30 seconds. During this time, the pool has one fewer connection available. With 10 concurrent uploads and a pool of 10 (`DB_POOL_MAX=10`), the pool exhausts and all non-upload DB calls (health checks, user questions, context lookups) block or fail.

The review calls this a "long monolithic transaction" — technically the transaction does commit mid-function at `upsert_site` (line 182) and again at line 922, so it's not strictly one transaction. The real issue is **connection hold time**, not transaction length.

**Recommended fix:** Release the connection before the Excel parsing step, re-acquire it for the insert phase:
1. Acquire connection → upsert_site + duplicate check → release
2. Parse Excel (no connection held)
3. Acquire connection → insert + commit → release

---

### G3 — LIMIT 200 Hardcoded in 8 Templates (Design Choice, Not Bug)
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py`

**Verdict: CONFIRMED as a real pattern — but it is intentional, not an error.**

`LIMIT 200` appears at lines 140, 157, 193, 213, 245, 404, 414, 428. Additional limits: `LIMIT 100` (×3), `LIMIT 50` (×3), `LIMIT 10` (×1).

These limits exist to prevent LLM context overflow — the results feed into a token-limited prompt. Removing the limits without a token budget check would crash the LLM API call for large sites. Making them configurable is a reasonable improvement, but framing them as a bug overstates the issue. The limits are load-bearing constraints, not magic numbers.

The actionable concern is that a site with 5,000 connections asking "list all devices" silently truncates at 200 rows — the LLM will answer as if the result is complete. Adding a `truncated: true` flag in the result formatting would make this visible to the caller.

---

### G4 — `USER_CONTEXT` / `USER_SITE` Lost on Restart
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:52–53`

```python
USER_CONTEXT = {}   # username → context dict (2-hour TTL)
USER_SITE = {}      # username → {site_code, site_id, upload_id}
```

**Verdict: CONFIRMED.**

These module-level dicts are wiped on every Gunicorn restart, container redeploy, or pod kill. A user mid-session after a deploy gets `"No sheet loaded — upload a file first"` on their next question even though their data is in Postgres.

There is a partial recovery path (lines 645–668) that tries to re-fetch `USER_SITE` from Postgres when the in-memory dict is empty — but `USER_CONTEXT` has no equivalent recovery. The `/api/count-by-status` endpoint (H2) depends entirely on `USER_CONTEXT` having a file path, which cannot be recovered from Postgres.

---

### G5 — No Upload Directory Cleanup
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:383`

**Verdict: CONFIRMED.**

```python
save_path = UPLOAD_DIR / unique_name
f.save(save_path)
```

Uploaded `.xlsx` files accumulate in `ATLAS_UPLOAD_DIR` with no cleanup. Each unique upload generates a file named `{timestamp}_{username}_{original_name}.xlsx`. Over time this grows without bound. Combined with N2 (`_CONNECTION_CACHE` unbounded growth keyed by file path), both the disk and the in-process memory grow together as uploads accumulate.

---

### What the Grok Review Missed

These issues were not mentioned at all — all confirmed in earlier sections of this report:

| Issue | Severity | Where Documented |
|-------|----------|-----------------|
| `managed_connection()` missing rollback | CRITICAL | C1 |
| `ip_lookup` full-table-scan fallback (`%`) | HIGH | H3 |
| Inline raw SQL in `/api/ask` route | HIGH | H1 |
| `_CONNECTION_CACHE` unbounded memory leak | HIGH | N2 |
| 4× Excel parse per upload | HIGH | N4 |
| Rack-number location filter silently ignored | MEDIUM | M1 |
| `_lookup_elevation` per-cab-type workbook open | MEDIUM | B8 |
| `classify_question` accidental re-export | LOW | R1 |
| `QUESTION_TYPES` dead code | LOW | R2 |

---

### Grok Review Summary

**Legitimate findings (confirmed):** G1, G2, G3 (as design debt), G4, G5, format_results_for_llm size (R3)

**Overstated or unsupported:** temp file leak (finally blocks handle cleanup), `upload_diff` fragility (has proper guard), overall risk rating (should be Medium-High, not Low-to-Medium)

**Critical gap:** The review does not mention C1 (`managed_connection()` missing rollback) — the single highest-risk defect in the codebase.

## Section 6: Grok Second Batch Review — Claim Verification

> Source review dated 2026-04-25, covering build_sheet_processor.py, demo_web_app.py, demo_auth_ai.py, Define_Optic_Count.py, diagnose_*.py

### Overall Assessment Verdict

The second review rates risk as **"Medium"** — more accurate than the first review's "Low-to-Medium," though C1 (`managed_connection()` missing rollback) is still absent from both reviews. The "mature and feature-rich" verdict is broadly correct for functionality, but understates the performance debt in `Define_Optic_Count.py`.

---

### Claim-by-Claim Verdicts

| ID | Claim | Verdict |
|----|-------|---------|
| F1 | `build_sheet_processor.py`: multiple workbook loads, no caching | CONFIRMED — already documented B1–B2 |
| F2 | `demo_web_app.py`: `USER_CONTEXT`/`USER_SITE` lost on restart | CONFIRMED |
| F3 | `demo_web_app.py`: no automatic upload cleanup | CONFIRMED |
| F4 | `demo_web_app.py`: large embedded HTML/JS | CONFIRMED |
| F5 | `demo_web_app.py`: SSE streaming support | **FALSE** — no SSE routes exist in demo_web_app.py |
| F6 | `demo_web_app.py`: temp file handling in rack analyzer could leak | **FALSE** — demo_web_app has no rack analyzer and no tempfile usage |
| F7 | `demo_auth_ai.py`: `_SOX_SECTION_CACHE` global and never cleared | CONFIRMED but MISLEADING — correct lazy-load pattern for static data |
| F8 | `demo_auth_ai.py`: PDF parsing on every compliance question | **FALSE** — cached after first call; does NOT re-parse per question |
| F9 | `demo_auth_ai.py`: `_build_grounded_messages()` is long | CONFIRMED — 119 lines (398–516) |
| F10 | `demo_auth_ai.py`: `_trim_context_for_llm()` is long | OVERSTATED — ~50 lines (179–228) |
| F11 | `Define_Optic_Count.py`: very large file | CONFIRMED — 1,440 lines |
| F12 | `Define_Optic_Count.py`: good caching (`_XLS_CACHE`, `_DF_CACHE`) | CONFIRMED — proper module-level caching with `clear_excel_cache()` |
| F13 | `Define_Optic_Count.py`: repeated Excel parsing | PARTIALLY CONFIRMED — caching mitigates most; still present in some paths |
| F14 | `Define_Optic_Count.py`: 22 `iterrows()` calls | **NEW — UNDERSTATED** — present but not flagged prominently; worst bottleneck in codebase |

---

### F2 / F3 — `demo_web_app.py` In-Memory State and No Cleanup
**File:** `DCT_Scripts/Optic_Count/demo_web_app.py:24–25`

```python
USER_CONTEXT = {}   # line 24
USER_SITE = {}      # line 25
```

**Verdict: CONFIRMED.** Same pattern as `atlas_web_app.py` (G4). Both apps carry independent in-memory session state that is wiped on restart. `demo_web_app.py` has `_evict_stale_contexts()` (line 190) for time-based TTL eviction of in-memory context, but no file cleanup for the upload directory. The two web apps share the same operational gap.

---

### F5 — `demo_web_app.py` Does NOT Have SSE Streaming
**File:** `DCT_Scripts/Optic_Count/demo_web_app.py`

**Verdict: FALSE.**

The review states "SSE streaming support" as a strength of `demo_web_app.py`. The actual routes in `demo_web_app.py` are:
- `GET /api/health`
- `POST /api/demo-verify-pin`
- `POST /api/upload-count`
- `POST /api/sheet-qa`
- `GET /api/audit-log`
- `GET /`

There is no `/api/stream/netbox`, no `/api/stream/all-sites`, no `text/event-stream` response, and no SSE infrastructure. SSE streaming lives exclusively in `atlas_web_app.py`. This is an incorrect attribution.

---

### F6 — `demo_web_app.py` Has No Temp File Handling at All
**File:** `DCT_Scripts/Optic_Count/demo_web_app.py`

**Verdict: FALSE.**

The review claims "Temp file handling in rack analyzer could leak" for `demo_web_app.py`. Searching the file finds zero occurrences of `tempfile`, `NamedTemporaryFile`, or `os.unlink`. `demo_web_app.py` does not have a rack analyzer — the `/api/buildsheet` route family exists only in `atlas_web_app.py`. This is a misattribution from the batch review structure.

---

### F7 — `_SOX_SECTION_CACHE` — Lazy-Load Pattern Is Correct, "Never Cleared" Is Misleading
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:62, 604–629`

```python
_SOX_SECTION_CACHE = None

def _load_sox_sections() -> Dict[str, Any]:
    global _SOX_SECTION_CACHE
    if _SOX_SECTION_CACHE is not None:
        return _SOX_SECTION_CACHE
    ...   # parse PDF once, store result
    return _SOX_SECTION_CACHE
```

**Verdict: CONFIRMED as a fact, but the concern is overstated.**

The cache is populated on first access and never invalidated. This IS the correct pattern for static data that does not change during the process lifetime. The only scenario where "never cleared" is a bug is if the SOX PDF file is updated on disk while the app is running — the old parsed content would continue to be served. This is a LOW risk operational concern, not a code defect.

There is no `clear_sox_cache()` function, so an operator cannot force a reload without restarting the process. Adding one as an admin endpoint would address this completely.

---

### F8 — PDF Is NOT Parsed Per Question (False Finding)
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:604–629`

**Verdict: FALSE.**

The review states: "PDF parsing happens on every compliance question (expensive)."

This is factually wrong. `_load_sox_sections()` checks `if _SOX_SECTION_CACHE is not None: return _SOX_SECTION_CACHE` at line 606 before doing any I/O. After the first compliance question, all subsequent calls return the cached dict immediately with no file I/O or PDF parsing. The expensive operation runs exactly once per process lifetime.

---

### F9 — `_build_grounded_messages()` Is Long (119 Lines)
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:398–516`

**Verdict: CONFIRMED.**

`_build_grounded_messages()` spans lines 398–516 = 119 lines. It handles: Postgres context branch, in-memory branch, rack result branch, compliance branch, and legacy branch — each building a different message list for the LLM. Long but not as extreme as `format_results_for_llm()` (R3, 480 lines). The complexity is inherent to the branching context logic, though the branches could be extracted into named helpers.

---

### F10 — `_trim_context_for_llm()` Is NOT Notably Long
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:179–228`

**Verdict: OVERSTATED.**

`_trim_context_for_llm()` spans lines 179–228 = ~50 lines. The review groups it with `_build_grounded_messages()` as a "long function" concern. At 50 lines it is well within normal range and does not need splitting. The review overstates this.

---

### F14 — NEW: `Define_Optic_Count.py` Has 22 `iterrows()` Calls
**File:** `DCT_Scripts/Optic_Count/Define_Optic_Count.py`

**Verdict: CONFIRMED AND CRITICALLY UNDERSTATED.**

The Grok review mentions "repeated Excel parsing" as a concern for `Define_Optic_Count.py` but does not call out the `iterrows()` bottleneck specifically. The actual count is **22 separate `iterrows()` loops** across the file:

```
Lines: 245, 287, 304, 330, 369, 483, 534, 547, 557, 590, 
       604, 616, 648, 684, 795, 802, 811, 848, 911, 943, 1124, 1189
```

This is worse than `cutsheet_normalizer.py` (N1, 4 loops) and represents the most pervasive `iterrows()` usage in the entire codebase. The `_XLS_CACHE`/`_DF_CACHE` mitigate repeated file I/O, but once the DataFrame is in memory, every counting function iterates row-by-row. For a 4,300-row Quincy cutsheet this is manageable; for Ellendale-scale (~20K rows), the legacy path becomes a hard performance wall.

The review correctly identifies the `_XLS_CACHE`/`_DF_CACHE` caching as a strength, but the `iterrows()` pattern that underpins the entire counting logic is a significant bottleneck left unaddressed.

---

### What the Second Grok Review Missed

| Issue | Severity | Where documented |
|-------|----------|-----------------|
| C1: `managed_connection()` missing rollback | CRITICAL | C1 — missed again |
| 22 `iterrows()` in `Define_Optic_Count.py` | HIGH | F14 (this section) |
| SSE mis-attributed to `demo_web_app.py` | Factual error | F5 |
| Rack analyzer temp file mis-attributed to `demo_web_app.py` | Factual error | F6 |
| PDF caching claim inverted | Factual error | F8 |

### Second Review Summary

**Legitimate findings (confirmed):** F2, F3, F4, F7 (mild concern), F9, F11, F12, F13 (partially), F14 (understated)

**False or misattributed:** F5 (SSE in demo_web_app), F6 (temp files in demo_web_app), F8 (PDF per-question parsing)

**Overstated:** F10 (`_trim_context_for_llm` size)

**Critical gap:** C1 is absent from both Grok reviews. The most impactful single fix in the codebase — adding `conn.rollback()` to `managed_connection()` — was not identified in either pass.

## Section 7: Grok Third Batch Review — Claim Verification

> Source review dated 2026-04-25, covering Optic_Count_GUI_Main.py, Optic_Count_GUI.py, IbOpticCount.py, OpticType.py, Netbox_query.py, Optic_Count.py

### Overall Assessment Verdict

Risk level "Medium" is accurate for this batch. The files reviewed are genuinely lower-stakes (GUI, legacy CLI, one-off scripts) compared to the core DB/Flask stack. However, two factual errors and two significant findings are absent.

---

### Claim-by-Claim Verdicts

| ID | Claim | Verdict |
|----|-------|---------|
| P1 | GUI: functional Tkinter with threading, read-only widgets | CONFIRMED |
| P2 | GUI: global mutable state (`files_to_count`, `current_sheet_context`) | CONFIRMED — plus undocumented third global `session_token` |
| P3 | GUI: legacy counting path dominant | CONFIRMED — all 4 count buttons call `Define_Optic_Count.*` |
| P4 | GUI: no error handling around missing logo asset | CONFIRMED — `get_logo()` has no try/except |
| P5 | GUI: scrollbar/canvas setup complex and fragile | CONFIRMED |
| P6 | `IbOpticCount.py`: hard-coded column "I" | CONFIRMED — `usecols="I"` |
| P7 | `IbOpticCount.py`: no status-aware counting | CONFIRMED |
| P8 | `Netbox_query.py`: hard-coded `SITE_ID` and `TARGET_RACKS` | CONFIRMED |
| P9 | `Netbox_query.py`: no error handling for missing token | CONFIRMED — `os.environ["KEY"]` raises `KeyError` |
| P10 | `Netbox_query.py`: SSL context created repeatedly | OVERSTATED — created once in `main()`, passed through |
| P11 | `Optic_Count.py`: duplicates `Define_Optic_Count` logic | CONFIRMED |
| P12 | `OpticType.py`: could be replaced with `Counter` | CONFIRMED |
| P13 | NEW: `Optic_Count.py` has no `__main__` guard | **MISSED** — entire script runs at import time |
| P14 | NEW: `IbOpticCount.py` opens workbook N times for N sheets | **MISSED** — no `pd.ExcelFile` wrapper in the sheet loop |
| P15 | NEW: `Netbox_query.py` hard-codes the NetBox base URL | **MISSED** — `coreweave.cloud.netboxapp.com` is not an env var |

---

### P4 — `get_logo()` Has No Error Handling — GUI Crashes on Missing Asset
**File:** `DCT_Scripts/Optic_Count/Optic_Count_GUI.py:15–20`

```python
def get_logo():
    from PIL import Image, ImageTk
    image_path = resource_path(os.path.join("assets", "CoreWeave_Logo.png"))
    cw_logo = Image.open(image_path)     # ← no try/except
    resized_image = cw_logo.resize((150, 100), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(resized_image)
```

**Verdict: CONFIRMED.**

If `assets/CoreWeave_Logo.png` is absent (frozen app packaging issue, missing from repo, wrong working directory), `Image.open()` raises `FileNotFoundError` with no fallback. The GUI will fail to start with a raw traceback rather than a graceful "logo not found, continuing without it" message. The `resource_path()` helper correctly handles PyInstaller's `sys._MEIPASS`, but does not verify the file exists before passing it to PIL.

---

### P9 — `Netbox_query.py` KeyError on Missing Token
**File:** `DCT_Scripts/Optic_Count/Netbox_query.py`

```python
def _build_headers():
    token = os.environ["NETBOX_API_TOKEN"]   # ← KeyError if unset
    ...
```

**Verdict: CONFIRMED.**

`os.environ["KEY"]` raises a bare `KeyError: 'NETBOX_API_TOKEN'` with no explanation when the env var is not set. The error message gives no hint to the user that they need to set the variable. `os.environ.get("NETBOX_API_TOKEN")` with a guard and a clear error message would be a one-line improvement.

---

### P10 — SSL Context "Created Repeatedly" Is Overstated
**File:** `DCT_Scripts/Optic_Count/Netbox_query.py`

**Verdict: OVERSTATED.**

`main()` creates `ctx = _ssl_ctx()` once (line `ctx = _ssl_ctx()`) and passes it explicitly to both `get_all()` calls. The `ssl_ctx=None` default in `get_json()`/`get_all()` only creates a new context as a fallback when the caller doesn't pass one. In normal usage via `main()`, one SSL context serves the entire run. The concern is real only if someone calls `get_json()` or `get_all()` directly without passing `ssl_ctx` — a defensive programming gap, not a confirmed bug.

---

### P13 — NEW: `Optic_Count.py` Has No `__main__` Guard
**File:** `DCT_Scripts/Optic_Count/Optic_Count.py`

**Verdict: CONFIRMED — not in Grok review.**

```python
import Define_Optic_Count

files_to_count = Define_Optic_Count.menu()   # ← executes at import time

for file in files_to_count:
    ...
```

The entire script — including the interactive `menu()` prompt — executes at module import time. There is no `if __name__ == "__main__":` guard. If any other module imports `Optic_Count`, it will immediately block waiting for interactive terminal input. In practice, `Optic_Count_GUI_Main.py` imports `Define_Optic_Count` and `Optic_Count_GUI` (not `Optic_Count`), so this isn't currently triggered. But it is a latent correctness bug that would produce confusing behavior if the module were ever imported in a test or shared context.

---

### P14 — NEW: `IbOpticCount.py` Opens Workbook N Times (No `pd.ExcelFile` Wrapper)
**File:** `DCT_Scripts/Optic_Count/IbOpticCount.py`

```python
for sheet_name in xls.sheet_names:
    ...
    df = pd.read_excel(file_path, sheet_name=sheet_name, usecols="I")
```

**Verdict: CONFIRMED — not in Grok review.**

`pd.ExcelFile(file_path)` is opened once (outer `xls = pd.ExcelFile(file_path)`), but then `pd.read_excel(file_path, ...)` is called inside the loop with the **string path**, not the already-open `xls` object. This reopens and re-parses the zip archive for each sheet. For a file with 10 tabs, the workbook is opened 11 times (1 outer + 10 inner). Passing `xls` instead of `file_path` to the inner `pd.read_excel()` call would fix this.

---

### P15 — NEW: `Netbox_query.py` Hard-Codes the NetBox Base URL
**File:** `DCT_Scripts/Optic_Count/Netbox_query.py`

**Verdict: CONFIRMED — not in Grok review.**

```python
locations = get_all(
    f"https://coreweave.cloud.netboxapp.com/api/dcim/locations/?site_id={SITE_ID}&limit=1000",
    ...
)
```

`SITE_ID`, `TARGET_RACKS`, and the base URL are all hardcoded. The review calls out `SITE_ID` and `TARGET_RACKS` but misses the URL. Moving all three to env vars (`NETBOX_BASE_URL`, `NETBOX_SITE_ID`, `NETBOX_TARGET_RACKS`) would make this script usable across environments without code changes.

---

### Third Review Summary

**Legitimate findings (confirmed):** P1–P9, P11, P12

**Overstated:** P10 (SSL context)

**Missed (new findings added):** P13 (`Optic_Count.py` no `__main__` guard), P14 (`IbOpticCount.py` workbook reopened per sheet), P15 (hardcoded NetBox URL)

**Persistent gap:** C1 (`managed_connection()` missing rollback) is absent from all three Grok reviews.

## Section 8: Grok Final Consolidated Review — Claim Verification

> Source review dated 2026-04-25. This is a synthesis across all previously reviewed files. Most claims are rehashes of Sections 5–7; new specifics are verified below.

### Overall Assessment

The "Medium" risk rating and "production-minded platform" verdict are reasonable for the GUI/legacy layer. The core DB+Flask stack carries higher risk than "Medium" due to C1 (still absent for the fourth consecutive review). The synthesis is accurate as a high-level summary.

---

### New Specific Claims Not Previously Verified

| ID | Claim | Verdict |
|----|-------|---------|
| Q1 | `demo_auth_ai.py`: Repeated SSL context creation | **CONFIRMED** — `_build_ssl_context()` called inline on every API request and every retry |
| Q2 | `demo_auth_ai.py`: Hard-coded Anthropic/OpenAI fallback | CONFIRMED — lines 839–867 |
| Q3 | `demo_auth_ai.py`: Uses raw `urllib.request` instead of Anthropic SDK | **NEW** — missed in all four reviews; loses SDK retry/rate-limit/error-handling |

### Previously Confirmed Claims Restated in This Review (No Re-Verification Needed)

All claims about GUI globals, legacy `iterrows()`, build sheet workbook loads, `USER_CONTEXT` state loss, and no upload cleanup are confirmed — see Sections 4–7 for full detail.

---

### Q1 — SSL Context Created on Every API Call and Every Retry
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:710–720, 763, 815`

```python
def _build_ssl_context() -> ssl.SSLContext:
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()

# Called inline on every request:
with urllib.request.urlopen(req, timeout=timeout, context=_build_ssl_context()) as resp:
```

**Verdict: CONFIRMED — and more serious than in `Netbox_query.py` (P10, where it was overstated).**

`_build_ssl_context()` is called as an inline argument to `urlopen()` — a new `ssl.SSLContext` object is allocated on every API call. `_call_anthropic()` has a retry loop; if Anthropic returns a 5xx error, each retry creates another SSL context. For a 3-retry call that ultimately fails, 3 SSL contexts are created and discarded.

SSL context creation involves reading CA bundle files from disk (`certifi.where()`) and is measurably more expensive than reusing a cached context. The fix is a single module-level constant:

```python
_SSL_CTX = _build_ssl_context()   # create once at module load
```

---

### Q2 — Hard-Coded Anthropic/OpenAI Provider Routing
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:839–867`

**Verdict: CONFIRMED.**

The provider selection logic (`_call_llm()`) checks env vars at call time and falls back from Anthropic to OpenAI silently. The routing logic is correct but not configurable — there is no env var to force one provider, skip the fallback, or add a third provider without code changes.

---

### Q3 — NEW: `demo_auth_ai.py` Uses Raw `urllib.request` Instead of Anthropic SDK
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:723–834`

**Verdict: CONFIRMED — missed in all four Grok reviews.**

`_call_anthropic()` and `_call_openai()` build raw HTTP requests using `urllib.request.Request` and `urllib.request.urlopen()` rather than the official `anthropic` Python SDK. This means:

- **No automatic rate-limit handling** — 429 responses must be handled manually (they appear to be via `last_error` retry logic, but this is a reimplementation of what the SDK does natively)
- **No streaming support** via the SDK's streaming interface
- **No automatic prompt caching headers** — the SDK can inject `cache-control` headers for Anthropic's prompt caching feature automatically
- **SSL context managed manually** (Q1) rather than by the SDK's httpx transport layer
- **Model version negotiation** — hardcoded model strings rather than using SDK defaults

The CLAUDE.md confirms `claude-sonnet-4-6` is the primary model. The Anthropic SDK is already in `requirements.txt` (`anthropic>=0.18`). Using `urllib.request` instead of the installed SDK adds maintenance burden without benefit.

---

### Persistent Pattern Across All Four Grok Reviews

C1 (`managed_connection()` missing rollback) was not identified in any of the four reviews. The full absence table:

| Issue | Section | Grok Pass 1 | Grok Pass 2 | Grok Pass 3 | Grok Pass 4 |
|-------|---------|-------------|-------------|-------------|-------------|
| C1: `managed_connection()` no rollback | C1 | ✗ | ✗ | ✗ | ✗ |
| H3: `ip_lookup` wildcard scan | H3 | ✗ | ✗ | ✗ | ✗ |
| N2: `_CONNECTION_CACHE` memory leak | N2 | ✗ | ✗ | ✗ | ✗ |
| Q3: raw urllib instead of Anthropic SDK | Q3 | ✗ | ✗ | ✗ | ✗ |
| F14: `Define_Optic_Count.py` 22 iterrows | F14 | ✗ | Partial | ✗ | ✗ |

These are not obscure edge cases — they affect every production DB call (C1), every LLM API request (Q3), and memory stability under load (N2). Use AI-generated code reviews as a starting checklist, not a final audit.

## Section 9: Full Line-by-Line Verification — All Structural Files

> Review conducted 2026-04-25. Six parallel agents read every line of atlas_data_loader.py, atlas_web_app.py, atlas_query_router.py, atlas_postgres_context.py, demo_auth_ai.py, cutsheet_profiles.py, cutsheet_normalizer.py, cutsheet_preprocessor.py, build_sheet_processor.py, and demo_web_app.py.

### Verification Status of All Existing Findings

All previously documented findings were re-confirmed at their stated line numbers:

| ID | Status | File | Lines |
|----|--------|------|-------|
| C1 | CONFIRMED | atlas_data_loader.py | 117–125 |
| H1 | CONFIRMED | atlas_web_app.py | 647–664 |
| H2 | CONFIRMED | atlas_web_app.py | 462–463 |
| H3 | CONFIRMED | atlas_query_router.py | 968–974 |
| M1 | CONFIRMED | atlas_query_router.py | 905–906 |
| M2 | CONFIRMED | atlas_query_router.py | 1006, 1011 |
| M3 | CONFIRMED | atlas_postgres_context.py | 65–77, 136–147 |
| M4 | CONFIRMED | atlas_web_app.py | 526–527, 575–576, 609–610 |
| M5 | CONFIRMED | atlas_data_loader.py | 138–156 |
| L1 | CONFIRMED | atlas_data_loader.py | 128–135 |
| R1–R6 | CONFIRMED | atlas_query_router.py | Various |
| N1–N12 | CONFIRMED | cutsheet files | Various |
| B1–B8 | CONFIRMED | build_sheet_processor.py | Various |
| G1–G5 | CONFIRMED | atlas_data_loader.py / atlas_web_app.py | Various |
| Q1–Q3 | CONFIRMED | demo_auth_ai.py | Various |
| F2–F9 | CONFIRMED | demo_web_app.py / demo_auth_ai.py | Various |
| F5, F6 | CONFIRMED FALSE | demo_web_app.py | — |

---

### NEW Findings — atlas_data_loader.py

#### V1 — Multi-Commit Atomicity Violation in `load_file()`
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py`
**Severity: HIGH**

`load_file()` calls seven separate functions that each call `conn.commit()` independently:

| Function | commit() line |
|----------|--------------|
| `upsert_site()` | 182 |
| `create_upload()` | 212 |
| Soft-delete of previous uploads | 922 |
| `load_cutsheet()` | 536 |
| `load_site_hosts()` | 592 |
| `load_burndown()` | 693 |
| `backfill_device_roles()` | 740 |

If `load_site_hosts()` (step 5) raises an exception, `cutsheet_connections` data (step 4) is already committed and visible to queries. The upload record exists but host enrichment is absent. Materialized views refresh against partially-loaded data. This violates atomicity — the upload either should fully succeed or fully roll back.

**Fix:** Remove `conn.commit()` from individual helpers; commit once at the end of `load_file()` or use a savepoint pattern.

---

#### V2 — Raw Row Mispairing After Deduplication
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:513–526`
**Severity: HIGH**

```python
cur.execute(
    "SELECT id FROM cutsheet_connections WHERE upload_id = %s ORDER BY id",
    (upload_id,),
)
inserted_ids = cur.fetchall()
raw_tuples = [
    (r[0], rows[i][-1]) for i, r in enumerate(inserted_ids)
    if i < len(rows)
]
```

The INSERT uses `ON CONFLICT DO NOTHING` (deduplication). If row at index 1 is a duplicate and is skipped, `inserted_ids` has gaps: `[id0, id2]`. The zip pairs `id2` with `rows[1][-1]` instead of `rows[2][-1]` — wrong raw JSON is stored against the wrong connection ID.

This is a silent data correctness bug. The code comment at line ~521 acknowledges the assumption ("zip is safe"), but `ON CONFLICT DO NOTHING` invalidates it.

**Fix:** Use a CTE that returns the `(connection_id, raw_json)` pair at insert time rather than a post-hoc re-query.

---

#### V3 — Exception Swallowed in Host and Burndown Loading
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py`
**Severity: MEDIUM**

Host loading failure (line ~943) is caught with:
```python
except Exception as exc:
    log.warning("Host loading failed: %s", exc)
```

The exception is swallowed and `load_file()` returns `{"ok": True, "hosts_loaded": 0}`. The caller in `atlas_web_app.py` treats this as success. Users get no indication that host enrichment failed. The same pattern applies to burndown loading. Combined with V1, this means partial loads silently succeed.

---

#### V4 — Soft-Delete Does Not Cascade to Child Tables
**File:** `DCT_Scripts/Optic_Count/atlas_data_loader.py:912–922`
**Severity: MEDIUM**

```python
cur.execute(
    "UPDATE cutsheet_uploads SET is_active = FALSE "
    "WHERE site_id = %s AND is_active = TRUE",
    (site_id,),
)
```

Previous uploads are soft-deleted at the `cutsheet_uploads` level, but `cutsheet_connections`, `cutsheet_raw_rows`, `host_inventory`, and `burndown_connections` rows for those old uploads are NOT marked inactive — they remain queryable by upload_id. Materialized views must apply `upload_id` filtering consistently or they will join deactivated data. Any view or query that JOINs `cutsheet_uploads` without filtering `is_active = TRUE` will include stale rows.

---

### NEW Findings — atlas_web_app.py

#### V5 — Raw Exception Messages Returned to API Clients
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:435, 576, 610`
**Severity: CRITICAL (Security)**

```python
return jsonify({"error": f"Failed to parse file: {exc}"}), 500  # line 435
return jsonify({"error": str(exc)}), 500                         # line 576, 610
```

Raw `Exception` objects are serialized directly into API responses. This leaks:
- Internal file paths: `/tmp/1745000000_demo_user_cutsheet.xlsx`
- Python module names and stack hints from `openpyxl`, `pandas`, `psycopg2`
- Database error details (table names, column names, constraint names)
- Server directory structure via `FileNotFoundError`

**Fix:** Map all exceptions to generic user-facing messages. Log the full exception server-side.

---

#### V6 — X-Forwarded-For Spoofing Bypasses Rate Limiting
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:93–97`
**Severity: HIGH (Security)**

```python
def _get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"
```

The `X-Forwarded-For` header is accepted and trusted without validation. An attacker can set `X-Forwarded-For: 1.2.3.4` to spoof any IP address, bypassing the rate limiter entirely. Rate limiting is rendered ineffective against any client that can set HTTP headers.

**Fix:** Only trust `X-Forwarded-For` when requests arrive from known internal proxy IPs. Use `request.remote_addr` directly if no reverse proxy is deployed, or validate the chain against an allowlist.

---

#### V7 — Rate Limit Store O(n) Cleanup Under Lock
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:100–115`
**Severity: HIGH**

```python
if len(_RATE_LIMIT_STORE) > _RATE_LIMIT_MAX_KEYS:
    expired = [k for k, (_, ws) in _RATE_LIMIT_STORE.items()
               if now - ws > _RATE_LIMIT_WINDOW]
    for k in expired:
        del _RATE_LIMIT_STORE[k]
```

Cleanup only triggers when the store exceeds 5,000 keys. At that point, the entire dict is scanned under `_state_lock`, blocking all concurrent requests. An attacker sending requests from 5,000 unique IPs can trigger this cleanup on every subsequent request, creating lock contention that degrades throughput.

---

#### V8 — SSE Queue Has No maxsize and No Timeout on `q.get()`
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:230–251`
**Severity: MEDIUM**

```python
q = queue.Queue()  # No maxsize
...
msg = q.get()      # No timeout — blocks forever if producer dies
```

Two problems: (1) if a client disconnects mid-stream, the producer thread keeps writing to the queue unbounded; (2) if the producer crashes before sending `None`, the consumer `q.get()` blocks the Flask worker thread forever, exhausting the thread pool under load.

**Fix:** `queue.Queue(maxsize=500)` and `q.get(timeout=60)` with a break on `queue.Empty`.

---

#### V9 — No File Size Limit on Upload
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:362–383` and `demo_web_app.py:17`
**Severity: HIGH**

Neither web app sets Flask's `MAX_CONTENT_LENGTH`. Werkzeug defaults to unlimited request body size. An attacker can upload a multi-gigabyte file that:
1. Saturates disk via `f.save(save_path)`
2. Blocks the Gunicorn worker thread for minutes during write
3. Then starves the Postgres background load thread

**Fix:** `app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024` (100 MB) and check the per-file size explicitly.

---

#### V10 — No Timeout on Postgres Context Build
**File:** `DCT_Scripts/Optic_Count/atlas_web_app.py:671–688`
**Severity: MEDIUM**

`build_postgres_context()` is called synchronously in the `/api/ask` request handler with no timeout. If a complex SQL query (e.g., `rack_summary` on a large site, `upload_diff` on large datasets) hangs, the Flask worker thread blocks indefinitely. Under Gunicorn's 4-thread model, 4 concurrent hung queries exhaust all workers.

---

### NEW Findings — atlas_query_router.py

#### V11 — `upload_diff` Returns Empty on NULL Parameters (Silent Data Loss)
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:551–566, 976–979`
**Severity: HIGH**

The `upload_diff` SQL template uses:
```sql
WHERE upload_id = %(upload_id_a)s::bigint
```

When `extract_upload_ids()` fails to find IDs in the question, `upload_id_a` and `upload_id_b` are `None`. psycopg2 sends `NULL` for Python `None`, making the condition `upload_id = NULL::bigint`. In SQL, `x = NULL` is always `NULL` (not `TRUE`), so zero rows are returned. The guard at line 1516 checks for this **after** the query runs, but by that point the user has already received an empty result.

The guard at line 1516 does catch this and returns a helpful message — so this is a performance waste (query runs unnecessarily) plus a UX issue (confusing "no differences" before the guard message).

---

#### V12 — `_escape_ilike()` Called with Potential `None` Causes `AttributeError`
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:882–884, 942–950`
**Severity: MEDIUM**

```python
def _escape_ilike(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

If any extractor returns `None` (e.g., `ext.extract_device_name()` on a question with no device), and that `None` is passed directly to `_escape_ilike()`, it raises `AttributeError: 'NoneType' object has no attribute 'replace'`. The outer `except Exception` at line 1578 catches this, but the user sees a generic "routing failed" error with no explanation.

Each `build_query_params()` call at lines 942–950 has guards (`if _device else "%"`), but the guards are inconsistent — some use `if _device` (correct) while others call `f"%{_escape_ilike(_device)}%"` without checking first (line 950 has the guard, so this is actually correct in the current code). Worth auditing all 9 extractor usages for consistency.

---

#### V13 — Location Zero-Padding Causes False Negatives in Rack Matching
**File:** `DCT_Scripts/Optic_Count/atlas_query_router.py:898–900`
**Severity: MEDIUM**

```python
m = re.fullmatch(r"([a-z]{1,4}\d+):(\d{1,4})", loc)
if m:
    hall, rack = m.groups()
    return f"{_escape_ilike(hall)}%:{_escape_ilike(rack.zfill(3))}:%"
```

`rack.zfill(3)` pads the rack number to 3 digits. If a user asks about "dh202:41" (rack 41), the pattern becomes `dh202%:041:%`. If the database stores `dh202:41:10`, the ILIKE match fails because `041` ≠ `41`. The stored location format determines whether zero-padding applies, but this code assumes it always does.

This is separate from M1 (pure rack numbers returning `""`): this affects hall:rack format queries and produces false negatives — queries that find nothing when data exists.

---

### NEW Findings — atlas_postgres_context.py

#### V14 — `route_question()` Called Without try/except
**File:** `DCT_Scripts/Optic_Count/atlas_postgres_context.py:79`
**Severity: HIGH**

```python
result = route_question(question, site_id, upload_id=upload_id)
if not result.get("ok"):
    return {...}
```

`route_question()` raises `Exception` when routing fails (it has its own try/except at line 1578 of `atlas_query_router.py` that returns `{"ok": False}`). However, if `route_question()` itself raises before reaching that handler (e.g., a `KeyError` in param building, an import error), the exception propagates unhandled from `build_postgres_context()`. The caller in `atlas_web_app.py` wraps this in `try/except` (line 686), but the fallback is `pg_context = None` — the user gets an in-memory fallback with no explanation.

---

### NEW Findings — demo_auth_ai.py

#### V15 — `TOKEN_SECRET` Validated at Call Time, Not Import Time
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:57–60`
**Severity: HIGH**

```python
TOKEN_SECRET = os.getenv("DEMO_TOKEN_SECRET", "").encode("utf-8")

def _require_token_secret():
    if not TOKEN_SECRET:
        raise RuntimeError("DEMO_TOKEN_SECRET must be set...")

def create_demo_token(username: str) -> str:
    _require_token_secret()  # Only checked when called
```

The app starts fine with a missing `DEMO_TOKEN_SECRET`. The first user to authenticate gets a `RuntimeError` with a raw Python traceback. This is caught by the route handler (line 369), which returns the error string to the client — leaking that `DEMO_TOKEN_SECRET` is unset.

**Fix:** Add `_require_token_secret()` at module level so the app fails to start, not fails mid-request.

---

#### V16 — Anthropic and OpenAI API Response Not Validated Before Access
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:764–775, 816–827`
**Severity: HIGH**

```python
# Anthropic path (line 766)
answer = "".join(block.get("text", "") for block in body.get("content", []))

# OpenAI path (line 818)
answer = body["choices"][0]["message"]["content"]  # KeyError if structure changes
```

The OpenAI path uses direct dict indexing without guards. If the API returns an unexpected structure (rate-limit response, API change, partial error), `body["choices"][0]["message"]["content"]` raises `KeyError` or `IndexError`. The Anthropic path uses `.get()` (safer), but both paths lack structural validation. A malformed response crashes with an unhandled exception that propagates to the user.

---

#### V17 — OpenAI Path Has No Retry Logic
**File:** `DCT_Scripts/Optic_Count/demo_auth_ai.py:792–834`
**Severity: MEDIUM**

`_call_anthropic()` implements exponential backoff with retries on 500/503/529 (lines 761–786). `_call_openai()` has no equivalent — a 503 from OpenAI returns immediately as a failure with no retry. If Anthropic is unavailable and OpenAI is the fallback, transient errors are not retried, making the fallback less reliable than the primary.

---

### NEW Findings — cutsheet_profiles.py

#### V18 — `apply_profile()` Can Silently Drop Rows of Data
**File:** `DCT_Scripts/Optic_Count/cutsheet_profiles.py:478–504`
**Severity: MEDIUM**

When two source columns map to the same Canon target (e.g., both "A-SIDE-DNS-NAME" and "A-SIDE DEVICE NAME" map to `Canon.A_DEVICE`), the conflict resolution at lines 478–504:
1. Logs how many rows disagree
2. **Drops the fallback column entirely** (`drop_cols.append(source_col)`)

If the primary column has 100 non-empty rows and the fallback has 150, dropping the fallback silently discards 50 rows' worth of device name data. No warning indicates data was lost — only a conflict count log. For a site where the two columns have complementary coverage (non-overlapping rows), this causes silent data loss in the ingest pipeline.

---

### NEW Findings — build_sheet_processor.py

#### V19 — `openpyxl` Workbook Not Closed on Exception
**File:** `DCT_Scripts/Optic_Count/build_sheet_processor.py:352–363`
**Severity: HIGH**

```python
wb_cut = openpyxl.load_workbook(cutsheet_path, read_only=True, data_only=True)
cut_tab = _find_cutsheet_tab(wb_cut) or 'CUTSHEET'
cables_raw = _read_sheet(wb_cut, cut_tab, CUTSHEET_COLS)
hosts_raw = _read_sheet(wb_cut, 'SITE-HOSTS', HOSTS_COLS)
for h in hosts_raw: ...
wb_cut.close()  # ← never reached if exception above
```

If `_find_cutsheet_tab()`, `_read_sheet()`, or the host loop raises, `wb_cut.close()` is never called. openpyxl holds the file descriptor open (and the zip archive in memory) until GC. Under concurrent buildsheet requests, this can exhaust file descriptors.

Same pattern at line 527 in `process_room()` and line 586 in `generate_layout_workbook()`.

**Fix:** Use `with openpyxl.load_workbook(...) as wb_cut:` or wrap in try/finally.

---

### NEW Findings — demo_web_app.py

#### V20 — `context["summary"]` Access Without Key Check
**File:** `DCT_Scripts/Optic_Count/demo_web_app.py:332`
**Severity: MEDIUM**

```python
return jsonify({
    "ok": True,
    "file": safe_name,
    "output": result_text,
    "summary": context["summary"],    # ← KeyError if key absent
    ...
})
```

If `Define_Optic_Count.count_and_build_context()` or `cutsheet_preprocessor.preprocess_upload()` returns a context dict without a `"summary"` key (fallback paths, error conditions), this line raises `KeyError` with a raw 500 response. The fix is `context.get("summary", {})`.

---

### Consolidated New Findings Summary

| ID | Severity | File | Lines | Description |
|----|----------|------|-------|-------------|
| V1 | HIGH | `atlas_data_loader.py` | 182, 212, 536, 592, 693, 740, 922 | 7 separate commits in `load_file()` violate atomicity |
| V2 | HIGH | `atlas_data_loader.py` | 513–526 | Raw row index mispairing after `ON CONFLICT DO NOTHING` dedup |
| V3 | MEDIUM | `atlas_data_loader.py` | 943–944 | Host/burndown load exceptions swallowed; partial success returned |
| V4 | MEDIUM | `atlas_data_loader.py` | 912–922 | Soft-delete doesn't cascade; child tables retain stale rows |
| V5 | CRITICAL | `atlas_web_app.py` | 435, 576, 610 | Raw exception messages returned to API clients (info disclosure) |
| V6 | HIGH | `atlas_web_app.py` | 93–97 | `X-Forwarded-For` trusted unconditionally; rate limiting bypassed by IP spoofing |
| V7 | HIGH | `atlas_web_app.py` | 100–115 | Rate limit store O(n) cleanup under lock — DoS amplification |
| V8 | MEDIUM | `atlas_web_app.py` | 230–251 | SSE queue unbounded; `q.get()` blocks forever if producer dies |
| V9 | HIGH | `atlas_web_app.py` + `demo_web_app.py` | 17 | No `MAX_CONTENT_LENGTH`; unlimited file upload size |
| V10 | MEDIUM | `atlas_web_app.py` | 671–688 | No timeout on `build_postgres_context()`; hangs exhaust workers |
| V11 | HIGH | `atlas_query_router.py` | 551–566, 976–979 | `upload_diff` runs query with NULL params; empty result before guard |
| V12 | MEDIUM | `atlas_query_router.py` | 882–884 | `_escape_ilike(None)` raises `AttributeError` if extractor returns None |
| V13 | MEDIUM | `atlas_query_router.py` | 898–900 | `rack.zfill(3)` causes ILIKE false negatives if DB stores unpadded rack numbers |
| V14 | HIGH | `atlas_postgres_context.py` | 79 | `route_question()` called without try/except; unhandled raise silently falls back |
| V15 | HIGH | `demo_auth_ai.py` | 57–60 | `TOKEN_SECRET` only validated at call time; missing secret leaks env state to client |
| V16 | HIGH | `demo_auth_ai.py` | 818 | OpenAI response accessed with direct `[]` indexing; `KeyError` on unexpected structure |
| V17 | MEDIUM | `demo_auth_ai.py` | 792–834 | OpenAI path has no retry logic; Anthropic path has 3-retry backoff |
| V18 | MEDIUM | `cutsheet_profiles.py` | 478–504 | Conflict resolution silently drops fallback column data (data loss) |
| V19 | HIGH | `build_sheet_processor.py` | 352–363, 527, 586 | Workbook not closed on exception; file descriptor leak |
| V20 | MEDIUM | `demo_web_app.py` | 332 | `context["summary"]` access without key check; `KeyError` on fallback paths |

## Section 10: Full Pipeline Audit — Column Coverage, SQL Routing, and Missing Logic

> Five parallel agents traced every column from raw cutsheet → canonicalization → Postgres ingest → SQL templates → LLM context, plus end-to-end routing correctness.

---

### 10A — Data That Is Silently Dropped Before Postgres

#### W1 — Six Breakout and Patch Panel Columns Canonicalized but Never Stored
**Severity: HIGH**

`cutsheet_profiles.py` maps these six columns for PROFILE_STANDARD_V1 (lines 246–252):

| Canon constant | Source column | Stored in Postgres? |
|---|---|---|
| `Canon.A_BREAKOUT_LOC` | `A-BREAKOUT LOC:CAB:RU` | NO |
| `Canon.A_BREAKOUT_PORT` | `A-BREAKOUT SLOT:PORT` | NO |
| `Canon.Z_BREAKOUT_LOC` | `Z-BREAKOUT LOC:CAB:RU` | NO |
| `Canon.Z_BREAKOUT_PORT` | `Z-BREAKOUT SLOT:PORT` | NO |
| `Canon.A_PATCH_PANEL` | `A-PATCH-PANEL LOC:CAB:RU:PORT` | NO |
| `Canon.Z_PATCH_PANEL` | `Z-PATCH-PANEL LOC:CAB:RU:PORT` | NO |

`atlas_data_loader.py` INSERT at lines 490–494 only includes the 15 core columns. The breakout and patch panel columns are canonicalized by the profile (renamed correctly), then silently discarded — they are never present in `_col_map` that drives the INSERT.

**Double consequence:** The legacy `Define_Optic_Count.py` uses `A-BREAKOUT LOC:CAB:RU` and `Z-BREAKOUT LOC:CAB:RU` to **deduplicate optics on breakout panels** (a breakout cable has one optic serving multiple sub-connections). The Postgres pipeline has no equivalent deduplication — it counts every sub-connection as a separate optic. **Cutsheets with breakout panels will show higher optic counts in the Postgres pipeline than the legacy counter.**

---

#### W2 — `Canon.HOST_LOCODE` Defined but Never Mapped or Stored
**Severity: MEDIUM**

`cutsheet_profiles.py` line 66 defines `Canon.HOST_LOCODE = "LOCODE"`. No profile in `PROFILE_REGISTRY` (PROFILE_STANDARD_V1, V2, or ALTERNATE) maps any source column to `Canon.HOST_LOCODE` in `host_columns`. `atlas_data_loader.py` never inserts a locode field into `host_inventory`. Geographic site codes for devices are never stored.

---

#### W3 — RoCE Not Supported: No Connector Columns in Schema
**Severity: MEDIUM**

`Define_Optic_Count.py` uses `A-CONNECTOR` and `Z-CONNECTOR` columns for RoCE optic counting and port deduplication via `check_if_roce_port_occupied()`. Neither column exists in `cutsheet_connections`. RoCE-format Excel files will either fail schema verification or ingest with incorrect optic counts. No SQL template handles RoCE-specific connection types.

---

### 10B — Critical SQL Bug: `data_hall_summary` Always Returns NULL

#### W4 — `data_hall_summary` Queries Unpopulated Column
**Severity: CRITICAL**

`atlas_query_router.py` data_hall_summary template (line ~323):
```sql
SELECT a_locode AS locode, COUNT(*) AS connections, COUNT(DISTINCT a_device) AS devices
FROM cutsheet_connections
WHERE site_id = %(site_id)s
GROUP BY a_locode
ORDER BY connections DESC
```

`a_locode` is stored in `cutsheet_connections` schema (column exists). However, **it is never populated during ingest**. `atlas_data_loader.py` maps `Canon.A_LOCODE` into `_col_map` (line 402), but in real production cutsheets (PROFILE_STANDARD_V1), the "A-SIDE LOCODE" column is typically empty or absent.

**Verified:** The `a_locode` column stores the raw LOCODE string from the spreadsheet (e.g., `"US-SLC-01"`). This is distinct from the physical location `a_loc_cab_ru` (e.g., `"dh202:041:042"`). For most sites, `a_locode` is blank in the source data.

**Result:** `data_hall_summary` queries against a column that is almost always `NULL`, returning an empty GROUP BY or single `NULL` group instead of actual data hall breakdowns.

**Correct approach:** Extract the data hall prefix from `a_loc_cab_ru` using string parsing:
```sql
SELECT split_part(a_loc_cab_ru, ':', 1) AS locode, COUNT(*) AS connections ...
```

---

### 10C — Routing Gaps: Queries That Return Wrong Results Silently

#### W5 — Cross-Site Queries Ignore Explicitly Named Sites
**Severity: HIGH**

When a user asks `"How many SN5610s in QCY and ORD?"`, `route_cross_site_intent()` detects `len(site_code_mentions) >= 2` and routes to `cross_site_models`. The SQL template (lines 650–668) filters only `WHERE cu.is_active = TRUE` with **no site code filtering** — it returns models for ALL active sites, not just QCY and ORD.

The extracted site codes (`["QCY", "ORD"]`) are never passed as parameters to `build_query_params()` for cross-site queries — the comment at lines 983–984 explicitly states `"cross_site queries don't filter by site_id"`. The result is that any question mentioning 2+ site codes triggers an all-sites query, not a scoped multi-site query. Users asking for a comparison between specific sites get data for all 12+ active sites.

---

#### W6 — Model + Location Queries Misclassify to `location_lookup`
**Severity: HIGH**

Question: `"How many SN5610s in rack dh202:041?"` extracts both `extracted_location = "dh202:041"` and `extracted_model = "SN5610"`. `route_location_intent()` runs at higher priority than `route_device_intent()`. The location signal wins and routes to `location_lookup`, returning per-device cable detail for that rack rather than a model count scoped to that rack.

The system has no `model_search` variant that accepts a location filter — `model_search` templates (lines 372–405) only filter by `site_id`, `upload_id`, `model_pattern`, and optionally `model_status_filters`. There is no `location_filter` parameter available to `model_search`.

A user wanting to know "how many SN5610s are in DH202" has no supported query path.

---

#### W7 — `data_hall_summary` Has No Parameter Extractor for Locode Filter
**Severity: MEDIUM**

`build_query_params()` has no case for `qtype == "data_hall_summary"` (lines 911–988). No locode or data-hall identifier is extracted from the question or passed as a SQL parameter. The template returns all data halls aggregated — it cannot be scoped to a specific data hall even if the user names one.

A user asking `"How many connections are in DH202?"` will get connections for every data hall at the site, not just DH202.

---

#### W8 — `role_lookup` Cannot Filter by Device Name
**Severity: MEDIUM**

`extract_device_name()` is called in `build_context()` and stored in `ctx.extracted_device` (line 927). When `qtype == "role_lookup"`, `build_query_params()` (lines 964–966) builds only `role_filter` and `side_filter`. The `device_pattern` parameter is never added. The SQL template for `role_lookup` has no device name filter.

A user asking `"What role does fdp-01-a1 play in this cutsheet?"` gets all FDP-type devices instead of the specific one named.

---

#### W9 — `upload_diff` Returns "No Differences" Instead of Error on Invalid IDs
**Severity: MEDIUM**

When `extract_upload_ids()` returns `(None, None)` or invalid IDs, the guard at line 1516 returns a helpful "upload IDs not found" message. BUT when IDs are provided but non-existent (e.g., user says `"compare upload 999 with upload 1000"` for a site with uploads 1–10), the SQL executes, both CTEs return zero rows, and `format_results_for_llm()` returns `"No differences found between the two uploads."` This is indistinguishable from a valid comparison with no changes. There is no validation that upload_id_a and upload_id_b actually exist before running the query.

---

### 10D — Missing Question Types

#### W10 — Cable Type Queries Unsupported
**Severity: MEDIUM**

`Canon.CABLE_TYPE = "CABLE TYPE"` exists in the profile. `cable_type` is stored in `cutsheet_connections`. No SQL template queries `cable_type`. No extractor for cable type keywords. No routing path for questions like `"What cable types are in use?"` or `"How many CAT6a cables?"`. These fall through to `general` and return only 3 aggregate counts with no cable type information.

---

#### W11 — Locode/Data Hall Filter Queries Have No Extractor
**Severity: MEDIUM**

The `data_hall_summary` question type exists and is classified, but (a) the underlying column it queries is wrong (W4) and (b) there is no extractor to pull a locode or data hall identifier from the question. A question like `"Show connections in locode US-SLC-01"` produces an unfiltered all-data-halls result. `extract_location()` extracts LOC:CAB:RU format locations but not bare locode strings.

---

#### W12 — Breakout Port and Patch Panel Questions Unsupported
**Severity: LOW**

`Canon.A_BREAKOUT_LOC`, `Canon.A_BREAKOUT_PORT`, `Canon.Z_BREAKOUT_LOC`, `Canon.Z_BREAKOUT_PORT`, `Canon.A_PATCH_PANEL`, `Canon.Z_PATCH_PANEL` are defined in profiles but never stored (W1). Consequently, no SQL template queries them, no extractor handles them, and no question type covers them. Questions about breakout panels or patch panel routing have no supported path.

---

#### W13 — Burndown Temporal Trend Queries Unsupported
**Severity: LOW**

`burndown_connections` stores historical link status (`link_status`, `current_neighbor`, `dct_notes`). `link_status` and `lldp_neighbor_mismatch` return current-state snapshots. There is no query type for tracking how link status changed across uploads (e.g., `"Which ports changed from LLDP Passed to Failed over the last 3 uploads?"`). `trend_status` and `trend_section` cover the cutsheet connections table only, not burndown data.

---

### 10E — Missing Indexes Causing Silent Performance Degradation

#### W14 — Missing Indexes on Queried or JOINed Columns
**Severity: MEDIUM**

| Table | Column | Situation |
|---|---|---|
| `cutsheet_connections` | `cable_type` | Stored; no SQL query yet, but no index if queries are added |
| `cutsheet_connections` | `a_locode`, `z_locode` | Stored; used in `data_hall_summary` (broken per W4); no index |
| `host_inventory` | `role` | Used in `backfill_device_roles()` JOIN (line 717); no index — this JOIN does a full table scan per upload |
| `host_inventory` | `data_hall`, `status`, `rack`, `row_type` | Stored but no indexes and not queried |
| `burndown_connections` | `status`, `a_port`, `z_port`, `current_neighbor` | Stored; no indexes |

The `host_inventory.role` missing index is the highest-impact gap — `backfill_device_roles()` runs on every upload and performs a `JOIN ... ON hi.hostname = cc.a_device AND hi.role IS NOT NULL` without an index on `hi.role`.

---

### 10F — LLM Context Gaps: Data That Exists But Never Reaches the Model

#### W15 — Breakout and LOCODE Data Never Reaches LLM via Any Path
**Severity: HIGH**

In the in-memory path, `normalize_cutsheet()` collects breakout fields (lines 283–322: `a_breakout`, `a_breakout_loc`, `a_breakout_slot`, `a_breakout_new_optic`, etc.) into every connection record. `build_llm_context()` (lines 666–754) never includes these fields in the output dict. LOCODEs are stored per device (line 241: `device['locode']`) but never aggregated into the context dict.

In the Postgres path, breakout columns are not stored (W1) so there is nothing to query.

**Result:** The LLM has no awareness that breakout cables exist, cannot reason about breakout port assignments, and cannot identify multi-fiber-to-one-optic relationships.

---

#### W16 — `upload_diff` Roles Not Formatted Into Context
**Severity: MEDIUM**

The `upload_diff` SQL template (lines 551–637) includes `a_role` and `z_role` in the selected columns. `format_results_for_llm()` for `upload_diff` (lines 1327–1352) iterates over the result rows and outputs `section, a_device, a_port, z_device, z_port, a_optic, z_optic, status` per row. The `a_role` and `z_role` fields are present in every result row but are never extracted or displayed in the formatted output. The LLM cannot see role information in upload diff results.

---

#### W17 — Roles Completely Absent from In-Memory LLM Context
**Severity: MEDIUM**

The in-memory path (`cutsheet_normalizer.build_llm_context()`) has no concept of device roles. The Postgres path provides roles only via the `role_lookup` question type. The composite `build_postgres_context_for_general()` does not query `host_inventory` and includes no role data. The LLM in the general context path cannot answer "What is the role of device X?" unless a specific `role_lookup` query is classified and executed.

---

#### W18 — Optional Tables Produce Silent Empty Results
**Severity: MEDIUM**

Two SQL question types depend on optionally-populated tables:

| Table | Populated when | Question types that fail silently |
|---|---|---|
| `host_inventory` | SITE-HOSTS tab exists AND parses successfully | `role_lookup`, `model_search` (UNION branch), `location_lookup` (UNION branch) |
| `burndown_connections` | BURNDOWN tab exists AND parses successfully | `link_status`, `lldp_neighbor_mismatch` |

In both cases, the load failure is non-fatal (`except Exception: log.warning(...)` at lines 943–944 and 965–966). The ingest returns `{"ok": True, "hosts_loaded": 0}`. Subsequent queries return zero rows with no indication that the supporting table is empty due to a load failure vs. genuinely having no data.

A user asking "What is the role of the FDP devices?" after a failed SITE-HOSTS load will get the `role_lookup` no-results message: `"No devices found with role 'FDP'. Possible reasons: (1) No SITE-HOSTS tab was uploaded..."` — which is helpful, but the root cause (parse failure vs. absent tab) is not surfaced.

---

### 10G — Consolidated New Findings Summary

| ID | Severity | Category | Description |
|----|----------|----------|-------------|
| W1 | HIGH | Data loss | 6 breakout/patch panel columns canonicalized but never stored; legacy dedup breaks |
| W2 | MEDIUM | Data loss | `HOST_LOCODE` defined but never mapped or stored |
| W3 | MEDIUM | Data loss | RoCE connector columns missing from schema; RoCE ingest unsupported |
| W4 | CRITICAL | SQL bug | `data_hall_summary` queries `a_locode` which is always NULL; use `split_part(a_loc_cab_ru,':',1)` |
| W5 | HIGH | Routing | Cross-site queries ignore explicitly named sites; return all-sites data |
| W6 | HIGH | Routing | Model+location queries misclassify to `location_lookup`; no scoped model_search path |
| W7 | MEDIUM | Routing | `data_hall_summary` has no locode extractor; always returns unfiltered result |
| W8 | MEDIUM | Routing | `role_lookup` cannot filter by device name; device pattern never built |
| W9 | MEDIUM | Routing | `upload_diff` with non-existent IDs silently returns "no differences" |
| W10 | MEDIUM | Missing type | Cable type queries unsupported; `cable_type` stored but never queried |
| W11 | MEDIUM | Missing type | Locode filter queries have no extractor; `data_hall_summary` always unfiltered |
| W12 | LOW | Missing type | Breakout port and patch panel questions have no supported query path |
| W13 | LOW | Missing type | Burndown temporal trend queries unsupported |
| W14 | MEDIUM | Performance | Missing indexes on `host_inventory.role` (backfill JOIN), `cable_type`, burndown columns |
| W15 | HIGH | Context gap | Breakout and LOCODE data never reaches LLM via any path |
| W16 | MEDIUM | Context gap | `upload_diff` `a_role`/`z_role` returned by SQL but not formatted for LLM |
| W17 | MEDIUM | Context gap | Device roles absent from in-memory LLM context |
| W18 | MEDIUM | Context gap | Optional tables (`host_inventory`, `burndown_connections`) fail silently; LLM gets empty results |

## Cutsheet Remediation Priority

```
Immediate:
  N3  ← dual status dicts already diverged; new site will break one path silently

Short-term (performance):
  N1  ← iterrows() bottleneck — will block at Ellendale scale
  N4  ← parse Excel once; eliminate 3-4x redundant I/O per upload
  N2  ← _CONNECTION_CACHE unbounded growth; will OOM Gunicorn workers

Next cycle:
  N5  ← hardcoded Ellendale section headers; won't work for new sites
  N8  ← iterrows() in load_prebuilt_sheets() (3 loops)
  N7  ← fragile hostname heuristic in lookup_device_connections()
  N6  ← log/improve tab selection fallback

Cleanup:
  N9  ← reduce canonicalize() pass count
  N10 ← fix pandas 2.x CoW-unsafe Series mutation
  N11 ← hoist _brk out of _build_connection() inner scope
  N12 ← add STATUS_MAP key-collision test
```
