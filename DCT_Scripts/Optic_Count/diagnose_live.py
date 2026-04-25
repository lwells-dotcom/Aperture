#!/usr/bin/env python3
"""
diagnose_live.py - Run inside a live Atlas pod to trace the full cutsheet routing chain.

Usage (from your local machine):
    kubectl exec -it deploy/atlas-atlas-web -- python3 diagnose_live.py

Or run the schema check against Postgres directly:
    kubectl exec -it statefulset/atlas-atlas-postgres -- psql -U atlas -d atlas -c "\\dt"

This script checks every failure point in the upload -> Postgres -> query -> LLM chain.
"""

import os
import sys
import json

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"
INFO = "\033[94mINFO\033[0m"


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(label, ok, detail=""):
    status = PASS if ok else FAIL
    print(f"  [{status}] {label}")
    if detail:
        print(f"         {detail}")
    return ok


def warn(label, detail=""):
    print(f"  [{WARN}] {label}")
    if detail:
        print(f"         {detail}")


def info(label, detail=""):
    print(f"  [{INFO}] {label}")
    if detail:
        print(f"         {detail}")


# =========================================================================
# 1. Environment variables
# =========================================================================
section("1. Environment Variables")

required_env = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
optional_env = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEMO_TOKEN_SECRET", "DEMO_VERIFY_PIN"]

all_env_ok = True
for var in required_env:
    val = os.getenv(var)
    ok = bool(val)
    if not ok:
        all_env_ok = False
    check(f"{var}", ok, f"= '{val}'" if ok else "MISSING")

for var in optional_env:
    val = os.getenv(var, "")
    has_val = bool(val)
    if has_val:
        # Mask secrets
        masked = val[:8] + "..." if len(val) > 12 else "(set)"
        info(f"{var}", f"= {masked}")
    else:
        warn(f"{var}", "not set")


# =========================================================================
# 2. Postgres connectivity
# =========================================================================
section("2. Postgres Connectivity")

try:
    import psycopg2
    check("psycopg2 importable", True)
except ImportError:
    check("psycopg2 importable", False, "pip install psycopg2-binary")
    sys.exit(1)

try:
    from atlas_data_loader import managed_connection, check_postgres

    pg_ok = check_postgres()
    check("check_postgres()", pg_ok)

    if pg_ok:
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
                info("Postgres version", ver.split(",")[0])
except Exception as e:
    check("Postgres connection", False, str(e))
    print("\n  Cannot continue without Postgres. Exiting.")
    sys.exit(1)


# =========================================================================
# 3. Schema state
# =========================================================================
section("3. Schema State")

