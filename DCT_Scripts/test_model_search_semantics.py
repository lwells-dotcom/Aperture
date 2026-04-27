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

from atlas_query_router import _model_search_mode, build_query_params, format_results_for_llm  # noqa: E402
from atlas_query_router import classify_question  # noqa: E402


class ModelSearchSemanticsTests(unittest.TestCase):
    def test_unique_model_question_routes_to_model_search(self):
        self.assertEqual(
            classify_question("How many unique SN2201s appear in the cutsheet?"),
            "model_search",
        )

    def test_model_search_mode_distinguishes_raw_and_unique_counts(self):
        self.assertEqual(
            _model_search_mode("How many SN2201s appear in the cutsheet?"),
            "raw_count",
        )
        self.assertEqual(
            _model_search_mode("How many unique SN2201s appear in the cutsheet?"),
            "unique_count",
        )
        self.assertEqual(
            _model_search_mode("List SN2201 devices in the cutsheet."),
            "list",
        )
        self.assertEqual(
            _model_search_mode("How many SN5610s are in service?", has_status_filter=True),
            "status_count",
        )

    def test_model_search_build_params_add_status_filter_for_in_service_queries(self):
        params = build_query_params("How many SN5610s are in service?", "model_search", 1)
        self.assertEqual(params["model_pattern"], "%SN5610%")
        self.assertEqual(params["model_status_filters"], ["lldp_passed", "human_verified", "complete"])
        self.assertEqual(params["model_status_label"], "In service")
        self.assertEqual(params["model_search_mode"], "status_count")

    def test_format_model_search_raw_count_summary(self):
        text = format_results_for_llm(
            "model_search",
            [{
                "cutsheet_occurrences": 25048,
                "a_side_occurrences": 21691,
                "z_side_occurrences": 3357,
                "cutsheet_unique_devices": 842,
            }],
            "How many SN2201s appear in the cutsheet?",
        )
        self.assertIn("Total cutsheet appearances matching pattern: 25048", text)
        self.assertIn("A-side appearances: 21691", text)
        self.assertIn("Unique devices represented in cutsheet: 842", text)

    def test_format_model_search_unique_count_summary(self):
        text = format_results_for_llm(
            "model_search",
            [{
                "total_unique_devices": 847,
                "cutsheet_unique_devices": 842,
                "inventory_unique_devices": 5,
            }],
            "How many unique SN2201s appear in the cutsheet?",
        )
        self.assertIn("Total unique devices matching pattern: 847", text)
        self.assertIn("In cutsheet connections: 842 device(s)", text)
        self.assertIn("In host inventory only: 5 device(s)", text)

    def test_format_model_search_status_count_summary(self):
        text = format_results_for_llm(
            "model_search",
            [{
                "matching_device_locations": 1592,
                "matching_device_names": 1592,
                "matching_cutsheet_rows": 34468,
                "a_side_rows": 17234,
                "z_side_rows": 17234,
            }],
            "How many SN5610s are in service?",
        )
        self.assertIn("Status filter: In service", text)
        self.assertIn("Unique device locations matching pattern: 1592", text)
        self.assertIn("Matching cutsheet rows: 34468", text)

    def test_format_model_search_list_uses_total_unique_instead_of_limit(self):
        text = format_results_for_llm(
            "model_search",
            [
                {
                    "device_name": "sn5610-a",
                    "model": "SN5610",
                    "connections": 64,
                    "total_unique": 1592,
                },
                {
                    "device_name": "sn5610-b",
                    "model": "SN5610",
                    "connections": 64,
                    "total_unique": 1592,
                },
            ] + [
                {
                    "device_name": f"sn5610-{i}",
                    "model": "SN5610",
                    "connections": 10,
                    "total_unique": 1592,
                }
                for i in range(2, 200)
            ],
            "List SN5610 devices in service.",
        )
        self.assertIn("Total distinct devices matching pattern: 1592", text)
        self.assertIn("showing top 200 by matching row count", text)


    def test_model_with_data_hall_routes_to_model_search_with_filter(self):
        # Classifier must return model_search, not location_lookup
        self.assertEqual(classify_question("how many SN5610s in dh202"), "model_search")

        # build_query_params must populate data_hall_filter with 'dh202:%' pattern
        params = build_query_params("how many SN5610s in dh202", "model_search", 1)
        self.assertEqual(params["data_hall_filter"], "dh202:%")
        self.assertIn("SN5610", params["model_pattern"])

        # Without a data hall the filter must be empty (no scoping)
        params_no_hall = build_query_params("how many SN5610s total", "model_search", 1)
        self.assertEqual(params_no_hall["data_hall_filter"], "")

        # Alternate phrasing — still model_search
        self.assertEqual(
            classify_question("List SN5610 devices in data hall dh202"),
            "model_search",
        )


if __name__ == "__main__":
    unittest.main()
