#!/usr/bin/env python3
"""
Regression tests for Rack Analyzer rack/device extraction.
"""
import os
import sys
import tempfile
import unittest

from openpyxl import Workbook

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "Optic_Count"))

from build_sheet_processor import process_rack  # noqa: E402


class BuildSheetProcessorTests(unittest.TestCase):
    def _make_workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "CUTSHEET"
        ws.append([
            "STATUS",
            "A-LOC:CAB:RU",
            "A-SIDE-DNS-NAME",
            "A-MODEL",
            "A-PORT",
            "A-OPTIC",
            "Z-LOC:CAB:RU",
            "Z-SIDE-DNS-NAME",
            "Z-MODEL",
            "Z-PORT",
            "Z-OPTIC",
            "CABLE",
        ])
        return wb

    def test_process_rack_includes_z_side_only_devices(self):
        wb = self._make_workbook()
        ws = wb["CUTSHEET"]
        ws.append([
            "LLDP:  Passed",
            "dh202:999:10",
            "agg-999",
            "SN5600",
            "1/1",
            "QSFP",
            "dh202:201:20",
            "rack-201-z",
            "SN4700",
            "1/2",
            "QSFP",
            "LC",
        ])

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            wb.save(tmp.name)
            path = tmp.name

        try:
            result = process_rack(path, None, "dh2", "201")
        finally:
            os.unlink(path)

        locations = {device["location"] for device in result["devices"]}
        self.assertIn("dh202:201:20", locations)

    def test_process_rack_supplements_devices_from_site_hosts(self):
        wb = self._make_workbook()
        hosts = wb.create_sheet("SITE-HOSTS")
        hosts.append([
            "STATUS",
            "LAST-PROVISIONED",
            "LOC:CAB:RU",
            "DNS-A-RECORD",
            "NETBOX MODEL",
            "SERIAL",
            "ROLE",
            "ROW:TYPE",
        ])
        hosts.append([
            "Active",
            "",
            "dh202:201:30",
            "rack-201-host",
            "GPU-NODE",
            "",
            "COMPUTE",
            "ROW",
        ])

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            wb.save(tmp.name)
            path = tmp.name

        try:
            result = process_rack(path, None, "dh2", "201")
        finally:
            os.unlink(path)

        by_loc = {device["location"]: device for device in result["devices"]}
        self.assertIn("dh202:201:30", by_loc)
        self.assertEqual(by_loc["dh202:201:30"]["dns_name"], "rack-201-host")
        self.assertEqual(by_loc["dh202:201:30"]["model"], "GPU-NODE")

    def test_process_rack_rejects_ambiguous_short_room(self):
        wb = self._make_workbook()
        ws = wb["CUTSHEET"]
        ws.append([
            "LLDP:  Passed",
            "dh201:201:10",
            "rack-a",
            "SN4700",
            "1/1",
            "QSFP",
            "dh201:999:10",
            "agg-a",
            "SN5600",
            "1/2",
            "QSFP",
            "LC",
        ])
        ws.append([
            "LLDP:  Passed",
            "dh203:201:10",
            "rack-b",
            "SN4700",
            "1/1",
            "QSFP",
            "dh203:999:10",
            "agg-b",
            "SN5600",
            "1/2",
            "QSFP",
            "LC",
        ])

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            wb.save(tmp.name)
            path = tmp.name

        try:
            with self.assertRaisesRegex(ValueError, "Ambiguous room 'dh2'"):
                process_rack(path, None, "dh2", "201")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
