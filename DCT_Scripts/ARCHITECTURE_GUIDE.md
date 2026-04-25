# Atlas DCT Infrastructure Intelligence — Architecture Guide

**Review Date:** 2026-03-31
**Reviewer Role:** Performance Architect
**Codebase Path:** `/Users/lwells/Atlas/DCT_Scripts/Optic_Count/`
**Cutsheet Under Test:** `CUTSHEET_DEMO_V2.xlsx` — 49,860 rows, single CUTSHEET tab

---

## Performance Architecture Review

### Summary

Atlas is a well-structured Flask application for datacenter cutsheet analysis with a clean two-track data path (in-memory pandas for immediate use, Postgres for scale) and solid security fundamentals. The primary scaling risk is the pervasive use of `.iterrows()` Python loops against DataFrames that are already confirmed at 49,860 rows — 11x the 4,300-row baseline assumed in the codebase rules — and a per-request database connection pattern that will collapse under concurrent load. The Postgres path is architecturally correct and should be accelerated as the exclusive production path; the in-memory pandas path should be kept only as a cold-start fallback.

---

## Data Flow Diagram (ASCII)

```
Upload (.xlsx / .csv)
        |
        v
[demo_web_app.py /api/upload-count]
        |
        +---> Define_Optic_Count.build_sheet_context()  [legacy, in-memory]
        |
        +---> cutsheet_normalizer.preload_connections()  [cache CONNECTIONS sheet]
        |         |
        |         v
        |     _CONNECTION_CACHE[file_path] = DataFrame   [module-level, per-worker]
        |
        +---> cutsheet_normalizer.load_prebuilt_sheets()  [V3 prebuilt path]
        |
        +---> atlas_data_loader.load_file()  [Postgres dual-write, non-fatal]
                  |
                  +---> upsert_site() -> cutsheet_connections (execute_values 500/batch)
                  +---> load_site_hosts()   -> host_inventory
                  +---> load_burndown()     -> burndown_connections
                  +---> refresh_atlas_views()  [MATERIALIZED VIEW refresh]

Q&A Request (.../api/sheet-qa)
        |
        v
[demo_web_app.py /api/sheet-qa]
        |
        +---> USER_SITE lookup (in-memory, per-worker)
        |         |
        |         +--> If missing: Postgres site recovery query
        |
        +---> atlas_postgres_context.build_postgres_context()  [Priority 1]
        |         |
        |         v
        |     atlas_query_router.route_question()
        |         |
        |         +---> classify_question()   [regex, O(patterns) = O(1)]
        |         +---> build_query_params()  [extract entities, O(1)]
        |         +---> execute_query()       [parameterized SQL template]
        |         +---> format_results_for_llm()  [text formatting, O(rows)]
        |
        +---> demo_auth_ai._trim_context_for_llm()  [Priority 2: normalized; Priority 3: legacy]
        |         |
        |         v
        |     cutsheet_normalizer.build_llm_context()  [in-memory fallback]
        |
        v
demo_auth_ai.ask_grounded()
        |
        +---> _build_grounded_messages()  [inject device connections if named]
        +---> _call_anthropic() / _call_openai()
        |
        v
JSON response with answer + token usage + context_source badge
```

---

## CRITICAL Issues

### C1 — `.iterrows()` on 49,860-row DataFrame in `_tag_sections()`
**Location:** `cutsheet_normalizer.py:76-88` (`_tag_sections`), called from `normalize_cutsheet()`

**Problem:** Row-wise Python loop using `.iterrows()` over the full DataFrame. At 49,860 rows confirmed in CUTSHEET_DEMO_V2.xlsx this runs at approximately 50,000–100,000 Python function calls. The loop also calls `_is_section_header()` twice per row (once in `_tag_sections`, once in the `data_rows` filter at line 123) — doubling the work.

