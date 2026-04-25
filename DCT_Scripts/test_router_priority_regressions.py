#!/usr/bin/env python3
import os
import sys
import types
import unittest

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

from atlas_query_router import classify_question  # noqa: E402


class RouterPriorityRegressionTests(unittest.TestCase):
    def test_cross_site_words_do_not_override_single_site_optic_or_status_queries(self):
        self.assertEqual(
            classify_question("How many SFP-BASE-10G-LR optics are present across all connections?"),
            "optic_count",
        )
        self.assertEqual(
            classify_question("What is the ratio of LLDP Passed to LLDP Failed connections across the entire cutsheet?"),
            "connection_status",
        )
        self.assertEqual(
            classify_question("How many unique physical devices across both sides?"),
            "device_list",
        )

    def test_status_router_defers_to_more_specific_section_model_and_site_queries(self):
        self.assertEqual(
            classify_question("How many TIER-3 TO TIER-2 connections are in the cutsheet?"),
            "section_summary",
        )
        self.assertEqual(
            classify_question("Which section has the highest number of incomplete connections?"),
            "section_completion",
        )
        self.assertEqual(
            classify_question("What is the complete device model inventory sorted by count?"),
            "model_search",
        )
        self.assertEqual(
            classify_question("How many connections are listed in total in the cutsheet?"),
            "site_overview",
        )
        self.assertEqual(
            classify_question("Are there any PROLIANT-DL360-GEN10-PLUS or CPU-HPE-01 devices and how many connections do they have?"),
            "model_search",
        )
        self.assertEqual(
            classify_question("What is the total number of connections that need attention (not complete and not LLDP-verified)?"),
            "connection_status",
        )

    def test_installed_in_section_phrase_stays_section_scoped(self):
        self.assertEqual(
            classify_question("What devices are installed in the NET-AGG section?"),
            "section_summary",
        )


if __name__ == "__main__":
    unittest.main()
