"""
atlas_query_router.py - Question classification and SQL template routing.

Classifies natural language questions using domain routers (query_intent),
selects the appropriate parameterized SQL template, executes it against
Postgres, and formats results for compact LLM context.

No LLM-generated SQL. All queries are pre-built templates with parameterized inputs.

Refactored 2026-04-19: monolithic regex list replaced with domain routers.
Old _PATTERNS list and inline extractors moved to query_intent.py / query_extractors.py.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from atlas_data_loader import managed_connection
from query_intent import classify_question, classify_with_context, IntentResult, QuestionContext
import query_extractors as ext

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Question type definitions
# ---------------------------------------------------------------------------

QUESTION_TYPES = [
    "optic_count",
    "model_search",
    "device_list",
    "z_device_list",
    "a_device_list",
    "role_lookup",
    "device_detail",
    "device_connections",
    "connection_status",
    "cable_status",
    "section_summary",
    "section_completion",
    "lldp_failures",
    "lldp_neighbor_mismatch",
    "link_status",
    "rack_summary",
    "location_lookup",
    "site_overview",
    "data_hall_summary",
    "ip_lookup",
    "node_compute",
    "upload_diff",
    "upload_list",
    "cross_site_models",
    "cross_site_optics",
    "cross_site_status",
    "trend_status",
    "trend_section",
    "general",
]


# ---------------------------------------------------------------------------
# SQL templates (parameterized, no injection risk)
# All templates support upload_id scoping:
#   AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
# When upload_id is None this condition is always true (full site scope).
# ---------------------------------------------------------------------------

_SQL_TEMPLATES: Dict[str, str] = {
    # B11: UNION ALL per-side aggregation. Counts each optic on the side it
    # actually appears on. Mixed-optic cables (A=X, Z=Y) count once for each
    # optic type instead of being silently grouped under the A-side optic.
    # cable_count = total optic instances (a_count + z_count).
    # Status counts are per-optic-instance, not per-cable.
    "optic_count": """
        SELECT
            optic_type,
            a_count + z_count                                              AS cable_count,
            a_count,
            z_count,
            in_service,
            failed,
            pending
        FROM (
            SELECT
                optic_type,
                SUM(CASE WHEN side = 'A' THEN 1 ELSE 0 END)              AS a_count,
                SUM(CASE WHEN side = 'Z' THEN 1 ELSE 0 END)              AS z_count,
                COUNT(*) FILTER (WHERE status_normalized IN
                    ('lldp_passed', 'human_verified', 'complete'))         AS in_service,
                COUNT(*) FILTER (WHERE status_normalized = 'lldp_failed') AS failed,
                COUNT(*) FILTER (WHERE status_normalized IN
                    ('not_run', 'not_terminated', 'pending', 'in_progress', 'addition')) AS pending
            FROM (
                SELECT a_optic AS optic_type, 'A' AS side, status_normalized
                FROM cutsheet_connections
                WHERE site_id = %(site_id)s
                  AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
                  AND a_optic IS NOT NULL AND a_optic != '' AND a_optic != 'nan'
                  AND (%(optic_filter)s = '' OR a_optic ILIKE %(optic_filter)s)
                  AND (%(section_filter)s = '' OR section ILIKE %(section_filter)s)
                  AND (%(location_filter)s = '' OR a_loc_cab_ru ILIKE %(location_filter)s
                       OR z_loc_cab_ru ILIKE %(location_filter)s)
                UNION ALL
                SELECT z_optic AS optic_type, 'Z' AS side, status_normalized
                FROM cutsheet_connections
                WHERE site_id = %(site_id)s
                  AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
                  AND z_optic IS NOT NULL AND z_optic != '' AND z_optic != 'nan'
                  AND (%(optic_filter)s = '' OR z_optic ILIKE %(optic_filter)s)
                  AND (%(section_filter)s = '' OR section ILIKE %(section_filter)s)
                  AND (%(location_filter)s = '' OR a_loc_cab_ru ILIKE %(location_filter)s
                       OR z_loc_cab_ru ILIKE %(location_filter)s)
            ) sides
            GROUP BY optic_type
        ) sub
        ORDER BY cable_count DESC
    """,

    "z_device_list": """
        SELECT device_name, connections, ports,
               COUNT(*) OVER () AS total_unique
        FROM (
            SELECT z_device AS device_name,
                   COUNT(*) AS connections,
                   COUNT(DISTINCT z_port) AS ports
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
            GROUP BY z_device
        ) sub
        ORDER BY connections DESC
        LIMIT 200
    """,

    "a_device_list": """
        SELECT device_name, connections, ports,
               COUNT(*) OVER () AS total_unique
        FROM (
            SELECT a_device AS device_name,
                   COUNT(*) AS connections,
                   COUNT(DISTINCT a_port) AS ports
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
            GROUP BY a_device
        ) sub
        ORDER BY connections DESC
        LIMIT 200
    """,

    "role_lookup": """
        WITH role_rows AS (
            SELECT 'A'   AS side,
                   a_role AS role,
                   a_device AS device_name,
                   a_model  AS model
            FROM cutsheet_connections
            WHERE site_id    = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND a_role  IS NOT NULL AND a_role  != '' AND a_role  != 'nan'
              AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
              AND (%(role_filter)s  = '' OR a_role  ILIKE %(role_filter)s)
              AND (%(side_filter)s  = '' OR %(side_filter)s = 'A')
            UNION ALL
            SELECT 'Z',
                   z_role,
                   z_device,
                   z_model
            FROM cutsheet_connections
            WHERE site_id    = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND z_role  IS NOT NULL AND z_role  != '' AND z_role  != 'nan'
              AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
              AND (%(role_filter)s  = '' OR z_role  ILIKE %(role_filter)s)
              AND (%(side_filter)s  = '' OR %(side_filter)s = 'Z')
        )
        SELECT role, side, device_name,
               MODE() WITHIN GROUP (ORDER BY model)
                   FILTER (WHERE model IS NOT NULL AND model != '') AS model,
               COUNT(*) AS connection_count
        FROM role_rows
        GROUP BY role, side, device_name
        ORDER BY role, side, device_name
        LIMIT 200
    """,

    "device_list": """
        SELECT device_name, COUNT(*) AS connections, COUNT(DISTINCT port) AS ports
        FROM (
            SELECT a_device AS device_name, a_port AS port
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
            UNION ALL
            SELECT z_device, z_port
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
        ) sub
        GROUP BY device_name
        ORDER BY connections DESC
        LIMIT 200
    """,

    "device_detail": """
        SELECT device_name, COUNT(*) AS connections, COUNT(DISTINCT port) AS ports
        FROM (
            SELECT a_device AS device_name, a_port AS port
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
              AND a_device ILIKE %(device_pattern)s
            UNION ALL
            SELECT z_device, z_port
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
              AND z_device ILIKE %(device_pattern)s
        ) sub
        GROUP BY device_name
        ORDER BY connections DESC
    """,

    "device_connections": """
        SELECT section, a_device, a_port, a_optic,
               z_device, z_port, z_optic, cable_id, status
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (a_device ILIKE %(device_pattern)s OR z_device ILIKE %(device_pattern)s)
        ORDER BY section, a_port
        LIMIT 200
    """,

    "connection_status": """
        SELECT status_normalized, status, COUNT(*) AS cnt
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND status_normalized IN ('lldp_passed', 'lldp_failed', 'human_verified')
        GROUP BY status_normalized, status
        ORDER BY cnt DESC
    """,

    "cable_status": """
        SELECT status_normalized, status, COUNT(*) AS cnt
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND status_normalized IN ('not_run', 'not_terminated', 'complete',
                                    'in_progress', 'addition', 'pending')
        GROUP BY status_normalized, status
        ORDER BY cnt DESC
    """,

    "section_summary": """
        SELECT section,
               COUNT(*) AS connections,
               COUNT(DISTINCT a_device) AS a_devices,
               COUNT(DISTINCT z_device) AS z_devices
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (%(section_name_filter)s = '' OR section ILIKE %(section_name_filter)s)
        GROUP BY section
        ORDER BY connections DESC
    """,

    "section_completion": """
        SELECT section,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status_normalized IN ('complete', 'lldp_passed', 'human_verified')) AS complete,
               COUNT(*) FILTER (WHERE status_normalized NOT IN ('complete', 'lldp_passed', 'human_verified')) AS incomplete,
               ROUND(100.0 * COUNT(*) FILTER (WHERE status_normalized IN ('complete', 'lldp_passed', 'human_verified')) / NULLIF(COUNT(*), 0), 1) AS pct_complete
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (%(section_name_filter)s = '' OR section ILIKE %(section_name_filter)s)
        GROUP BY section
        ORDER BY incomplete DESC, total DESC
    """,

    "lldp_failures": """
        SELECT section, a_device, a_port, z_device, z_port, cable_id, status
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND status_normalized = 'lldp_failed'
        ORDER BY section, a_device
        LIMIT 100
    """,

    "site_overview": """
        SELECT
            (SELECT COUNT(*) FROM cutsheet_connections
             WHERE site_id = %(site_id)s
               AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
            ) AS total_connections,
            (SELECT COUNT(DISTINCT a_device) FROM cutsheet_connections
             WHERE site_id = %(site_id)s
               AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
               AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
            ) AS total_devices,
            (SELECT COUNT(DISTINCT section) FROM cutsheet_connections
             WHERE site_id = %(site_id)s
               AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
            ) AS total_sections
    """,

    "data_hall_summary": """
        SELECT a_locode AS locode,
               COUNT(*) AS connections,
               COUNT(DISTINCT a_device) AS devices
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND a_locode IS NOT NULL AND a_locode != ''
        GROUP BY a_locode
        ORDER BY connections DESC
    """,

    "ip_lookup": """
        SELECT cc.a_device, cc.z_device, cc.a_port, cc.z_port, cc.status, rr.raw_row
        FROM cutsheet_connections cc
        LEFT JOIN cutsheet_raw_rows rr ON rr.connection_id = cc.id
        WHERE cc.site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR cc.upload_id = %(upload_id)s::bigint)
          AND rr.raw_row::text ILIKE %(search_pattern)s
        LIMIT 50
    """,

    "node_compute": """
        SELECT device_name, COUNT(*) AS connections, COUNT(DISTINCT port) AS ports
        FROM (
            SELECT a_device AS device_name, a_port AS port
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
              AND (a_device ILIKE '%%node%%'
                   OR a_device ILIKE '%%compute%%'
                   OR a_device ILIKE '%%gpu%%'
                   OR a_device ILIKE '%%server%%')
            UNION ALL
            SELECT z_device, z_port
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
              AND (z_device ILIKE '%%node%%'
                   OR z_device ILIKE '%%compute%%'
                   OR z_device ILIKE '%%gpu%%'
                   OR z_device ILIKE '%%server%%')
        ) sub
        GROUP BY device_name
        ORDER BY connections DESC
    """,

    "model_search": """
        SELECT device_name, model, connections,
               COUNT(*) OVER () AS total_unique
        FROM (
            SELECT device_name, model, SUM(connections) AS connections
            FROM (
                SELECT a_device AS device_name, a_model AS model, 1 AS connections
                FROM cutsheet_connections
                WHERE site_id = %(site_id)s
                  AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
                  AND (a_model ILIKE %(model_pattern)s OR a_device ILIKE %(model_pattern)s)
                  AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
                  AND (%(model_status_filters)s::text[] IS NULL OR status_normalized = ANY(%(model_status_filters)s::text[]))
                UNION ALL
                SELECT z_device AS device_name, z_model AS model, 1 AS connections
                FROM cutsheet_connections
                WHERE site_id = %(site_id)s
                  AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
                  AND (z_model ILIKE %(model_pattern)s OR z_device ILIKE %(model_pattern)s)
                  AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
                  AND (%(model_status_filters)s::text[] IS NULL OR status_normalized = ANY(%(model_status_filters)s::text[]))
                UNION ALL
                SELECT hostname AS device_name, model, 0 AS connections
                FROM host_inventory
                WHERE site_id = %(site_id)s
                  AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
                  AND (model ILIKE %(model_pattern)s OR hostname ILIKE %(model_pattern)s)
                  AND %(model_status_filters)s::text[] IS NULL
            ) combined
            GROUP BY device_name, model
        ) sub
        ORDER BY connections DESC, device_name
        LIMIT 200
    """,

    "link_status": """
        SELECT a_device, a_port, z_device, z_port, link_status, status,
               current_neighbor, current_neighbor_port, dct_notes
        FROM burndown_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
        ORDER BY link_status, a_device
        LIMIT 200
    """,

    "lldp_neighbor_mismatch": """
        SELECT a_device, a_port, z_device, z_port,
               current_neighbor, current_neighbor_port,
               link_status, status, dct_notes
        FROM burndown_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND current_neighbor IS NOT NULL
          AND current_neighbor != ''
          AND LOWER(TRIM(current_neighbor)) != LOWER(TRIM(z_device))
        ORDER BY a_device, a_port
        LIMIT 200
    """,

    "rack_summary": """
        WITH endpoint_rows AS (
            SELECT
                split_part(a_loc_cab_ru, ':', 1) || ':' ||
                LPAD(split_part(a_loc_cab_ru, ':', 2), 3, '0') AS rack_loc,
                a_device AS device_name,
                a_model AS model,
                a_optic AS optic,
                COALESCE(
                    NULLIF(cable_id, ''),
                    CONCAT_WS('|',
                        COALESCE(a_device, ''), COALESCE(a_port, ''),
                        COALESCE(z_device, ''), COALESCE(z_port, '')
                    )
                ) AS connection_key
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND a_loc_cab_ru IS NOT NULL AND a_loc_cab_ru != '' AND a_loc_cab_ru != 'nan'
              AND split_part(a_loc_cab_ru, ':', 1) != ''
              AND split_part(a_loc_cab_ru, ':', 2) != ''
              AND (%(location_filter)s = '' OR a_loc_cab_ru ILIKE %(location_filter)s)

            UNION ALL

            SELECT
                split_part(z_loc_cab_ru, ':', 1) || ':' ||
                LPAD(split_part(z_loc_cab_ru, ':', 2), 3, '0') AS rack_loc,
                z_device AS device_name,
                z_model AS model,
                z_optic AS optic,
                COALESCE(
                    NULLIF(cable_id, ''),
                    CONCAT_WS('|',
                        COALESCE(a_device, ''), COALESCE(a_port, ''),
                        COALESCE(z_device, ''), COALESCE(z_port, '')
                    )
                ) AS connection_key
            FROM cutsheet_connections
            WHERE site_id = %(site_id)s
              AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
              AND z_loc_cab_ru IS NOT NULL AND z_loc_cab_ru != '' AND z_loc_cab_ru != 'nan'
              AND split_part(z_loc_cab_ru, ':', 1) != ''
              AND split_part(z_loc_cab_ru, ':', 2) != ''
              AND (%(location_filter)s = '' OR z_loc_cab_ru ILIKE %(location_filter)s)
        ),
        rack_agg AS (
            SELECT
                rack_loc AS loc_cab_ru,
                COUNT(DISTINCT connection_key) AS connections,
                COUNT(DISTINCT device_name) FILTER (
                    WHERE device_name IS NOT NULL AND device_name != '' AND device_name != 'nan'
                ) AS devices,
                STRING_AGG(DISTINCT model, ', ' ORDER BY model) FILTER (
                    WHERE model IS NOT NULL AND model != '' AND model != 'nan'
                ) AS models,
                STRING_AGG(DISTINCT optic, ', ' ORDER BY optic) FILTER (
                    WHERE optic IS NOT NULL AND optic != '' AND optic != 'nan'
                ) AS optics,
                COUNT(optic) FILTER (
                    WHERE optic IS NOT NULL AND optic != '' AND optic != 'nan'
                ) AS optic_count
            FROM endpoint_rows
            GROUP BY rack_loc
        ),
        site_totals AS (
            SELECT
                COUNT(DISTINCT rack_loc) AS total_racks,
                COUNT(DISTINCT connection_key) AS site_unique_connections
            FROM endpoint_rows
        )
        SELECT
            r.loc_cab_ru,
            r.connections,
            r.devices,
            r.models,
            r.optics,
            r.optic_count,
            s.total_racks,
            s.site_unique_connections
        FROM rack_agg r
        CROSS JOIN site_totals s
        ORDER BY r.connections DESC, r.loc_cab_ru
        LIMIT 50
    """,

    "location_lookup": """
        SELECT 'cutsheet' AS source,
               a_device AS device, a_model AS model,
               a_loc_cab_ru AS loc_cab_ru, a_port AS port,
               a_optic AS optic, z_optic AS peer_optic,
               z_device AS peer_device, z_port AS peer_port, status
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND a_loc_cab_ru ILIKE %(location_pattern)s
        UNION ALL
        SELECT 'cutsheet' AS source,
               z_device AS device, z_model AS model,
               z_loc_cab_ru AS loc_cab_ru, z_port AS port,
               z_optic AS optic, a_optic AS peer_optic,
               a_device AS peer_device, a_port AS peer_port, status
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND z_loc_cab_ru ILIKE %(location_pattern)s
        UNION ALL
        SELECT 'inventory' AS source,
               hostname AS device, model,
               rack AS loc_cab_ru, '' AS port,
               '' AS optic, '' AS peer_optic,
               '' AS peer_device, '' AS peer_port, status
        FROM host_inventory
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND rack ILIKE %(location_pattern)s
        ORDER BY source, device
        LIMIT 100
    """,

    "upload_diff": """
        WITH upload_a AS (
            SELECT upload_id, site_id, section, a_device, a_port, a_optic,
                   z_device, z_port, z_optic, cable_id, status, status_normalized,
                   a_model, z_model, a_loc_cab_ru, z_loc_cab_ru, a_role, z_role
            FROM cutsheet_connections
            WHERE upload_id = %(upload_id_a)s::bigint
              AND site_id = %(site_id)s::bigint
        ),
        upload_b AS (
            SELECT upload_id, site_id, section, a_device, a_port, a_optic,
                   z_device, z_port, z_optic, cable_id, status, status_normalized,
                   a_model, z_model, a_loc_cab_ru, z_loc_cab_ru, a_role, z_role
            FROM cutsheet_connections
            WHERE upload_id = %(upload_id_b)s::bigint
              AND site_id = %(site_id)s::bigint
        ),
        removed AS (
            SELECT 'removed' AS change_type, a.section, a.a_device, a.a_port,
                   a.z_device, a.z_port, a.a_optic, a.z_optic, a.status,
                   a.a_model, a.z_model, a.a_role, a.z_role,
                   a.a_loc_cab_ru, a.z_loc_cab_ru, a.cable_id
            FROM upload_a a
            WHERE NOT EXISTS (
                SELECT 1 FROM upload_b b
                WHERE a.a_device = b.a_device AND a.a_port = b.a_port
                  AND a.z_device = b.z_device AND a.z_port = b.z_port
            )
        ),
        added AS (
            SELECT 'added' AS change_type, b.section, b.a_device, b.a_port,
                   b.z_device, b.z_port, b.a_optic, b.z_optic, b.status,
                   b.a_model, b.z_model, b.a_role, b.z_role,
                   b.a_loc_cab_ru, b.z_loc_cab_ru, b.cable_id
            FROM upload_b b
            WHERE NOT EXISTS (
                SELECT 1 FROM upload_a a
                WHERE a.a_device = b.a_device AND a.a_port = b.a_port
                  AND a.z_device = b.z_device AND a.z_port = b.z_port
            )
        ),
        status_changed AS (
            SELECT 'status_changed' AS change_type, a.section, a.a_device, a.a_port,
                   a.z_device, a.z_port, a.a_optic, a.z_optic,
                   a.status || ' -> ' || b.status AS status,
                   a.a_model, a.z_model, a.a_role, a.z_role,
                   a.a_loc_cab_ru, a.z_loc_cab_ru, a.cable_id
            FROM upload_a a
            INNER JOIN upload_b b
                ON a.a_device = b.a_device AND a.a_port = b.a_port
               AND a.z_device = b.z_device AND a.z_port = b.z_port
            WHERE COALESCE(a.status, '') != COALESCE(b.status, '')
        ),
        optic_changed AS (
            SELECT 'optic_changed' AS change_type, a.section, a.a_device, a.a_port,
                   a.z_device, a.z_port,
                   a.a_optic || ' -> ' || b.a_optic || ' / ' || a.z_optic || ' -> ' || b.z_optic AS a_optic,
                   NULL AS z_optic, a.status,
                   a.a_model, a.z_model, a.a_role, a.z_role,
                   a.a_loc_cab_ru, a.z_loc_cab_ru, a.cable_id
            FROM upload_a a
            INNER JOIN upload_b b
                ON a.a_device = b.a_device AND a.a_port = b.a_port
               AND a.z_device = b.z_device AND a.z_port = b.z_port
            WHERE (COALESCE(a.a_optic, '') != COALESCE(b.a_optic, '')
                OR COALESCE(a.z_optic, '') != COALESCE(b.z_optic, ''))
              AND COALESCE(a.status, '') = COALESCE(b.status, '')
        )
        SELECT change_type, COUNT(*) AS count,
               ARRAY_AGG(
                   JSON_BUILD_OBJECT(
                       'section', section, 'a_device', a_device, 'a_port', a_port,
                       'z_device', z_device, 'z_port', z_port, 'a_optic', a_optic,
                       'z_optic', z_optic, 'status', status, 'a_model', a_model,
                       'z_model', z_model, 'cable_id', cable_id
                   ) ORDER BY section, a_device, a_port
               ) AS items
        FROM (
            SELECT * FROM removed UNION ALL SELECT * FROM added
            UNION ALL SELECT * FROM status_changed UNION ALL SELECT * FROM optic_changed
        ) AS all_changes
        GROUP BY change_type
        ORDER BY CASE change_type
            WHEN 'removed' THEN 1 WHEN 'added' THEN 2
            WHEN 'status_changed' THEN 3 WHEN 'optic_changed' THEN 4 ELSE 5
        END
    """,

    "upload_list": """
        SELECT id, filename, row_count, created_at, is_active, uploaded_by, profile
        FROM cutsheet_uploads
        WHERE site_id = %(site_id)s::bigint
        ORDER BY created_at DESC
        LIMIT 50
    """,

    # Cross-site queries intentionally ignore upload_id — they join across all
    # active uploads (cu.is_active = TRUE) for every site. The upload_id param
    # is present in the params dict but unused by these templates.
    "cross_site_models": """
        SELECT model, site_code, sites_present, connection_count
        FROM (
            SELECT
                COALESCE(NULLIF(a_model, ''), NULLIF(z_model, '')) AS model,
                s.site_code,
                COUNT(DISTINCT s.id) OVER (
                    PARTITION BY COALESCE(NULLIF(a_model, ''), NULLIF(z_model, ''))
                ) AS sites_present,
                COUNT(*) AS connection_count
            FROM cutsheet_connections cc
            JOIN cutsheet_uploads cu ON cc.upload_id = cu.id
            JOIN sites s ON cc.site_id = s.id
            WHERE cu.is_active = TRUE
              AND COALESCE(NULLIF(a_model, ''), NULLIF(z_model, '')) IS NOT NULL
              AND COALESCE(NULLIF(a_model, ''), NULLIF(z_model, '')) != 'nan'
            GROUP BY COALESCE(NULLIF(a_model, ''), NULLIF(z_model, '')), s.site_code, s.id
        ) sub
        ORDER BY model, site_code
    """,

    "cross_site_optics": """
        SELECT optic_type, site_code, cable_count, in_service, failed, pending
        FROM (
            SELECT
                COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')) AS optic_type,
                s.site_code,
                COUNT(*) AS cable_count,
                COUNT(*) FILTER (WHERE status_normalized IN
                    ('lldp_passed', 'human_verified', 'complete')) AS in_service,
                COUNT(*) FILTER (WHERE status_normalized = 'lldp_failed') AS failed,
                COUNT(*) FILTER (WHERE status_normalized IN
                    ('not_run', 'not_terminated', 'pending', 'in_progress', 'addition')) AS pending
            FROM cutsheet_connections cc
            JOIN cutsheet_uploads cu ON cc.upload_id = cu.id
            JOIN sites s ON cc.site_id = s.id
            WHERE cu.is_active = TRUE
              AND COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')) IS NOT NULL
              AND COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')) != 'nan'
            GROUP BY COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')), s.site_code
        ) sub
        ORDER BY optic_type, site_code
    """,

    "cross_site_status": """
        SELECT site_code, status_normalized, connection_count
        FROM (
            SELECT s.site_code, cc.status_normalized, COUNT(*) AS connection_count
            FROM cutsheet_connections cc
            JOIN cutsheet_uploads cu ON cc.upload_id = cu.id
            JOIN sites s ON cc.site_id = s.id
            WHERE cu.is_active = TRUE
            GROUP BY s.site_code, cc.status_normalized
        ) sub
        ORDER BY site_code, connection_count DESC
    """,

    # NOTE: trend_status intentionally includes ALL uploads (active and inactive)
    # to show the full historical timeline. If only active uploads are desired,
    # add: AND u.is_active = TRUE to the WHERE clause.
    "trend_status": """
        SELECT
            u.id AS upload_id, u.filename, u.created_at,
            COUNT(*) AS total_connections,
            COUNT(*) FILTER (WHERE c.status_normalized = 'lldp_passed') AS lldp_passed_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'lldp_failed') AS lldp_failed_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'complete') AS complete_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'human_verified') AS human_verified_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'not_run') AS not_run_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'not_terminated') AS not_terminated_count,
            COUNT(*) FILTER (WHERE c.status_normalized IN
                ('lldp_passed', 'human_verified', 'complete')) AS completion_total,
            ROUND(100.0 * COUNT(*) FILTER (WHERE c.status_normalized IN
                ('lldp_passed', 'human_verified', 'complete')) / NULLIF(COUNT(*), 0), 1) AS completion_percentage
        FROM cutsheet_uploads u
        LEFT JOIN cutsheet_connections c ON u.id = c.upload_id AND c.site_id = %(site_id)s
        WHERE u.site_id = %(site_id)s
        GROUP BY u.id, u.filename, u.created_at
        ORDER BY u.created_at ASC
        LIMIT 10
    """,

    # NOTE: trend_section intentionally includes ALL uploads for historical view.
    # Add u.is_active = TRUE filter if only active snapshots are desired.
    "trend_section": """
        SELECT
            u.id AS upload_id, u.filename, u.created_at, c.section,
            COUNT(*) AS total_connections,
            COUNT(*) FILTER (WHERE c.status_normalized = 'lldp_passed') AS lldp_passed_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'lldp_failed') AS lldp_failed_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'complete') AS complete_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'human_verified') AS human_verified_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'not_run') AS not_run_count,
            COUNT(*) FILTER (WHERE c.status_normalized = 'not_terminated') AS not_terminated_count,
            COUNT(*) FILTER (WHERE c.status_normalized IN
                ('lldp_passed', 'human_verified', 'complete')) AS completion_total,
            ROUND(100.0 * COUNT(*) FILTER (WHERE c.status_normalized IN
                ('lldp_passed', 'human_verified', 'complete')) / NULLIF(COUNT(*), 0), 1) AS completion_percentage
        FROM cutsheet_uploads u
        LEFT JOIN cutsheet_connections c ON u.id = c.upload_id AND c.site_id = %(site_id)s
        WHERE u.site_id = %(site_id)s
          AND (%(section_name_filter)s = '' OR c.section ILIKE %(section_name_filter)s)
        GROUP BY u.id, u.filename, u.created_at, c.section
        ORDER BY u.created_at ASC, c.section ASC
        LIMIT 100
    """,

    "general": """
        SELECT 'device_count' AS metric,
               COUNT(DISTINCT a_device)::text AS value
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
        UNION ALL
        SELECT 'connection_count',
               COUNT(*)::text
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
        UNION ALL
        SELECT 'section_count',
               COUNT(DISTINCT section)::text
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
    """,
}

_MODEL_SEARCH_RAW_COUNT_SQL = """
    SELECT
        COUNT(*) AS cutsheet_occurrences,
        COUNT(*) FILTER (WHERE side = 'A') AS a_side_occurrences,
        COUNT(*) FILTER (WHERE side = 'Z') AS z_side_occurrences,
        COUNT(DISTINCT device_name) AS cutsheet_unique_devices
    FROM (
        SELECT a_device AS device_name, 'A' AS side
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (a_model ILIKE %(model_pattern)s OR a_device ILIKE %(model_pattern)s)
          AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
        UNION ALL
        SELECT z_device AS device_name, 'Z' AS side
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (z_model ILIKE %(model_pattern)s OR z_device ILIKE %(model_pattern)s)
          AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
    ) combined