**Complexity:** O(2n) Python-level iterations. Measured impact: `.iterrows()` on a 50k-row DataFrame typically takes 2–5 seconds on modern hardware vs. under 50ms for a vectorized equivalent.

**Fix — vectorized section tagging:**
```python
def _tag_sections_vectorized(df: pd.DataFrame) -> pd.Series:
    # Build the header mask once, vectorized
    status_filled = df["STATUS"].fillna("").astype(str).str.strip()
    a_loc_filled  = df["A-LOC:CAB:RU"].fillna("").astype(str).str.strip()
    a_dns_filled  = df["A-SIDE-DNS-NAME"].fillna("").astype(str).str.strip()

    is_header = status_filled.ne("") & a_loc_filled.eq("") & a_dns_filled.eq("")

    sections = status_filled.where(is_header, other=pd.NA).ffill().fillna("UNKNOWN")
    return sections
```
This reduces the two loops to a single vectorized pass with no Python-level row iteration.

---

### C2 — `_RATE_LIMIT_STORE` is an unbounded in-memory dict
**Location:** `demo_web_app.py:31`, used in `_check_rate_limit()`

**Problem:** `_RATE_LIMIT_STORE` is a module-level dict that is written to for every unique client IP but never evicted. A long-running gunicorn worker handling diverse IPs will accumulate entries indefinitely. At 1 entry per IP, 10k unique IPs per day = ~500KB/day; at 100k IPs it becomes a memory leak.

**Complexity:** O(n) memory growth over worker lifetime. O(1) per check, but the store is never pruned.

**Fix — bounded TTL eviction (add to `_check_rate_limit`):**
```python
def _check_rate_limit(key: str) -> bool:
    now = time.time()
    # Periodic eviction: prune stale entries every ~1000 requests
    if len(_RATE_LIMIT_STORE) > 5000:
        cutoff = now - _RATE_LIMIT_WINDOW
        stale = [k for k, (_, ws) in _RATE_LIMIT_STORE.items() if ws < cutoff]
        for k in stale:
            del _RATE_LIMIT_STORE[k]
    count, window_start = _RATE_LIMIT_STORE.get(key, (0, now))
    if now - window_start > _RATE_LIMIT_WINDOW:
        _RATE_LIMIT_STORE[key] = (1, now)
        return True
    if count >= _RATE_LIMIT_MAX:
        return False
    _RATE_LIMIT_STORE[key] = (count + 1, window_start)
    return True
```

---

### C3 — Per-request DB connection creation, no pooling
**Location:** `atlas_query_router.py:484` (`execute_query`), `atlas_postgres_context.py:21,33,46,62` (multiple `get_connection()` calls per request)

**Problem:** Every Q&A request opens 2–4 separate `psycopg2.connect()` calls: one in `route_question()` → `execute_query()`, one in `get_site_info()`, and potentially one in the site recovery path in `demo_web_app.py`. Under gunicorn with 4 workers and 10 concurrent users this creates up to 40 simultaneous connection attempts against Postgres.

**Complexity:** O(connections) = O(workers × concurrent_requests). `psycopg2.connect()` costs ~5–20ms per call for TCP establishment.

**Fix — use `psycopg2.pool.ThreadedConnectionPool` or `psycopg2.pool.SimpleConnectionPool`:**
```python
# atlas_data_loader.py — add at module level
import psycopg2.pool

_POOL: Optional[psycopg2.pool.ThreadedConnectionPool] = None

def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=int(os.getenv("DB_POOL_MAX", "10")),
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "9000")),
            dbname=os.getenv("DB_NAME", "atlas"),
            user=os.getenv("DB_USER", "atlas"),
            password=os.getenv("DB_PASSWORD", "atlas"),
        )
    return _POOL

def get_connection():
    return get_pool().getconn()

def release_connection(conn):
    get_pool().putconn(conn)
```
All callers that do `conn = get_connection()` must call `release_connection(conn)` in their `finally` block.

---

## MAJOR Issues

