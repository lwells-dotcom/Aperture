#!/usr/bin/env python3
import os
import sys
import types
import unittest
from unittest.mock import patch

mock_pg = types.ModuleType("psycopg2")
mock_pg.connect = lambda *a, **k: None
mock_pg.extras = types.ModuleType("psycopg2.extras")
mock_pg.extras.RealDictCursor = object
mock_pg.pool = types.ModuleType("psycopg2.pool")
mock_pg.pool.ThreadedConnectionPool = type(
    "ThreadedConnectionPool", (), {"__init__": lambda *a, **k: None}
)
mock_pg.sql = types.ModuleType("psycopg2.sql")
mock_pg.OperationalError = type("OperationalError", (Exception,), {})
mock_pg.InterfaceError = type("InterfaceError", (Exception,), {})
sys.modules["psycopg2"] = mock_pg
sys.modules["psycopg2.extras"] = mock_pg.extras
sys.modules["psycopg2.pool"] = mock_pg.pool
sys.modules["psycopg2.sql"] = mock_pg.sql

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "Optic_Count"))

from atlas_postgres_context import build_postgres_context  # noqa: E402
from atlas_query_router import _SQL_TEMPLATES, route_question  # noqa: E402


class QueryFollowupTests(unittest.TestCase):
    def test_upload_diff_requires_two_ids_even_if_one_is_present(self):
        result = route_question("compare upload 5", site_id=1)
        self.assertEqual(result["question_type"], "upload_diff")
        self.assertEqual(result["confidence"], "low")
        self.assertIn("Please specify two upload IDs to compare", result["context"])
        self.assertIn("Use 'list uploads' or 'show upload history'", result["context"])

    def test_general_context_passes_confidence_to_composite_builder(self):
        fake_result = {
            "ok": True,
            "question_type": "general",
            "confidence": "low",
            "reason": "fallback to general",
            "context": "ignored",
            "row_count": 0,
            "query_elapsed_seconds": 0.0,
            "token_estimate": 0,
        }
        with patch("atlas_query_router.route_question", return_value=fake_result):
            with patch("atlas_postgres_context.build_postgres_context_for_general", return_value={"ok": True}) as mock_general:
                build_postgres_context("Tell me about the site", 1, upload_id=123)
        mock_general.assert_called_once_with(
            1,
            upload_id=123,
            confidence="low",
            classification_reason="fallback to general",
        )

    def test_connection_status_excludes_complete(self):
        self.assertNotIn("'complete'", _SQL_TEMPLATES["connection_status"])
        self.assertIn("'complete'", _SQL_TEMPLATES["cable_status"])


if __name__ == "__main__":
    unittest.main()
