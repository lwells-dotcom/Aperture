"""
atlas_postgres_context.py - Bridge between query router and LLM context layer.

Builds targeted Postgres context for classified questions, or composite
context for general/unclassified questions.  Returns token estimates and
query timing for monitoring.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from atlas_data_loader import managed_connection

log = logging.getLogger(__name__)


def get_site_info(site_id: int) -> Optional[Dict[str, Any]]:
    """Fetch site metadata by ID."""
    import psycopg2.extras
    with managed_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM sites WHERE id = %s", (site_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def get_site_by_code(site_code: str) -> Optional[int]:
    """Look up site_id by code."""
    with managed_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM sites WHERE site_code = %s", (site_code,))
            row = cur.fetchone()
    return row[0] if row else None


def get_latest_upload(site_id: int) -> Optional[Dict[str, Any]]:
    """Get most recent active upload for a site (B9: respects soft-delete)."""
    import psycopg2.extras
    with managed_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM cutsheet_uploads "
                "WHERE site_id = %s AND is_active = TRUE "
                "ORDER BY created_at DESC LIMIT 1",
                (site_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def build_postgres_context(
    question: str, site_id: int, upload_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Build targeted LLM context by routing the question through
    atlas_query_router, which classifies and runs the right SQL template.
    """
    from atlas_query_router import route_question

    # B9: Default to latest active upload to avoid double-counting
    # when multiple uploads exist for the same site.
    if upload_id is None:
        from atlas_data_loader import managed_connection
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM cutsheet_uploads "
                    "WHERE site_id = %s AND is_active = TRUE "
                    "ORDER BY created_at DESC LIMIT 1",
                    (site_id,),
                )
                row = cur.fetchone()
                if row:
                    upload_id = row[0]

    try:
        result = route_question(question, site_id, upload_id=upload_id)
    except Exception as exc:
        log.exception("route_question raised outside its own handler")
        return {"ok": False, "error": str(exc), "question_type": "unknown"}

    if not result.get("ok"):
        return {
            "error": result.get("error", "Query routing failed"),
            "question_type": result.get("question_type", "unknown"),
        }

    # For general questions, use the richer composite context function
    # instead of the basic 3-metric SQL template.
    if result["question_type"] == "general":
        return build_postgres_context_for_general(
            site_id,
            upload_id=upload_id,
            confidence=result.get("confidence", "?"),
            classification_reason=result.get("reason", ""),
        )

    # Fetch site info for context header
    site_info = get_site_info(site_id)
    site_code = site_info["site_code"] if site_info else "UNKNOWN"

    # Include classification confidence so the LLM can calibrate certainty.
    conf = result.get("confidence", "?")
    reason = result.get("reason", "")
    conf_tag = f" [confidence: {conf}]" if conf != "high" else ""
    context_header = f"Site: {site_code} | Query type: {result['question_type']}{conf_tag}"

    return {
        "site_code": site_code,
        "site_id": site_id,
        "question_type": result["question_type"],
        "confidence": conf,
        "classification_reason": reason,
        "context": f"{context_header}\n\n{result['context']}",
        "row_count": result["row_count"],
        "query_elapsed_seconds": result["query_elapsed_seconds"],
        "token_estimate": result["token_estimate"],
        "source": "POSTGRES",
    }


def build_postgres_context_for_general(
    site_id: int,
    upload_id: Optional[int] = None,
    *,
    confidence: str = "high",
    classification_reason: str = "",
) -> Dict[str, Any]:
    """
    Build a composite context for general/unclassified questions.
    Combines: device summary + status counts + optic summary.
    Uses direct cutsheet_connections queries so upload_id scoping is respected.
    """
    import psycopg2.extras

    # B9: Default to latest active upload to avoid double-counting
    if upload_id is None:
        with managed_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM cutsheet_uploads "
                    "WHERE site_id = %s AND is_active = TRUE "
                    "ORDER BY created_at DESC LIMIT 1",
                    (site_id,),
                )
                row = cur.fetchone()
                if row:
                    upload_id = row[0]

    t0 = time.monotonic()
    uid = upload_id
    try:
        with managed_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Device summary — top 20 by connection count
                cur.execute(
                    """
                    SELECT device_name, COUNT(*) AS connections
                    FROM (
                        SELECT a_device AS device_name FROM cutsheet_connections
                        WHERE site_id = %s
                          AND (%s::bigint IS NULL OR upload_id = %s)
                          AND a_device IS NOT NULL AND a_device != '' AND a_device != 'nan'
                        UNION ALL
                        SELECT z_device FROM cutsheet_connections
                        WHERE site_id = %s
                          AND (%s::bigint IS NULL OR upload_id = %s)
                          AND z_device IS NOT NULL AND z_device != '' AND z_device != 'nan'
                    ) sub
                    GROUP BY device_name ORDER BY connections DESC LIMIT 20
                    """,
                    (site_id, uid, uid, site_id, uid, uid),
                )
                devices = [dict(r) for r in cur.fetchall()]

                # Status counts
                cur.execute(
                    "SELECT status, COUNT(*) AS cnt FROM cutsheet_connections "
                    "WHERE site_id = %s AND (%s::bigint IS NULL OR upload_id = %s) "
                    "GROUP BY status ORDER BY cnt DESC",
                    (site_id, uid, uid),
                )
                statuses = [dict(r) for r in cur.fetchall()]

                # Optic summary — COALESCE avoids double-counting cables with same optic on both sides
                cur.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')) AS optic_type,
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE status_normalized IN
                            ('lldp_passed', 'human_verified', 'complete')) AS in_service,
                        COUNT(*) FILTER (WHERE status_normalized = 'lldp_failed') AS failed
                    FROM cutsheet_connections
                    WHERE site_id = %s
                      AND (%s::bigint IS NULL OR upload_id = %s)
                      AND COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')) IS NOT NULL
                      AND COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, '')) != 'nan'
                    GROUP BY COALESCE(NULLIF(a_optic, ''), NULLIF(z_optic, ''))
                    ORDER BY total DESC LIMIT 15
                    """,
                    (site_id, uid, uid),
                )
                optics = [dict(r) for r in cur.fetchall()]

        elapsed = round(time.time() - t0, 4)

        # Format as compact text
        site_info = get_site_info(site_id)
        site_code = site_info["site_code"] if site_info else "UNKNOWN"
        conf_tag = f" [confidence: {confidence}]" if confidence != "high" else ""
        lines = [f"Site: {site_code} | Query type: general{conf_tag}", "", "Composite overview"]

        lines.append("\nDevices (top 20):")
        for d in devices:
            lines.append(f"  {d['device_name']}: {d['connections']} connections")

        lines.append("\nStatus breakdown:")
        for s in statuses:
            lines.append(f"  {s['status']}: {s['cnt']}")

        lines.append("\nOptic summary:")
        for o in optics:
            lines.append(
                f"  {o['optic_type']}: {o['total']} total, "
                f"{o['in_service']} in-service, {o['failed']} failed"
            )

        context_text = "\n".join(lines)

        return {
            "site_code": site_code,
            "site_id": site_id,
            "question_type": "general",
            "confidence": confidence,
            "classification_reason": classification_reason,
            "context": context_text,
            "row_count": len(devices) + len(statuses) + len(optics),
            "query_elapsed_seconds": elapsed,
            "token_estimate": len(context_text.split()),
            "source": "POSTGRES",
        }
    except Exception as exc:
        log.exception("Composite context build failed")
        return {
            "error": str(exc),
            "question_type": "general",
            "confidence": confidence,
            "classification_reason": classification_reason,
        }