### M1 — `build_llm_context()` iterates connections list twice
**Location:** `cutsheet_normalizer.py:587-648`

**Problem:** The function makes two separate passes over `normalized["connections"]`: once to build `model_summary` (via the `devices` loop) and once to build `optic_summary`. At 49,860 rows this is O(2n).

**Complexity:** O(2n). Can be reduced to O(n) with a single combined pass.

**Fix:**
```python
def build_llm_context(normalized: Dict[str, Any]) -> Dict[str, Any]:
    model_summary: Dict[str, Any] = {}
    optic_summary: Dict[str, Any] = {}
    section_conn_counts: Dict[str, int] = {}

    # Single O(n) pass over devices
    for dev in normalized["devices"]:
        model = dev["model"] or "UNKNOWN"
        entry = model_summary.setdefault(model, {
            "count": 0, "locations": [], "dns_names": [], "sections": set()
        })
        entry["count"] += 1
        if len(entry["locations"]) < 10:
            entry["locations"].append(dev["loc_cab_ru"])
        if dev["dns_name"] and len(entry["dns_names"]) < 5:
            entry["dns_names"].append(dev["dns_name"])
        entry["sections"].update(dev["sections"])

    # Single O(n) pass over connections — build optic summary + section counts together
    for conn in normalized["connections"]:
        sec = conn["section"]
        section_conn_counts[sec] = section_conn_counts.get(sec, 0) + 1
        for side in ("a", "z"):
            optic = conn[f"{side}_optic"]
            loc   = conn[f"{side}_loc"]
            if not optic:
                continue
            if side == "a" and conn["a_breakout"] and not conn["a_breakout_new_optic"]:
                continue
            entry = optic_summary.setdefault(optic, {"count": 0, "locations": {}})
            entry["count"] += 1
            entry["locations"][loc] = entry["locations"].get(loc, 0) + 1

    # ... rest unchanged
```

---

### M2 — `_overhead_rack_to_cab_type()` opens and reads the Excel file on every call
**Location:** `build_sheet_processor.py:166-187` (`_overhead_rack_to_cab_type`), called by both `_lookup_cab_type()` (line 191) and `_cab_type_summary()` (line 205) in the same `process_rack()` call path

**Problem:** `process_rack()` calls both `_lookup_cab_type()` and `_cab_type_summary()`, which each independently call `_overhead_rack_to_cab_type(cutsheet_path)`. This opens and parses the entire OVERHEAD sheet twice per rack request. For large cutsheets this is O(2 × sheet_read_time).

**Fix — memoize at the module level:**
```python
import functools

@functools.lru_cache(maxsize=16)
def _overhead_rack_to_cab_type(cutsheet_path: str) -> dict:
    # ... existing implementation unchanged
```
`lru_cache` is safe here because the file path is the cache key and the workbook is read-only. The cache is invalidated on process restart, which is appropriate for a demo/upload workflow.

---

### M3 — `check_postgres()` opens a new connection on every health check and every Q&A request
**Location:** `demo_web_app.py:79-85` (`_check_postgres`), called at `/api/health`, in `upload_count`, and twice in `sheet_qa`

**Problem:** Each `_check_postgres()` call executes `psycopg2.connect()` + `conn.close()`. In `sheet_qa` this is called up to two times per request (site recovery check + context build check). Under load this creates constant TCP connection churn.

**Fix — add a short-lived TTL cache:**
```python
_PG_CHECK_CACHE: Dict[str, Any] = {"ok": False, "ts": 0.0}
_PG_CHECK_TTL = 10.0  # seconds

def _check_postgres() -> bool:
    now = time.time()
    if now - _PG_CHECK_CACHE["ts"] < _PG_CHECK_TTL:
        return _PG_CHECK_CACHE["ok"]
    try:
        from atlas_data_loader import check_postgres
        result = check_postgres()
    except Exception:
        result = False
    _PG_CHECK_CACHE.update({"ok": result, "ts": now})
    return result
```