with managed_connection() as conn:
    with conn.cursor() as cur:
        # Check tables exist
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = [r[0] for r in cur.fetchall()]
        required_tables = ["sites", "cutsheet_uploads", "cutsheet_connections",
                           "host_inventory", "burndown_connections", "cutsheet_raw_rows"]
        for t in required_tables:
            check(f"Table '{t}' exists", t in tables)

        # Check critical columns on cutsheet_connections
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'cutsheet_connections'
            ORDER BY ordinal_position
        """)
        cc_cols = [r[0] for r in cur.fetchall()]
        critical_cols = ["status_normalized", "a_model", "z_model",
                         "a_loc_cab_ru", "z_loc_cab_ru", "a_role", "z_role",
                         "cable_id", "section"]
        for col in critical_cols:
            check(f"Column 'cutsheet_connections.{col}'", col in cc_cols)

        # Verify raw_row migration completed
        if "raw_row" in cc_cols:
            warn("cutsheet_connections still has 'raw_row' column",
                 "Schema migration may not have completed - ip_lookup uses cutsheet_raw_rows now")

        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'cutsheet_uploads'"
        )
        upload_cols = [r[0] for r in cur.fetchall()]
        check("Column 'cutsheet_uploads.file_hash'", "file_hash" in upload_cols)
        check("Column 'cutsheet_uploads.is_active'", "is_active" in upload_cols)

        # Check materialized views
        cur.execute("""
            SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'
        """)
        matviews = [r[0] for r in cur.fetchall()]
        expected_views = ["optic_inventory_by_side", "optic_inventory_combined",
                          "cable_status_summary", "device_summary"]
        for v in expected_views:
            check(f"Materialized view '{v}'", v in matviews)


# =========================================================================
# 4. Data state
# =========================================================================
section("4. Data State")

with managed_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sites")
        site_count = cur.fetchone()[0]
        info(f"Sites: {site_count}")

        cur.execute("SELECT site_code, id FROM sites ORDER BY id")
        for row in cur.fetchall():
            info(f"  Site: {row[0]} (id={row[1]})")

        cur.execute("""
            SELECT cu.id, s.site_code, cu.filename, cu.is_active,
                   cu.row_count, cu.uploaded_by,
                   cu.created_at::text
            FROM cutsheet_uploads cu
            JOIN sites s ON cu.site_id = s.id
            ORDER BY cu.created_at DESC
            LIMIT 10
        """)
        uploads = cur.fetchall()
        info(f"Recent uploads: {len(uploads)}")
        for u in uploads:
            active_tag = "ACTIVE" if u[3] else "inactive"
            info(f"  Upload {u[0]}: {u[1]} | {u[2]} | {active_tag} | "
                 f"{u[4]} rows | by {u[5]} | {u[6]}")

        # Connection counts per active upload
        cur.execute("""
            SELECT cu.id, s.site_code, COUNT(cc.id) AS conn_count
            FROM cutsheet_uploads cu
            JOIN sites s ON cu.site_id = s.id
            LEFT JOIN cutsheet_connections cc ON cc.upload_id = cu.id
            WHERE cu.is_active = TRUE
            GROUP BY cu.id, s.site_code
            ORDER BY cu.id DESC
        """)
        active_uploads = cur.fetchall()
        if not active_uploads:
            warn("No active uploads found", "Upload a cutsheet first")
        for au in active_uploads:
            ok = au[2] > 0
            check(f"Upload {au[0]} ({au[1]}): {au[2]} connections",
                  ok, "" if ok else "0 rows loaded - canonicalization may have failed")

        # Status distribution for latest active upload
        if active_uploads:
            latest = active_uploads[0][0]
            cur.execute("""
                SELECT status_normalized, COUNT(*) AS cnt
                FROM cutsheet_connections
                WHERE upload_id = %s
                GROUP BY status_normalized
                ORDER BY cnt DESC
                LIMIT 10
            """, (latest,))
            statuses = cur.fetchall()
            info(f"Status distribution (upload {latest}):")
            for s in statuses:
                info(f"  {s[0] or '(empty)'}: {s[1]}")

            # Check for null/empty status_normalized
            cur.execute("""
                SELECT COUNT(*) FROM cutsheet_connections
                WHERE upload_id = %s
                  AND (status_normalized IS NULL OR status_normalized = '')
            """, (latest,))
            null_status = cur.fetchone()[0]
            if null_status > 0:
                warn(f"{null_status} rows have empty status_normalized",
                     "Status normalization may not be running during load")


# =========================================================================
# 5. Query router smoke test
# =========================================================================
section("5. Query Router Smoke Test")

try:
    from atlas_query_router import route_question
    from query_intent import classify_question

    test_questions = [
        ("How many optics are in the cutsheet?", "optic_count"),
        ("What is the overall status?", "site_overview"),
        ("How many SN5610s are there?", "model_search"),
        ("Show devices in rack dh202:041", "location_lookup"),
    ]

    # Need a site_id to test routing
    with managed_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.id, cu.id FROM cutsheet_uploads cu
                JOIN sites s ON cu.site_id = s.id
                WHERE cu.is_active = TRUE
                ORDER BY cu.created_at DESC LIMIT 1
            """)
            row = cur.fetchone()

    if row:
        test_site_id, test_upload_id = row
        for q, expected_type in test_questions:
            result = classify_question(q)
            classified_type = result.question_type if hasattr(result, 'question_type') else str(result)
            type_ok = classified_type == expected_type
            check(f"'{q[:40]}...' -> {classified_type}",
                  type_ok, "" if type_ok else f"expected {expected_type}")

            # Try actual route_question
            try:
                rr = route_question(q, test_site_id, upload_id=test_upload_id)
                has_context = rr.get("ok") and rr.get("row_count", 0) > 0
                if has_context:
                    info(f"  SQL returned {rr['row_count']} rows, "
                         f"{rr.get('token_estimate', '?')} tokens")
                else:
                    warn(f"  SQL returned 0 rows or error: {rr.get('error', 'no rows')}")
            except Exception as e:
                warn(f"  route_question failed: {e}")
    else:
        warn("No active uploads to test against")

except Exception as e:
    check("Query router import", False, str(e))


# =========================================================================
# 6. Gunicorn worker state (in-memory dicts)
# =========================================================================
section("6. In-Memory State (this worker only)")

try:
    from atlas_web_app import USER_CONTEXT, USER_SITE
    info(f"USER_CONTEXT keys: {list(USER_CONTEXT.keys()) or '(empty)'}")
    info(f"USER_SITE keys: {list(USER_SITE.keys()) or '(empty)'}")
    for user, site in USER_SITE.items():
        info(f"  {user}: site={site.get('site_code')} "
             f"upload_id={site.get('upload_id')}")
except Exception as e:
    warn(f"Could not inspect in-memory state: {e}")


# =========================================================================
# 7. LLM connectivity
# =========================================================================
section("7. LLM API Connectivity")

anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
openai_key = os.getenv("OPENAI_API_KEY", "")

if anthropic_key:
    info("Anthropic API key present", f"model={os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}")
elif openai_key:
    info("OpenAI API key present", f"model={os.getenv('OPENAI_MODEL', 'gpt-4o-mini')}")
else:
    warn("No LLM API key set", "AI Q&A will fail")


# =========================================================================
# Summary
# =========================================================================
section("DIAGNOSIS COMPLETE")
print("""
  If all checks pass but the browser still shows wrong answers:
  1. Open browser DevTools -> Network tab
  2. Upload a cutsheet and check the /api/upload-count response for pg_loaded
  3. Ask a question and check the /api/ask response for context_source
  4. If context_source is EMPTY_FALLBACK, the Postgres load failed during upload
  5. If context_source is IN_MEMORY, you hit a different gunicorn worker

  To force schema migration on existing Postgres:
    kubectl exec -it statefulset/atlas-atlas-postgres -- \\
      psql -U atlas -d atlas -f /docker-entrypoint-initdb.d/01_schema.sql
""")