"""

_MODEL_SEARCH_STATUS_COUNT_SQL = """
    SELECT
        COUNT(DISTINCT location) FILTER (
            WHERE location IS NOT NULL AND location != '' AND location != 'nan'
        ) AS matching_device_locations,
        COUNT(DISTINCT device_name) FILTER (
            WHERE device_name IS NOT NULL AND device_name != '' AND device_name != 'nan'
        ) AS matching_device_names,
        COUNT(*) AS matching_cutsheet_rows,
        COUNT(*) FILTER (WHERE side = 'A') AS a_side_rows,
        COUNT(*) FILTER (WHERE side = 'Z') AS z_side_rows
    FROM (
        SELECT a_loc_cab_ru AS location, a_device AS device_name, 'A' AS side
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (a_model ILIKE %(model_pattern)s OR a_device ILIKE %(model_pattern)s)
          AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
          AND status_normalized = ANY(%(model_status_filters)s::text[])
        UNION ALL
        SELECT z_loc_cab_ru AS location, z_device AS device_name, 'Z' AS side
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (z_model ILIKE %(model_pattern)s OR z_device ILIKE %(model_pattern)s)
          AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
          AND status_normalized = ANY(%(model_status_filters)s::text[])
    ) combined
"""

_MODEL_SEARCH_UNIQUE_COUNT_SQL = """
    SELECT
        COUNT(DISTINCT device_name) AS total_unique_devices,
        COUNT(DISTINCT device_name) FILTER (WHERE source = 'cutsheet') AS cutsheet_unique_devices,
        COUNT(DISTINCT device_name) FILTER (WHERE source = 'host_inventory') AS inventory_unique_devices
    FROM (
        SELECT a_device AS device_name, 'cutsheet' AS source
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (a_model ILIKE %(model_pattern)s OR a_device ILIKE %(model_pattern)s)
          AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
        UNION
        SELECT z_device AS device_name, 'cutsheet' AS source
        FROM cutsheet_connections
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (z_model ILIKE %(model_pattern)s OR z_device ILIKE %(model_pattern)s)
          AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
        UNION
        SELECT hostname AS device_name, 'host_inventory' AS source
        FROM host_inventory
        WHERE site_id = %(site_id)s
          AND (%(upload_id)s::bigint IS NULL OR upload_id = %(upload_id)s::bigint)
          AND (model ILIKE %(model_pattern)s OR hostname ILIKE %(model_pattern)s)
          AND hostname IS NOT NULL AND hostname != '' AND hostname != 'nan'
    ) combined
