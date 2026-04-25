#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "Optic_Count"))

import Define_Optic_Count as d  # noqa: E402


CSV_TEXT = """A-SIDE LOCODE,Z-SIDE LOCODE,A-BREAKOUT LOC:CAB:RU,Z-BREAKOUT LOC:CAB:RU,A-OPTIC,Z-OPTIC,A-LOC:CAB:RU,Z-LOC:CAB:RU,A-MODEL,Z-MODEL,STATUS,A-PORT,Z-PORT
sw1,sw2,nan,nan,QSFP28-100G-DR1,QSFP28-100G-DR1,dh202:041:01,dh202:041:02,SN5610,SN5610,Cable Is Ran: Complete,Eth1/1,Eth1/1
sw3,sw4,nan,nan,QSFP28-100G-DR1,QSFP28-100G-DR1,dh202:041:03,dh202:041:04,SN2201,SN2201,Human Verified,Eth1/2,Eth1/2
sw5,sw6,nan,nan,QSFP28-100G-DR1,QSFP28-100G-DR1,dh202:041:05,dh202:041:06,SN3700,SN3700,Cable Not Run,Eth1/3,Eth1/3
"""


class DefineOpticCountInServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        self.tmp.write(CSV_TEXT)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_in_service_helper_accepts_complete_and_human_verified(self):
        self.assertTrue(d._is_in_service_status("Cable Is Ran: Complete"))
        self.assertTrue(d._is_in_service_status("Human Verified"))
        self.assertTrue(d._is_in_service_status("LLDP Passed"))
        self.assertFalse(d._is_in_service_status("Cable Not Run"))

    def test_count_cutsheet_by_status_uses_broader_in_service_family(self):
        in_service, not_in_service = d.count_cutsheet(self.path, sort_by_status=True)
        self.assertEqual(sum(item.count for item in in_service), 4)
        self.assertEqual(sum(item.count for item in not_in_service), 2)

    def test_count_devices_cutsheet_by_status_uses_broader_in_service_family(self):
        in_service, not_in_service = d.count_devices_cutsheet(self.path, sort_by_status=True)
        self.assertEqual(sum(item.count for item in in_service), 4)
        self.assertEqual(sum(item.count for item in not_in_service), 2)


if __name__ == "__main__":
    unittest.main()