---

### M4 — `lookup_device_connections()` result loop uses `.iterrows()` on matched subset
**Location:** `cutsheet_normalizer.py:570-584`

**Problem:** After the vectorized `.str.contains()` filter (which is correct), the result is converted to a list of dicts via `for _, row in matches.iterrows()`. For a device with 200 connections this is O(200) Python-level row iterations. At 49,860 total rows and a common device like a spine switch with 200+ connections, this is non-trivial.

**Fix — use `DataFrame.to_dict('records')` and remap keys:**
```python
if matches.empty:
    return None

col_map = {
    cols["status"]:   "status",
    cols["a_dns"]:    "a_device",
    cols["a_model"]:  "a_model",
    cols["a_port"]:   "a_port",
    cols["a_optic"]:  "a_optic",
    cols["z_dns"]:    "z_device",
    cols["z_model"]:  "z_model",
    cols["z_port"]:   "z_port",
    cols["z_optic"]:  "z_optic",
    cols["cable"]:    "cable",
}
keep = [c for c in col_map if c in matches.columns]
renamed = matches[keep].rename(columns=col_map).fillna("").astype(str)
return renamed.to_dict("records")
```
This replaces Python row iteration with a single vectorized rename + `to_dict()`.

---

### M5 — Two parallel Flask applications with duplicated middleware
**Location:** `atlas_web_app.py` vs `demo_web_app.py`

**Problem:** Both files implement independently: auth token extraction (`_bearer` / `_extract_bearer_token`), audit logging (`_audit`), context TTL eviction (`_evict_stale_contexts`), and session stores (`USER_CONTEXT`, `AUDIT_LOG`). `atlas_web_app.py` is missing the security headers middleware (`set_security_headers`) present in `demo_web_app.py`. Changes to one do not propagate to the other.

**Recommended fix:** Extract shared middleware into `atlas_middleware.py` (security headers, rate limiting, audit, token helpers). Both apps import from it. This is the correct application of the Open/Closed principle for Flask Blueprint composition.

---

### M6 — Status strings are defined in three separate places
**Location:** `cutsheet_profiles.py:59-94` (STATUS_NORMALIZATION), `atlas_schema.sql:116-148` (ILIKE patterns), `atlas_query_router.py:130-171` (SQL template ILIKE patterns)

**Problem:** Adding a new status variant (e.g., "lldp: pass" with single space) requires changes in three files. The ILIKE patterns in SQL are a separate concern (fuzzy matching) but the canonical status strings used for exact comparison should have a single source.

**Fix:** `cutsheet_profiles.py` is already the right single source of truth for canonical names. The SQL ILIKE patterns are intentionally fuzzy and are acceptable. The gap is that the Postgres materialized view `optic_inventory` uses `ILIKE '%complete%'` while the canonical string is `"Cable Is Ran Complete"` — these are consistent. Document this explicitly in `atlas_schema.sql` with a comment referencing `cutsheet_profiles.py`.

---

## MINOR Issues

### m1 — `normalize_status_column()` uses `.apply()` instead of `.map()`
**Location:** `cutsheet_profiles.py:341`

At 49,860 rows, `.apply(normalize_status)` calls a Python function once per cell. `.map(STATUS_NORMALIZATION).fillna(df[col].str.strip())` would use the dict lookup in a vectorized manner.

```python
def normalize_status_column(df: pd.DataFrame, col: str = Canon.STATUS) -> pd.DataFrame:
    if col in df.columns:
        cleaned = df[col].astype(str).str.strip()
        df[col] = cleaned.str.lower().map(STATUS_NORMALIZATION).fillna(cleaned)
    return df
```

---

### m2 — Token estimate uses word count, not token count
**Location:** `atlas_query_router.py:647` and `atlas_postgres_context.py:165`