"""


def _model_search_mode(question: str, *, has_status_filter: bool = False) -> str:
    normalized = " ".join(question.lower().split())
    is_count_question = bool(
        re.search(r"\bhow\s+many\b", normalized)
        or re.search(r"\bcount\b", normalized)
        or re.search(r"\btotal\s+(?:count|number)\b", normalized)
    )
    if not is_count_question:
        return "list"
    if has_status_filter:
        return "status_count"
    if re.search(r"\b(?:unique|distinct)\b", normalized):
        return "unique_count"
    return "raw_count"


# ---------------------------------------------------------------------------
# Classification and routing
# ---------------------------------------------------------------------------

def _escape_ilike(value: str) -> str:
    """Escape ILIKE metacharacters (%, _, \\) so user input can't widen query scope."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_location_pattern(location: str) -> str:
    """Build a scoped ILIKE pattern for exact locs, racks, or hall prefixes."""
    if not location:
        return "%__NO_LOCATION__%"

    loc = location.strip().lower()

    if re.fullmatch(r"[a-z]{1,4}\d+:\d+:\d+", loc):
        return _escape_ilike(loc)

    m = re.fullmatch(r"([a-z]{1,4}\d+):(\d{1,4})", loc)
    if m:
        hall, rack = m.groups()
        return f"{_escape_ilike(hall)}%:{_escape_ilike(rack.zfill(3))}:%"

    if re.fullmatch(r"[a-z]{1,4}\d+", loc):
        return f"{_escape_ilike(loc)}%:%"

    if re.fullmatch(r"\d{1,4}", loc):
        return ""

    return f"%{_escape_ilike(loc)}%"


