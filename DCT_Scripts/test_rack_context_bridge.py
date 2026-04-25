#!/usr/bin/env python3
import os
import sys
import types
import unittest


define_optic_count = types.ModuleType("Define_Optic_Count")
define_optic_count.clear_excel_cache = lambda: None
source_count_netbox = types.ModuleType("Source_count_Netbox")
demo_auth_ai = types.ModuleType("demo_auth_ai")
build_sheet_processor = types.ModuleType("build_sheet_processor")

sys.modules["Define_Optic_Count"] = define_optic_count
sys.modules["Source_count_Netbox"] = source_count_netbox
sys.modules["demo_auth_ai"] = demo_auth_ai
sys.modules["build_sheet_processor"] = build_sheet_processor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "Optic_Count"))

from atlas_web_app import _build_rack_context_for_llm, _question_matches_rack_result  # noqa: E402


class RackContextBridgeTests(unittest.TestCase):
    def setUp(self):
        self.rack_result = {
            "room": "DH2",
            "rack": "041",
            "cab_type": "TYPE-A",
            "total_cables": 59,
            "internal_count": 22,
            "cab_to_cab_count": 37,
            "devices": [
                {
                    "ru": "33",
                    "location": "dh202:041:33",
                    "dns_name": "rack-041-sw1",
                    "model": "SN2201",
                    "status": "Installed",
                },
                {
                    "ru": "28",
                    "location": "dh202:041:28",
                    "dns_name": "rack-041-sw2",
                    "model": "SN3700",
                    "status": "Installed",
                },
            ],
            "optic_summary": {"LC-TO-LC SMF": 12, "MPO8-SMF": 4},
            "internal_labels": [
                "dh202:041:28 port 1/1/c13 -> dh202:041:18 port swp25",
            ],
            "cab_to_cab_labels": [
                "dh202:041:28 port 1/1/c1 -> dh202:043:20 port swp27",
            ],
        }

    def test_question_matching_handles_human_rack_phrasing(self):
        self.assertTrue(_question_matches_rack_result("look at dh2 rack 041", self.rack_result))
        self.assertTrue(_question_matches_rack_result("what devices are in rack 41 in dh2?", self.rack_result))
        self.assertFalse(_question_matches_rack_result("what devices are in dh2 rack 042?", self.rack_result))

    def test_rack_context_builder_formats_plain_text_context(self):
        payload = _build_rack_context_for_llm(self.rack_result)
        self.assertEqual(payload["source"], "RACK_ANALYZER")
        self.assertEqual(payload["location_key"], "dh2:041")
        self.assertIn("Rack Analyzer result for DH2 rack 041", payload["context"])
        self.assertIn("Devices Physically in Rack:", payload["context"])
        self.assertIn("Cables Leaving This Rack:", payload["context"])
        self.assertIn("dh202:041:33", payload["context"])
        self.assertGreater(payload["token_estimate"], 0)


if __name__ == "__main__":
    unittest.main()
