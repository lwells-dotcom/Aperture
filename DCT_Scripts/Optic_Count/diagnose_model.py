#!/usr/bin/env python3
"""
diagnose_model.py - Check what a_model/z_model values look like in Postgres.

Usage:
    kubectl cp diagnose_model.py <pod>:/tmp/diagnose_model.py
    kubectl exec -it deploy/atlas-atlas-web -- python3 /tmp/diagnose_model.py
"""
from atlas_data_loader import managed_connection

with managed_connection() as conn:
    with conn.cursor() as cur:
        # 1. What are the top a_model values?
        print("=== Top 20 a_model values (upload 3) ===")
        cur.execute("""
            SELECT a_model, COUNT(*) AS cnt
            FROM cutsheet_connections
            WHERE upload_id = 3
              AND a_model IS NOT NULL AND a_model != '' AND a_model != 'nan'
            GROUP BY a_model
            ORDER BY cnt DESC
            LIMIT 20
        """)
        rows = cur.fetchall()
        if rows:
            for r in rows:
                print(f"  {r[0]!r}: {r[1]}")
        else:
            print("  (NO ROWS - a_model is empty/null for all connections)")

        # 2. What are the top z_model values?
        print("\n=== Top 20 z_model values (upload 3) ===")
        cur.execute("""
            SELECT z_model, COUNT(*) AS cnt
            FROM cutsheet_connections
            WHERE upload_id = 3
              AND z_model IS NOT NULL AND z_model != '' AND z_model != 'nan'
            GROUP BY z_model
            ORDER BY cnt DESC
            LIMIT 20
        """)
        rows = cur.fetchall()
        if rows:
            for r in rows:
                print(f"  {r[0]!r}: {r[1]}")
        else:
            print("  (NO ROWS - z_model is empty/null for all connections)")

        # 3. How many rows have empty a_model?
        print("\n=== Empty model counts (upload 3) ===")
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE a_model IS NULL OR a_model = '' OR a_model = 'nan') AS a_model_empty,
                COUNT(*) FILTER (WHERE z_model IS NULL OR z_model = '' OR z_model = 'nan') AS z_model_empty
            FROM cutsheet_connections
            WHERE upload_id = 3
        """)
        r = cur.fetchone()
        print(f"  Total rows: {r[0]}")
        print(f"  a_model empty: {r[1]} ({r[1]*100//max(r[0],1)}%)")
        print(f"  z_model empty: {r[2]} ({r[2]*100//max(r[0],1)}%)")

        # 4. Sample some raw rows to see what columns look like
        print("\n=== Sample 5 rows (all columns) ===")
        cur.execute("""
            SELECT a_device, a_model, a_port, z_device, z_model, z_port, status, status_normalized, section
            FROM cutsheet_connections
            WHERE upload_id = 3
            LIMIT 5
        """)
        cols = [desc[0] for desc in cur.description]
        for row in cur.fetchall():
            print("  ---")
            for col, val in zip(cols, row):
                print(f"  {col}: {val!r}")

        # 5. Does ILIKE %SN5610% match anything?
        print("\n=== Direct ILIKE test: %SN5610% ===")
        cur.execute("""
            SELECT COUNT(*) FROM cutsheet_connections
            WHERE upload_id = 3 AND a_model ILIKE '%%SN5610%%'
        """)
        print(f"  a_model ILIKE '%SN5610%': {cur.fetchone()[0]} rows")

        cur.execute("""
            SELECT COUNT(*) FROM cutsheet_connections
            WHERE upload_id = 3 AND z_model ILIKE '%%SN5610%%'
        """)
        print(f"  z_model ILIKE '%SN5610%': {cur.fetchone()[0]} rows")

        cur.execute("""
            SELECT COUNT(*) FROM cutsheet_connections
            WHERE upload_id = 3 AND a_device ILIKE '%%SN5610%%'
        """)
        print(f"  a_device ILIKE '%SN5610%': {cur.fetchone()[0]} rows")

        cur.execute("""
            SELECT COUNT(*) FROM cutsheet_connections
            WHERE upload_id = 3 AND z_device ILIKE '%%SN5610%%'
        """)
        print(f"  z_device ILIKE '%SN5610%': {cur.fetchone()[0]} rows")