def build_query_params(
    question: str, qtype: str, site_id: int, upload_id: Optional[int] = None,
    *,
    ctx: Optional[QuestionContext] = None,
) -> Dict[str, Any]:
    """Build the parameter dict for the SQL template.

    Accepts an optional pre-built QuestionContext so extractors don't re-run
    when called from route_question().  If ctx is None, extractors run fresh.
    """
    params: Dict[str, Any] = {"site_id": site_id, "upload_id": upload_id}

    # Use pre-extracted values from ctx when available, else call extractors directly
    _loc = ctx.extracted_location if ctx else ext.extract_location(question)
    _optic = ctx.extracted_optic if ctx else ext.extract_optic_type(question)
    _section_filter = ctx.extracted_section_filter if ctx else ext.extract_section_filter(question)
    _device = ctx.extracted_device if ctx else ext.extract_device_name(question)
    _section_name = ctx.extracted_section if ctx else ext.extract_section_name(question)
    _model = ctx.extracted_model if ctx else ext.extract_model(question)
    _model_status_filters, _model_status_label = (
        ext.extract_model_status_filter(question)
    )
    _role = ctx.extracted_role if ctx else ext.extract_role_and_side(question)[0]
    _side = ctx.extracted_side if ctx else ext.extract_role_and_side(question)[1]
    _ip = ctx.extracted_ip if ctx else ext.extract_ip(question)

    if qtype == "location_lookup":
        params["location_pattern"] = _build_location_pattern(_loc)
        params["location_input"] = _loc

    if qtype == "optic_count":
        params["optic_filter"] = f"%{_escape_ilike(_optic)}%" if _optic else ""
        params["section_filter"] = f"%{_escape_ilike(_section_filter)}%" if _section_filter else ""
        params["location_filter"] = _build_location_pattern(_loc) if _loc else ""

    if qtype == "rack_summary":
        params["location_filter"] = _build_location_pattern(_loc) if _loc else ""

    if qtype in ("device_detail", "device_connections"):
        params["device_pattern"] = f"%{_escape_ilike(_device)}%" if _device else "%"

    if qtype in ("section_summary", "section_completion"):
        params["section_name_filter"] = f"%{_escape_ilike(_section_name)}%" if _section_name else ""

    if qtype == "model_search":
        params["model_pattern"] = f"%{_escape_ilike(_model)}%" if _model else "%"
        params["model_status_filters"] = _model_status_filters or None
        params["model_status_label"] = _model_status_label
        params["model_search_mode"] = _model_search_mode(
            question,
            has_status_filter=bool(_model_status_filters),
        )

    if qtype == "role_lookup":
        params["role_filter"] = f"%{_escape_ilike(_role)}%" if _role else ""
        params["side_filter"] = _side

    if qtype == "ip_lookup":
        if _ip:
            params["search_pattern"] = f"%{_escape_ilike(_ip)}%"
        else:
            words = re.findall(r"\b[a-zA-Z0-9]{3,}\b", question)
            search_term = words[-1] if words else ""
            params["search_pattern"] = f"%{_escape_ilike(search_term)}%" if search_term else "%"

    if qtype == "upload_diff":
        upload_a, upload_b = ext.extract_upload_ids(question)
        params["upload_id_a"] = upload_a
        params["upload_id_b"] = upload_b

    # upload_list only needs site_id (already in params)

    # cross_site queries don't filter by site_id; the SQL joins across all
    # active uploads.  Params dict still carries site_id for signature compat.

    if qtype == "trend_section":
        params["section_name_filter"] = f"%{_escape_ilike(_section_name)}%" if _section_name else ""

    return params