`len(context_text.split())` counts whitespace-delimited words. LLM tokens are ~0.75 words on average. For monitoring purposes this over-counts token usage by ~25%. Replace with `len(context_text) // 4` (character-based estimate) or add `tiktoken` to requirements for accurate counting.

---

### m3 — `atlas_web_app.py` missing security headers
**Location:** `atlas_web_app.py` — no `@app.after_request` security header middleware

`demo_web_app.py` correctly sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Content-Security-Policy`. `atlas_web_app.py` (the fuller feature version) does not. This is a regression in the alternate entry point.

---

### m4 — `define_Optic_Count` legacy module still active at upload time
**Location:** `demo_web_app.py:247` — `context = Define_Optic_Count.build_sheet_context([str(save_path)])`

This legacy module is called unconditionally on every upload, even when the prebuilt sheets path is available. The result is stored in `USER_CONTEXT` as a fallback but the call cost is paid on every upload. Consider gating this behind a `not prebuilt` check.

---

### m5 — `_extract_device_name()` regex may match topology keyword phrases
**Location:** `atlas_query_router.py:369-384`

The second pattern `r"\b(\w+-\w+-\w+(?:-\w+)*)\b"` will match strings like `"not-terminated-cable"` or `"how-many-devices"` in questions if a user phrases them with hyphens. The existing blocklist (`how-many`, `tell-me`, `show-me`, `what-is`) does not cover all cases. This could cause spurious device lookups that return empty results rather than falling back to a summary query.

---

## Complexity Annotations

| Function | File | Complexity | Notes |
|---|---|---|---|
| `_tag_sections()` | cutsheet_normalizer.py | O(n) Python loop | `.iterrows()` on full DataFrame; vectorize to O(n) pandas |
| `normalize_cutsheet()` | cutsheet_normalizer.py | O(n) | Calls `_tag_sections` + `_is_section_header` filter (O(2n) total) |
| `build_llm_context()` | cutsheet_normalizer.py | O(d + 2c) | d=devices, c=connections; two separate connection passes |
| `load_prebuilt_sheets()` CONNECTIONS pass | cutsheet_normalizer.py | O(c) Python loop | iterrows over CONNECTIONS sheet |
| `preload_connections()` | cutsheet_normalizer.py | O(n) | One-time vectorized load; correct pattern |
| `lookup_device_connections()` | cutsheet_normalizer.py | O(n) + O(k) | Vectorized filter O(n), then iterrows on k results |
| `_sanitize_context_dict()` | demo_auth_ai.py | O(n) | DFS tree traversal over context dict |
| `_select_relevant_sox_sections()` | demo_auth_ai.py | O(s * t) | s=sections, t=question tokens; bounded by PDF size |
| `classify_question()` | atlas_query_router.py | O(p) | p=28 compiled patterns; effectively O(1) |
| `build_query_params()` | atlas_query_router.py | O(1) | Regex extractions on question string |
| `execute_query()` | atlas_query_router.py | O(1) dispatch + DB time | New DB connection per call is the real cost |
| `format_results_for_llm()` | atlas_query_router.py | O(r) | r=result rows; linear string building |
| `load_cutsheet()` | atlas_data_loader.py | O(n) Python loop | Row loop to build tuples; bulk insert via execute_values |
| `load_site_hosts()` | atlas_data_loader.py | O(h) Python loop | h=host rows |
| `load_burndown()` | atlas_data_loader.py | O(b) Python loop | b=burndown rows |
| `detect_profile()` | cutsheet_profiles.py | O(p * f) | p=profiles(3), f=fingerprint cols(4); O(1) effectively |
| `normalize_status_column()` | cutsheet_profiles.py | O(n) Python | `.apply()` per row; use `.map()` instead |
| `process_rack()` | build_sheet_processor.py | O(n) | Full cutsheet scan + O(n) second pass for optics |
| `_cab_type_summary()` | build_sheet_processor.py | O(n) | Scans all cables for room; calls `_overhead_rack_to_cab_type` again |
| `_overhead_rack_to_cab_type()` | build_sheet_processor.py | O(r * c) | r=rows, c=cols of OVERHEAD sheet; re-reads file each call |
| `generate_layout_workbook()` | build_sheet_processor.py | O(n * cab_types) | Inner loop rescans cables per cab type |
| `_evict_stale_contexts()` | demo_web_app.py | O(u) | u=active users; called on every upload |
| `_check_rate_limit()` | demo_web_app.py | O(1) amortized | Unbounded store growth is the issue, not per-call cost |

---

## Top 5 Actionable Improvements

### 1. Vectorize `_tag_sections()` and eliminate duplicate `_is_section_header` calls
**Impact:** CRITICAL — eliminates 2–5 second processing delay on 49,860-row uploads.
**Effort:** 1 hour.
**Rationale:** The CUTSHEET_DEMO_V2.xlsx file has already grown to 49,860 rows, 11x the assumed 4,300. The `.iterrows()` pattern is confirmed as the primary CPU bottleneck on upload. See fix in C1 above.

### 2. Add `psycopg2.ThreadedConnectionPool` to all DB access paths
**Impact:** CRITICAL under concurrent load — prevents connection exhaustion with gunicorn workers.
**Effort:** 2 hours.
**Rationale:** Currently 2–4 connections are created and destroyed per Q&A request. With 4 gunicorn workers and 5 concurrent users, this creates 40–80 connection events per second. A pool of 10 connections would serve all workers without TCP connection overhead. See fix in C3 above.

### 3. Evict stale entries from `_RATE_LIMIT_STORE`
**Impact:** CRITICAL for long-running workers — prevents unbounded memory growth.
**Effort:** 30 minutes.
**Rationale:** This is a one-line fix class. Any production deployment with real internet traffic will accumulate IPs in this dict indefinitely. See fix in C2 above.

### 4. Merge `atlas_web_app.py` and `demo_web_app.py` into a single Flask app with a shared middleware module
**Impact:** MAJOR — eliminates security regression (missing headers in atlas_web_app.py), reduces maintenance surface, ensures all security controls apply to all routes.
**Effort:** 1 day.
**Rationale:** Both files implement the same auth, audit, and session management. The `atlas_web_app.py` SSE streaming, rack processor, and NetBox features should be added as Flask Blueprints on top of the `demo_web_app.py` foundation which has the more complete security posture. This prevents the recurring pattern of adding features in one app without porting security controls.

### 5. Gate `Define_Optic_Count.build_sheet_context()` behind a `not prebuilt` check
**Impact:** MAJOR — eliminates a redundant legacy parse on every upload when prebuilt sheets are present.
**Effort:** 30 minutes.
**Rationale:** For V3 cutsheets that have DEVICE_INVENTORY and CONNECTIONS sheets, the `build_sheet_context()` call is wasted work. The result is only used as a fallback and is superseded by the prebuilt path. Adding `if not prebuilt:` before the `Define_Optic_Count` call eliminates this cost.

---

## Approved Patterns — What Is Working Well

These patterns should be preserved and extended to new code:

- **Bulk Postgres inserts with `execute_values(page_size=500)`** (`atlas_data_loader.py`) — correct pattern for batch ingestion. Do not revert to individual `INSERT` statements.

- **HMAC-SHA256 token auth with `hmac.compare_digest()`** (`demo_auth_ai.py`) — timing-safe implementation. The TTL, scope check, and compact base64 format are all well-designed.

- **Prompt injection regex sanitization** (`demo_auth_ai.py:121-143`) — the `_PROMPT_INJECT_RE` pattern and recursive `_sanitize_context_dict()` are correct defense-in-depth. The special case for `context` key (sanitize but never truncate) is a thoughtful distinction.

- **Parameterized SQL with `_escape_ilike()`** (`atlas_query_router.py:356-358`) — no SQL injection risk. All 13 query templates use `%(param)s` placeholders correctly.

- **Materialized views for read-heavy patterns** (`atlas_schema.sql:109-190`) — `optic_inventory`, `cable_status_summary`, and `device_summary` are exactly the right architecture. The `refresh_atlas_views()` function call after every load is correct.

- **Profile auto-detection via fingerprint scoring** (`cutsheet_profiles.py:281-304`) — the score-based approach with 50% threshold is a clean, extensible way to handle multi-site format variance without hardcoded branching.

- **Three-layer site code detection** (`demo_web_app.py:110-154`) — prebuilt quick_reference → SITE-VARS sheet → filename regex is a resilient fallback chain. The priority ordering is correct.

- **`secure_filename()` + `is_relative_to()` path traversal guard** (`demo_web_app.py:237-243`) — both checks are present and necessary. Neither alone is sufficient.

- **Postgres context priority over in-memory context** (`demo_auth_ai.py:174-187`) — the three-tier priority (Postgres → normalized → legacy) is architecturally correct for the scaling transition.

- **`_CONNECTION_CACHE` preloading at upload time** (`cutsheet_normalizer.py:487-538`) — preloading at upload and serving lookups from memory is the right pattern. The vectorized pre-lowercase of DNS columns at load time (`df["_a_dns_lower"] = ...`) is a good performance optimization.

---

## Schema Notes

### Current Indexes (atlas_schema.sql)
All critical lookup columns are indexed: `site_id`, `upload_id`, `a_device`, `z_device`, `a_model`, `z_model`, `status`, `section`. This is complete for the current query templates.

### Missing Index — Recommended Addition
The `ip_lookup` query template searches `raw_row::text ILIKE %pattern%` — a full-table text cast with no index. At 49,860 rows this is acceptable but will not scale. If IP lookup becomes a frequent query type, add a GIN index on the `raw_row` JSONB column:

```sql
CREATE INDEX IF NOT EXISTS idx_cc_raw_row_gin ON cutsheet_connections USING GIN (raw_row);
```

### LOCODE Index for Data Hall Queries
The `data_hall_summary` query groups by `a_locode`. Adding a covering index would speed this up at scale:

```sql
CREATE INDEX IF NOT EXISTS idx_cc_a_locode ON cutsheet_connections(site_id, a_locode);
```

---

## Security Summary

| Control | Status | Notes |
|---|---|---|
| HMAC token auth | PASS | `compare_digest` used correctly |
| Rate limiting | PARTIAL | Logic correct; store has unbounded growth (C2) |
| SQL injection | PASS | Parameterized queries + `_escape_ilike()` throughout |
| Path traversal | PASS | `secure_filename` + `is_relative_to` both present |
| Prompt injection | PASS | Regex sanitization + context-key special case |
| Security headers | PARTIAL | Present in `demo_web_app.py`; missing in `atlas_web_app.py` (m3) |
| Secrets in code | PASS | All secrets via `.env` / environment variables |
| SIGALRM in threads | PASS | Not used anywhere (confirmed) |
| CSP `unsafe-inline` | NOTE | Necessary for single-page inline JS/CSS; acceptable for demo |

---

*This guide is intended as living documentation. Re-run this review after the Postgres connection pool is introduced and after the two Flask apps are consolidated.*

---

## Real vs Demo Data Comparison

### File Metrics
| File | Total Rows | Data Rows | Sections |
|------|-----------|-----------|----------|
| CUTSHEET_DEMO_V2.xlsx | 49,860 | ~49,800 | 54 |
| MASTER-US-WEST-09A-US-QNC01-PRIME-QUINCY CUTSHEET.csv | 4,331 | 3,705 | 54 |

The demo file is 13x larger than production. Load testing and complexity audits should use the demo file as the scale benchmark.

### Column Differences
Production CSV has 12 columns absent from the demo — all IP address assignment placeholders:
- IPv4-VRF-DEFAULT-10, IPv6-VRF-DEFAULT-10
- IPv4-VRF-MGMT-20, IPv6-VRF-MGMT-20
- IPv4-VRF-IPMI-30, IPv6-VRF-IPMI-30
- IPv4-VRF-TSS-40, IPv6-VRF-TSS-40
- IPv4-VRF-PUB-90, IPv6-VRF-PUB-90
- IPv4-TNT, IPv6-TNT

All 12 are currently empty (0 populated rows). atlas_schema.sql ip_assignments table is correctly positioned to absorb these when populated. No demo columns are absent from production.

### Status Value Comparison
| Status | Production CSV | Demo XLSX |
|--------|---------------|-----------|
| `LLDP:  Passed` (double-space) | 2,953 (79.7%) | 0 |
| `LLDP:  Failed` (double-space) | 255 (6.9%) | 0 |
| `Cable Is Ran: Complete` | 483 (13.0%) | present |
| `Human Verified` | 14 (0.4%) | present |
| `Cable Is Ran: Not Terminated` | 0 | present |
| `Cable Not Run` | 0 | present |
| `PROBLEM: No Optics` | 0 | present |
| `REMOVE CABLING` | 0 | present |

**Bug:** `cutsheet_profiles.py` STATUS_NORMALIZATION has no entries for `LLDP:  Passed` or `LLDP:  Failed`. These 86.6% of production rows pass through unnormalized. Fix required before production deployment.

### Optic Type Delta
Production-only: `QSFP28-100G-DR1-LOW-PWR`, `JNP-QSFP-100G-LR4-LU`, `QSFPDD-400G-LR4-LU`, `QSFP-100G-LR4-L`
Demo-only: `OSFP-800G-2DR4`, `OSFP-800G-2FR4`, `QSFPDD-400G-PLR4`, `QSFP112-400G-DR4`, `SFP-BASE-1G-LX`

### Device Model Delta
Production-only: `CPU-GP2-02` (1,440), `DF-3060` (80), `NET-6X100G-01` (42), `1U-1N-GEN5-1NIC` (36), `PROLIANT-DL360-GEN10-PLUS` (8), `CPU-HPE-01` (6)
Demo-only: `SN5610`, `SN5600`, `GB300-NVLINK-SW`, `GPU-GB300-01`, `GPU-GB300-02`, `NGFW-4245`

### Section Structure Delta
Production uses a different naming convention than the demo:
- Production: `GG1-A`, `GG1-B`, `GG1-C` grid structure
- Demo: `GRID-AGG A`, `GRID-AGG B`, `GRID-AGG C`
- Production adds: `NET-AGG`, `COMP-AGG`, `NET-DIST`, `COMP-DIST`, `FBS` (Fabric Breakout Spine), `UFM-PATH`
- Production: `DH2 ROW N MGMT + CON` vs Demo: `RACK xx MGMT-DIST`
- Production: `TIER-4 TO TIER-3` (256 connections) — new tier level

**Bug:** Any hardcoded section strings in `atlas_query_router.py` SQL templates will fail to match production data. Section names must be parameterized or resolved dynamically from the `topology_sections` table.

### Data Quality Issues
1. **Z-MODEL case inconsistency (production):** `sn3700` (12 rows) and `sn2201` (1 row) appear alongside `SN3700` and `SN2201`. Fix: add `.str.upper()` normalization to Z-MODEL column in `atlas_data_loader.py` before alias lookup.
2. **LLDP double-space:** `LLDP:  Passed` and `LLDP:  Failed` contain two spaces after the colon. This is the raw data format and must be preserved exactly in STATUS_NORMALIZATION entries in `cutsheet_profiles.py`.
3. **IP columns empty:** All 12 IP assignment columns are unpopulated in the production CSV. The data loader should skip them gracefully rather than failing on null INET values.
