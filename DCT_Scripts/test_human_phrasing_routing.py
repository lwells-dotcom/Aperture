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

from atlas_query_router import build_query_params, route_question  # noqa: E402
from atlas_query_router import classify_question  # noqa: E402
from query_extractors import extract_model  # noqa: E402


class HumanPhrasingRoutingTests(unittest.TestCase):
    def test_plural_model_names_are_singularized(self):
        self.assertEqual(extract_model("How many SN2201s appear in the cutsheet?"), "SN2201")
        self.assertEqual(extract_model("How many SN5610s are there?"), "SN5610")
        self.assertEqual(extract_model("How many 7750-SR-1SEs are in the cutsheet?"), "7750-SR-1SE")
        self.assertEqual(
            extract_model("Are there any PROLIANT-DL360-GEN10-PLUS or CPU-HPE-01 devices?"),
            "DL360-GEN10-PLUS",
        )

        params = build_query_params("How many SN2201s appear in the cutsheet?", "model_search", 1)
        self.assertEqual(params["model_pattern"], "%SN2201%")
        params = build_query_params("How many 7750-SR-1SEs are in the cutsheet?", "model_search", 1)
        self.assertEqual(params["model_pattern"], "%7750-SR-1SE%")

    def test_verification_terms_win_even_with_cable_nouns(self):
        self.assertEqual(classify_question("How many human verified cables are there?"), "connection_status")
        self.assertEqual(classify_question("How many verified cables are there?"), "connection_status")
        self.assertEqual(classify_question("How many LLDP passed cables are there?"), "connection_status")

    def test_bare_rack_number_returns_actionable_location_message(self):
        result = route_question("What devices are in rack 41?", site_id=1)
        self.assertEqual(result["question_type"], "location_lookup")
        self.assertEqual(result["confidence"], "low")
        self.assertIn("too broad by itself", result["context"])
        self.assertIn("dh202:041", result["context"])

    def test_data_hall_prefix_is_not_misclassified_as_model(self):
        self.assertEqual(classify_question("What devices are in dh202?"), "location_lookup")


if __name__ == "__main__":
    unittest.main()