def execute_query(qtype: str, params: Dict[str, Any]) -> Tuple[List[Dict], float]:
    """
    Execute the SQL template for the given question type.
    Returns (rows_as_dicts, elapsed_seconds).
    """
    sql = _SQL_TEMPLATES.get(qtype, _SQL_TEMPLATES["general"])
    if qtype == "model_search":
        mode = params.get("model_search_mode", "list")
        if mode == "raw_count":
            sql = _MODEL_SEARCH_RAW_COUNT_SQL
        elif mode == "status_count":
            sql = _MODEL_SEARCH_STATUS_COUNT_SQL
        elif mode == "unique_count":
            sql = _MODEL_SEARCH_UNIQUE_COUNT_SQL
    t0 = time.time()
    with managed_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    elapsed = round(time.time() - t0, 4)
    return rows, elapsed


def format_results_for_llm(qtype: str, rows: List[Dict], question: str = "") -> str:
    """Format query results into a compact string for LLM context."""
    if not rows:
        if qtype == "lldp_failures":
            return (
                f"Query type: {qtype}\n"
                "No LLDP: Failed connections found at this site.\n"
                "This site may use non-LLDP verification workflows. "
                "Use connection_status to see the actual status categories present."
            )
        if qtype == "role_lookup":
            role_filter, side_filter = ext.extract_role_and_side(question)
            side_note = f" on the {side_filter}-side" if side_filter else ""
            role_note = f" with role '{role_filter}'" if role_filter else ""
            return (
                f"Query type: {qtype}\n"
                f"No devices found{role_note}{side_note}.\n"
                "Possible reasons: (1) No SITE-HOSTS tab was uploaded with this cutsheet "
                "so host_inventory is empty and role columns were never populated. "
                "(2) The SITE-HOSTS tab exists but has no 'role' column. "
                "(3) The requested role/side filter matches zero records."
            )
        if qtype == "upload_diff":
            return f"Query type: {qtype}\nNo differences found between the two uploads."
        if qtype == "upload_list":
            return f"Query type: {qtype}\nNo uploads found for this site."
        if qtype in ("trend_status", "trend_section"):
            return (
                f"Query type: {qtype}\n"
                "No uploads found for this site. "
                "Trend analysis requires at least one cutsheet upload."
            )
        return f"Query type: {qtype}\nNo results found."

    lines = [f"Query type: {qtype}", f"Results ({len(rows)} rows):"]

    if qtype == "model_search":
        _LIST_CAP = 20
        _status_filters, status_label = ext.extract_model_status_filter(question)
        if status_label:
            lines.append(f"  Status filter: {status_label}")
        if rows and "matching_device_locations" in rows[0]:
            row = rows[0]
            lines.append(
                f"  Unique device locations matching pattern: {row.get('matching_device_locations', 0)}"
            )
            lines.append(
                f"  Unique hostnames matching filter: {row.get('matching_device_names', 0)}"
            )
            lines.append(
                f"  Matching cutsheet rows: {row.get('matching_cutsheet_rows', 0)}"
                f"  |  A-side rows: {row.get('a_side_rows', 0)}"
                f"  |  Z-side rows: {row.get('z_side_rows', 0)}"
            )
            return "\n".join(lines)
        if rows and "cutsheet_occurrences" in rows[0]:
            row = rows[0]
            lines.append(f"  Total cutsheet appearances matching pattern: {row.get('cutsheet_occurrences', 0)}")
            lines.append(
                f"  A-side appearances: {row.get('a_side_occurrences', 0)}"
                f"  |  Z-side appearances: {row.get('z_side_occurrences', 0)}"
            )
            lines.append(f"  Unique devices represented in cutsheet: {row.get('cutsheet_unique_devices', 0)}")
            return "\n".join(lines)
        if rows and "total_unique_devices" in rows[0]:
            row = rows[0]
            lines.append(f"  Total unique devices matching pattern: {row.get('total_unique_devices', 0)}")
            lines.append(f"  In cutsheet connections: {row.get('cutsheet_unique_devices', 0)} device(s)")
            lines.append(f"  In host inventory only: {row.get('inventory_unique_devices', 0)} device(s)")
            return "\n".join(lines)
        distinct_devices = [r for r in rows if r["connections"] > 0]
        inventory_only = [r for r in rows if r["connections"] == 0]
        total_unique = rows[0].get("total_unique") if rows else 0
        truncated = len(rows) == 200 and total_unique and total_unique > 200
        if total_unique:
            lines.append(
                f"  Total distinct devices matching pattern: {total_unique}"
                + (" (showing top 200 by matching row count)" if truncated else "")
            )
        else:
            lines.append(f"  Total distinct devices matching pattern: {len(rows)}")
        if distinct_devices:
            lines.append(
                f"  In cutsheet connections: {len(distinct_devices)} device(s)"
                + (f" (showing first {_LIST_CAP})" if len(distinct_devices) > _LIST_CAP else "")
            )
            for r in distinct_devices[:_LIST_CAP]:
                model_tag = f" [model={r['model']}]" if r.get("model") else ""
                lines.append(f"    {r['device_name']}{model_tag} ({r['connections']} connections)")
        if inventory_only:
            lines.append(
                f"  In host inventory only: {len(inventory_only)} device(s)"
                + (f" (showing first {_LIST_CAP})" if len(inventory_only) > _LIST_CAP else "")
            )
            for r in inventory_only[:_LIST_CAP]:
                model_tag = f" [model={r['model']}]" if r.get("model") else ""
                lines.append(f"    {r['device_name']}{model_tag}")
        if not rows:
            lines.append("  No devices found matching this model pattern.")
        return "\n".join(lines)

    if qtype == "link_status":
        up = [r for r in rows if str(r.get("link_status", "")).lower() == "up"]
        down = [r for r in rows if str(r.get("link_status", "")).lower() == "down"]
        other = [r for r in rows if str(r.get("link_status", "")).lower() not in ("up", "down")]
        lines.append(f"  Links up: {len(up)}  |  Links down: {len(down)}  |  Other: {len(other)}")
        for r in down:
            lines.append(
                f"  [DOWN] {r['a_device']}:{r['a_port']} -> {r['z_device']}:{r['z_port']}"
                + (f"  neighbor={r['current_neighbor']}" if r.get("current_neighbor") else "")
                + (f"  note={r['dct_notes']}" if r.get("dct_notes") else "")
            )
        for r in other:
            lines.append(
                f"  [{r.get('link_status', '?').upper()}] {r['a_device']}:{r['a_port']} -> "
                f"{r['z_device']}:{r['z_port']}"
            )
        return "\n".join(lines)

    if qtype == "lldp_neighbor_mismatch":
        lines.append(f"  Total mismatches found: {len(rows)}")
        for r in rows:
            link = str(r.get("link_status", "")).upper() or "?"
            lines.append(
                f"  [{link}] {r['a_device']}:{r['a_port']}"
                f"  expected={r['z_device']} actual={r['current_neighbor']}"
                + (f"  neighbor_port={r['current_neighbor_port']}" if r.get("current_neighbor_port") else "")
                + (f"  note={r['dct_notes']}" if r.get("dct_notes") else "")
            )
        return "\n".join(lines)

    if qtype == "role_lookup":
        role_filter, side_filter = ext.extract_role_and_side(question)
        if role_filter:
            lines.append(f"  Filter: role contains '{role_filter}'")
        if side_filter:
            lines.append(f"  Filter: {side_filter}-side only")

        # Group rows by (role, side) for structured output
        by_role_side: Dict[Tuple[str, str], List[Dict]] = {}
        for r in rows:
            key = (r.get("role") or "unknown", r.get("side") or "?")
            by_role_side.setdefault(key, []).append(r)

        _DEVICE_CAP = 20
        if not role_filter:
            # Summary mode: no specific role requested — show unique device counts per role/side
            lines.append("  Role inventory (from host_inventory):")
            for (role, side), devices in sorted(by_role_side.items()):
                lines.append(f"    {role} ({side}-side): {len(devices)} unique device(s)")
            lines.append(
                "  Note: role data only covers devices present in the SITE-HOSTS tab. "
                "Devices not in host_inventory have no role assigned."
            )
        else:
            # Specific role requested — list devices, grouped by side
            total_unique = len(rows)
            truncated = total_unique == 200
            lines.append(f"  Unique devices: {total_unique}" + (" (showing top 200)" if truncated else ""))
            for (role, side), devices in sorted(by_role_side.items()):
                lines.append(f"  {role} ({side}-side): {len(devices)} device(s)")
                for r in devices[:_DEVICE_CAP]:
                    model_tag = f" [{r['model']}]" if r.get("model") else ""
                    lines.append(f"    {r['device_name']}{model_tag} ({r['connection_count']} connections)")
                if len(devices) > _DEVICE_CAP:
                    lines.append(f"    ... and {len(devices) - _DEVICE_CAP} more")
        return "\n".join(lines)

    if qtype == "optic_count":
        optic_filter = ext.extract_optic_type(question)
        section_filter = ext.extract_section_filter(question)
        if optic_filter:
            lines.append(f"  Filter: optic type contains '{optic_filter}'")
        if section_filter:
            lines.append(f"  Filter: section contains '{section_filter}'")
        for r in rows:
            cable_count = r.get("cable_count") or 0
            a_count = r.get("a_count") or 0
            z_count = r.get("z_count") or 0
            in_service = r.get("in_service") or 0
            failed = r.get("failed") or 0
            pending = r.get("pending") or 0
            incomplete = failed + pending
            # Detect cables with optic on both sides (a+z > cable_count means overlap)
            both = a_count + z_count - cable_count
            side_str = f"A:{a_count} Z:{z_count}"
            if both > 0:
                side_str += f" both-sides:{both}"
            lines.append(
                f"  {r['optic_type']}: {cable_count} cables ({side_str}), "
                f"{in_service} in-service, {failed} failed, {pending} pending"
                + (f"  [{incomplete} incomplete]" if incomplete else "")
            )
    elif qtype in ("device_list", "device_detail", "node_compute", "z_device_list", "a_device_list"):
        side_label = {"z_device_list": "Z-side", "a_device_list": "A-side"}.get(qtype)
        if side_label:
            total_unique = rows[0].get("total_unique") if rows else len(rows)
            truncated = len(rows) == 200 and total_unique and total_unique > 200
            lines.append(f"  Side: {side_label} only")
            if truncated:
                lines.append(f"  Unique {side_label} devices: {total_unique} total (showing top 200 by connection count)")
            else:
                lines.append(f"  Unique {side_label} devices: {total_unique or len(rows)}")
        for r in rows:
            lines.append(f"  {r['device_name']}: {r['connections']} connections, {r['ports']} ports")
    elif qtype == "device_connections":
        for r in rows:
            lines.append(
                f"  [{r['status']}] {r['a_device']}:{r['a_port']} ({r['a_optic']}) "
                f"-> {r['z_device']}:{r['z_port']} ({r['z_optic']}) cable={r['cable_id']}"
            )
    elif qtype in ("connection_status", "cable_status"):
        total = sum(r.get("cnt", 0) for r in rows)
        label = "LLDP/verification statuses" if qtype == "connection_status" else "Cable run statuses"
        lines.append(f"  {label} (total: {total} connections):")
        for r in rows:
            cnt = r.get("cnt", 0)
            norm = r.get("status_normalized", "")
            raw = r.get("status") or norm
            pct = round(100.0 * cnt / total, 1) if total else 0
            lines.append(f"  {raw} [{norm}]: {cnt} ({pct}%)")
    elif qtype == "section_summary":
        total = sum(r["connections"] for r in rows)
        if len(rows) <= 5:
            lines.append(f"  Combined total: {total} connections across {len(rows)} section(s)")
        for r in rows:
            lines.append(
                f"  {r['section']}: {r['connections']} connections, "
                f"{r['a_devices']} A-devices, {r['z_devices']} Z-devices"
            )
        if len(rows) > 5:
            lines.append(f"  --- Total: {total} connections across {len(rows)} sections ---")
    elif qtype == "section_completion":
        total_all = sum(r["total"] for r in rows)
        complete_all = sum(r["complete"] for r in rows)
        incomplete_all = sum(r["incomplete"] for r in rows)
        pct_all = round(100.0 * complete_all / total_all, 1) if total_all else 0
        lines.append(f"  Site totals: {total_all} connections, {complete_all} complete, "
                     f"{incomplete_all} incomplete ({pct_all}% complete)")
        lines.append(f"  Sections: {len(rows)}")
        for r in rows:
            lines.append(
                f"  {r['section']}: {r['total']} total, {r['complete']} complete, "
                f"{r['incomplete']} incomplete ({r['pct_complete']}%)"
            )
    elif qtype == "lldp_failures":
        for r in rows:
            lines.append(
                f"  [{r['status']}] {r['a_device']}:{r['a_port']} -> "
                f"{r['z_device']}:{r['z_port']} cable={r['cable_id']} section={r['section']}"
            )
    elif qtype == "rack_summary":
        total_racks = rows[0].get("total_racks", len(rows)) if rows else 0
        site_unique_connections = rows[0].get("site_unique_connections") if rows else None
        truncated = len(rows) == 50 and total_racks and total_racks > 50
        lines.append(
            f"  Total racks: {total_racks}"
            + (" (showing top 50 by rack connection count)" if truncated else "")
        )
        if site_unique_connections is not None:
            lines.append(f"  Site unique connections: {site_unique_connections}")
        for i, r in enumerate(rows):
            rank = f"#{i + 1}" if i < 10 else "  "
            models = r.get("models") or "?"
            optics = r.get("optics") or ""
            optic_count = r.get("optic_count", 0)
            optic_tag = f" | {optic_count} optic(s): {optics}" if optics else " | 0 optics"
            lines.append(
                f"  {rank} {r['loc_cab_ru']}: {r['connections']} connections, "
                f"{r['devices']} device(s) [{models}]{optic_tag}"
            )
        return "\n".join(lines)
    elif qtype == "site_overview":
        if rows:
            r = rows[0]
            lines.append(f"  Total connections: {r['total_connections']}")
            lines.append(f"  Total devices: {r['total_devices']}")
            lines.append(f"  Total sections: {r['total_sections']}")
    elif qtype == "location_lookup":
        cutsheet_rows = [r for r in rows if r.get("source") == "cutsheet"]
        inventory_rows = [r for r in rows if r.get("source") == "inventory"]
        if cutsheet_rows:
            lines.append(f"  Cutsheet connections ({len(cutsheet_rows)} rows):")
            for r in cutsheet_rows:
                model_tag = f" [{r['model']}]" if r.get("model") else ""
                optic_tag = f" optic={r['optic']}" if r.get("optic") else ""
                peer_optic_tag = f" peer_optic={r['peer_optic']}" if r.get("peer_optic") else ""
                lines.append(
                    f"    {r['device']}{model_tag} port={r['port']}{optic_tag} "
                    f"-> {r['peer_device']}:{r['peer_port']}{peer_optic_tag} [{r['status']}] @ {r['loc_cab_ru']}"
                )
        if inventory_rows:
            lines.append(f"  Host inventory ({len(inventory_rows)} hosts):")
            for r in inventory_rows:
                model_tag = f" [{r['model']}]" if r.get("model") else ""
                lines.append(f"    {r['device']}{model_tag} rack={r['loc_cab_ru']} [{r['status']}]")
    elif qtype == "data_hall_summary":
        for r in rows:
            lines.append(f"  {r['locode']}: {r['connections']} connections, {r['devices']} devices")
    elif qtype == "ip_lookup":
        for r in rows:
            line = f"  {r['a_device']}:{r.get('a_port','')} -> {r['z_device']}:{r.get('z_port','')} [{r['status']}]"
            raw = r.get('raw_row')
            if isinstance(raw, dict):
                # find tokens from question long enough to be meaningful (IPs, hostnames, etc.)
                tokens = [t.lower() for t in question.split() if len(t) >= 4]
                matches = [(k, v) for k, v in raw.items()
                           if any(tok in str(v).lower() for tok in tokens)]
                matches.sort(key=lambda kv: len(str(kv[1])))
                if matches:
                    line += " | " + ", ".join(f"{k}:{v}" for k, v in matches[:3])
            lines.append(line)
    elif qtype == "upload_diff":
        total_changes = 0
        for r in rows:
            change_type = r.get("change_type", "unknown")
            count = r.get("count", 0)
            items = r.get("items", [])
            total_changes += count
            label = {"removed": "REMOVED", "added": "ADDED",
                     "status_changed": "STATUS CHANGED",
                     "optic_changed": "OPTIC CHANGED"}.get(change_type, change_type.upper())
            lines.append(f"  {label} ({count}):")
            for item in items[:10]:
                a_dev = item.get("a_device", "?")
                a_port = item.get("a_port", "?")
                z_dev = item.get("z_device", "?")
                z_port = item.get("z_port", "?")
                status = item.get("status", "")
                sec = item.get("section", "")
                line = f"    {a_dev}:{a_port} -> {z_dev}:{z_port}"
                if status:
                    line += f" [{status}]"
                if sec:
                    line += f" (sec: {sec})"
                lines.append(line)
            if count > 10:
                lines.append(f"    ... and {count - 10} more")
        lines.insert(2, f"  Total changes: {total_changes}")
    elif qtype == "upload_list":
        for r in rows:
            uid = r.get("id", "?")
            fname = r.get("filename", "unknown")
            rc = r.get("row_count", 0)
            created = str(r.get("created_at", "?"))[:19]
            active = " [ACTIVE]" if r.get("is_active") else ""
            uploader = f" by {r['uploaded_by']}" if r.get("uploaded_by") else ""
            profile = f" ({r['profile']})" if r.get("profile") else ""
            lines.append(f"  #{uid}: {fname} | {rc} rows | {created}{uploader}{profile}{active}")
    elif qtype == "cross_site_models":
        by_model: Dict[str, List[Dict]] = {}
        for r in rows:
            by_model.setdefault(r.get("model") or "unknown", []).append(r)
        total_sites = len(set(r.get("site_code") for r in rows if r.get("site_code")))
        for model in sorted(by_model):
            model_rows = by_model[model]
            total_conns = sum(r.get("connection_count", 0) for r in model_rows)
            lines.append(f"  {model}: {len(model_rows)}/{total_sites} sites, {total_conns} connections")
            for r in sorted(model_rows, key=lambda x: x.get("site_code", "")):
                lines.append(f"    @ {r.get('site_code', '?')}: {r.get('connection_count', 0)} connections")
    elif qtype == "cross_site_optics":
        by_optic: Dict[str, List[Dict]] = {}
        for r in rows:
            by_optic.setdefault(r.get("optic_type") or "unknown", []).append(r)
        total_sites = len(set(r.get("site_code") for r in rows if r.get("site_code")))
        for optic in sorted(by_optic):
            optic_rows = by_optic[optic]
            total_cables = sum(r.get("cable_count", 0) for r in optic_rows)
            total_svc = sum(r.get("in_service", 0) for r in optic_rows)
            total_fail = sum(r.get("failed", 0) for r in optic_rows)
            total_pend = sum(r.get("pending", 0) for r in optic_rows)
            lines.append(
                f"  {optic}: {total_cables} cables across {len(optic_rows)}/{total_sites} sites "
                f"({total_svc} in-service, {total_fail} failed, {total_pend} pending)"
            )
            for r in sorted(optic_rows, key=lambda x: x.get("site_code", "")):
                lines.append(
                    f"    @ {r.get('site_code', '?')}: {r.get('cable_count', 0)} cables "
                    f"({r.get('in_service', 0)} service, {r.get('failed', 0)} failed)"
                )
    elif qtype == "cross_site_status":
        by_site: Dict[str, List[Dict]] = {}
        for r in rows:
            by_site.setdefault(r.get("site_code") or "unknown", []).append(r)
        total_all = sum(r.get("connection_count", 0) for r in rows)
        lines.append(f"  Total across all sites: {total_all} connections")
        lines.append(f"  Sites: {len(by_site)}")
        for site in sorted(by_site):
            site_rows = by_site[site]
            site_total = sum(r.get("connection_count", 0) for r in site_rows)
            lines.append(f"  {site} ({site_total} total):")
            for r in sorted(site_rows, key=lambda x: x.get("connection_count", 0), reverse=True):
                status = r.get("status_normalized") or "unknown"
                cnt = r.get("connection_count", 0)
                pct = round(100.0 * cnt / site_total, 1) if site_total else 0
                lines.append(f"    {status}: {cnt} ({pct}%)")
    elif qtype == "trend_status":
        by_upload = {}
        for r in rows:
            uid = r["upload_id"]
            if uid not in by_upload:
                by_upload[uid] = r
        upload_list = sorted(by_upload.items(), key=lambda x: x[1]["created_at"])
        lines.append(f"  Timeline: {len(upload_list)} upload(s)")
        prev_row = None
        for uid, r in upload_list:
            date_str = str(r.get("created_at", "?"))[:19]
            total = r.get("total_connections") or 0
            pct = r.get("completion_percentage") or 0
            lp = r.get("lldp_passed_count") or 0
            lf = r.get("lldp_failed_count") or 0
            comp = r.get("complete_count") or 0
            hv = r.get("human_verified_count") or 0
            nr = r.get("not_run_count") or 0
            nt = r.get("not_terminated_count") or 0
            lines.append(f"  [{date_str}] {r.get('filename', '?')} | total={total} | {pct}% complete")
            lines.append(
                f"    LLDP_passed:{lp} LLDP_failed:{lf} complete:{comp} "
                f"human_verified:{hv} not_run:{nr} not_terminated:{nt}"
            )
            if prev_row is not None:
                d_total = total - (prev_row.get("total_connections") or 0)
                d_comp = (r.get("completion_total") or 0) - (prev_row.get("completion_total") or 0)
                d_fail = lf - (prev_row.get("lldp_failed_count") or 0)
                parts = []
                if d_total: parts.append(f"{d_total:+d} connections")
                if d_comp: parts.append(f"{d_comp:+d} complete")
                if d_fail: parts.append(f"{d_fail:+d} LLDP_failed")
                if parts:
                    lines.append(f"    Delta: {', '.join(parts)}")
            prev_row = r
        if len(upload_list) > 1:
            first = upload_list[0][1]
            last = upload_list[-1][1]
            fc = first.get("completion_total") or 0
            lc = last.get("completion_total") or 0
            ff = first.get("lldp_failed_count") or 0
            lf_val = last.get("lldp_failed_count") or 0
            lines.append("  TRAJECTORY:")
            lines.append(f"    Completion: {fc} -> {lc} ({lc - fc:+d})")
            lines.append(f"    LLDP failures: {ff} -> {lf_val} ({lf_val - ff:+d})")
            lines.append(f"    Trend: {'IMPROVING' if lc > fc else 'DEGRADING' if lc < fc else 'STABLE'}")
    elif qtype == "trend_section":
        by_upload_section: Dict[Tuple[int, str], Dict] = {}
        uploads_meta: Dict[int, Dict] = {}
        sections_seen: set = set()
        for r in rows:
            uid = r["upload_id"]
            sec = r.get("section") or "unspecified"
            by_upload_section[(uid, sec)] = r
            sections_seen.add(sec)
            if uid not in uploads_meta:
                uploads_meta[uid] = {"filename": r.get("filename"), "created_at": r.get("created_at")}
        uploads_ordered = sorted(uploads_meta.keys(), key=lambda u: uploads_meta[u]["created_at"])
        lines.append(f"  {len(uploads_ordered)} upload(s), {len(sections_seen)} section(s)")
        for sec in sorted(sections_seen):
            lines.append(f"  SECTION: {sec}")
            prev = None
            for uid in uploads_ordered:
                r = by_upload_section.get((uid, sec))
                if not r:
                    continue
                date_str = str(r.get("created_at", "?"))[:19]
                total = r.get("total_connections") or 0
                comp = r.get("completion_total") or 0
                pct = r.get("completion_percentage") or 0
                lines.append(f"    [{date_str}] total={total} complete={comp} ({pct}%)")
                if prev is not None:
                    dt = total - (prev.get("total_connections") or 0)
                    dc = comp - (prev.get("completion_total") or 0)
                    if dt or dc:
                        lines.append(f"      Delta: {dt:+d} total, {dc:+d} complete")
                prev = r
    else:
        # General / fallback
        for r in rows:
            lines.append(f"  {r.get('metric', '?')}: {r.get('value', '?')}")

    return "\n".join(lines)


def route_question(
    question: str, site_id: int, upload_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Full routing pipeline:
      1. Classify question
      2. Build params (including upload_id for per-cutsheet scoping)
      3. Execute SQL
      4. Format for LLM

    Returns dict with context string, metadata.
    """
    intent, ctx = classify_with_context(question)
    qtype = intent.question_type
    log.info("Classified %r -> %s (confidence=%s, reason=%s)",
             question[:80], qtype, intent.confidence, intent.reason)
    params = build_query_params(question, qtype, site_id, upload_id=upload_id, ctx=ctx)

    # upload_diff requires two explicit upload IDs. SQL uses = %(upload_id_a)s which
    # evaluates to NULL when IDs are absent — producing a misleading "no differences" result.
    if qtype == "upload_diff" and (not params.get("upload_id_a") or not params.get("upload_id_b")):
        found_ids = [str(uid) for uid in (params.get("upload_id_a"), params.get("upload_id_b")) if uid]
        if found_ids:
            missing_note = (
                f"Found upload ID {', '.join(found_ids)} but need two explicit upload IDs to compare. "
            )
        else:
            missing_note = "No upload IDs found in your question. "
        context_text = (
            "Query type: upload_diff\n"
            + missing_note
            + "Please specify two upload IDs to compare, e.g.: "
            "'compare upload 5 vs upload 6' or 'diff upload 3 and 4'.\n"
            "Use 'list uploads' or 'show upload history' to see available IDs."
        )
        return {
            "ok": True,
            "question_type": "upload_diff",
            "context": context_text,
            "row_count": 0,
            "query_elapsed_seconds": 0.0,
            "token_estimate": len(context_text.split()),
            "confidence": "low",
            "matched_domain": "diff",
            "reason": "upload_diff matched but no upload IDs found in question",
        }

    if qtype == "location_lookup" and not params.get("location_pattern"):
        raw_loc = params.get("location_input") or ""
        context_text = (
            "Query type: location_lookup\n"
            f"The location '{raw_loc}' is too broad by itself. "
            "Please include a data hall or full rack location, e.g. "
            "'dh202:041', 'dh2 041', or 'dh202:041:10'."
        )
        return {
            "ok": True,
            "question_type": "location_lookup",
            "context": context_text,
            "row_count": 0,
            "query_elapsed_seconds": 0.0,
            "token_estimate": len(context_text.split()),
            "confidence": "low",
            "matched_domain": "location",
            "reason": "location_lookup matched but extracted location was too broad",
        }

    try:
        rows, elapsed = execute_query(qtype, params)
        context_text = format_results_for_llm(qtype, rows, question)

        return {
            "ok": True,
            "question_type": qtype,
            "context": context_text,
            "row_count": len(rows),
            "query_elapsed_seconds": elapsed,
            "token_estimate": len(context_text.split()),
            "confidence": intent.confidence,
            "matched_domain": intent.matched_domain,
            "reason": intent.reason,
        }
    except Exception as exc:
        log.exception("Query routing failed for type=%s", qtype)
        return {
            "ok": False,
            "question_type": qtype,
            "error": str(exc),
        }
