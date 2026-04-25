#!/usr/bin/env python3
"""
Test harness: run 100 questions from test_questions.md through classify_question().
Reports classification + extracted params for each question.
"""
import os, sys, types

# Mock psycopg2 fully so we can import the router without a real Postgres driver
mock_pg = types.ModuleType("psycopg2")
mock_pg.connect = lambda *a, **k: None
mock_pg.extras = types.ModuleType("psycopg2.extras")
mock_pg.extras.RealDictCursor = object
mock_pg.pool = types.ModuleType("psycopg2.pool")
mock_pg.pool.ThreadedConnectionPool = type("ThreadedConnectionPool", (), {"__init__": lambda *a, **k: None})
mock_pg.sql = types.ModuleType("psycopg2.sql")
# TC3: B7 fix added references to these at module level in atlas_data_loader
mock_pg.OperationalError = type("OperationalError", (Exception,), {})
mock_pg.InterfaceError = type("InterfaceError", (Exception,), {})
sys.modules["psycopg2"] = mock_pg
sys.modules["psycopg2.extras"] = mock_pg.extras
sys.modules["psycopg2.pool"] = mock_pg.pool
sys.modules["psycopg2.sql"] = mock_pg.sql

# TC1: Use path relative to this script instead of a hardcoded session path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "Optic_Count"))

from atlas_query_router import classify_question
from query_extractors import (
    extract_device_name,
    extract_location,
    extract_model,
    extract_optic_type,
    extract_section_name,
)

QUESTIONS = [
    # --- Optic Inventory Counts (Q1-Q15) ---
    "How many QSFP28-100G-DR1-LOW-PWR optics are in the cutsheet?",
    "How many JNP-QSFP-100G-LR4-LU optics are deployed across all sections?",
    "What is the total count of QSFP28-100G-DR1 optics on the A-side?",
    "How many QSFPDD-400G-DR4 optics appear on the Z-side?",
    "How many QSFPDD-400G-LR4-LU optics are present across all sections?",
    "How many SFP-BASE-10G-LR optics are present across all connections?",
    "How many QSFP-100G-LR4-L optics appear in the Z-side column?",
    "What is the complete optic inventory breakdown showing count per optic type?",
    "Which optic type has the highest total count across both A and Z sides?",
    "Are there any connections where the A-side optic does not match the Z-side optic type?",
    # Q11-Q15
    "How many connections have a QSFPDD-400G-LR4-LU optic on the Z-side?",
    "How many connections list a MAM1Q00A-QSA28:10G-SFP-LR optic?",
    "What percentage of connections have optic data populated on both sides?",
    "How many connections show a dash as the optic value indicating no optic installed?",
    "Which sections contain the most QSFP28-100G-DR1-LOW-PWR optics?",
    # --- Cable Status and Completion (Q16-Q30) ---
    'How many cables have a status of "Cable Is Ran: Complete"?',
    "How many connections show a status of LLDP Passed?",
    "How many connections show a status of LLDP Failed?",
    "What percentage of all connections are marked complete or LLDP-verified?",
    "Which devices have the highest count of LLDP Failed connections?",
    "What is the ratio of LLDP Passed to LLDP Failed connections across the entire cutsheet?",
    "How many connections have been Human Verified?",
    "What is the overall cable completion rate for the site?",
    "Which section has the highest number of incomplete connections?",
    "Which section has the best completion percentage?",
    "How many connections are in a non-complete state across all sections?",
    "Are there any sections where zero cables are complete?",
    "What is the ratio of LLDP Passed to Cable Is Ran Complete connections?",
    "What is the total number of connections that need attention (not complete and not LLDP-verified)?",
    "Which sections have the highest concentration of LLDP Failed connections?",
    # --- Device Connectivity and Topology (Q31-Q45) ---
    "How many unique devices appear on the A-side of the cutsheet?",
    "How many unique devices appear on the Z-side of the cutsheet?",
    "What is the total number of unique physical devices across both sides?",
    "How many connections are listed in total in the cutsheet?",
    "How many topology sections are defined in the cutsheet?",
    "What network tiers are represented in the cutsheet (Tier-0 through Tier-4)?",
    "How many TIER-3 TO TIER-2 connections are in the cutsheet?",
    "How many TIER-2 TO TIER-1 connections exist?",
    "How many TIER-1 TO TIER-0 connections are defined?",
    "What is the connection count for the OOB-FW section?",
    "How many connections are in the BACKBONE MGMT section?",
    "What sections make up the management plane (OOB, MGMT, BACKBONE)?",
    "How many GG1-A connections are defined (all GG1-A sections combined)?",
    "How many GG1-B connections are defined across all GG1-B sections?",
    "How many GG1-C connections are defined?",
    # --- Device Model Queries (Q46-Q60) ---
    "How many CPU-GP2-02 devices appear in the cutsheet?",
    "How many DF-3060 devices are present?",
    "How many SN4700 switches are in the dataset?",
    "How many SN3700 switches appear?",
    "How many SN3420 switches are listed?",
    "How many SN2201 switches appear in the cutsheet?",
    "How many 7750-SR-1SE routers are in the cutsheet?",
    "How many PA-1420 firewalls are present?",
    "How many CM8148 devices appear?",
    "How many OM2216-C14 devices are listed?",
    "How many NET-6X100G-01 devices appear in the cutsheet?",
    "How many 1U-1N-GEN5-1NIC devices appear?",
    "What is the complete device model inventory sorted by count?",
    "Which device model has the most connections in the dataset?",
    "Are there any PROLIANT-DL360-GEN10-PLUS or CPU-HPE-01 devices and how many connections do they have?",
    # --- LLDP and Link Verification (Q61-Q70) ---
    "How many connections have the exact status LLDP Passed?",
    "How many connections have the exact status LLDP Failed?",
    "Which devices have the most LLDP Failed connections?",
    "Are there any connections in the burndown sheet with link status down?",
    "Which sections have the highest concentration of LLDP Failed statuses?",
    "What is the ratio of LLDP Passed to LLDP Failed connections expressed as a percentage?",
    "Are there any connections where the current LLDP neighbor does not match the expected Z-side device?",
    "Which ports have been human verified versus LLDP verified?",
    "How many connections are pending LLDP verification?",
    "What is the total count of connections with any verified status (LLDP Passed or Human Verified)?",
    # --- Location and Rack Queries (Q71-Q80) ---
    "What devices are installed in the NET-AGG section?",
    "What devices appear in the COMP-AGG section?",
    "How many racks are represented in the cutsheet across all data halls?",
    "What connections are associated with the GG1-A section?",
    "What connections are associated with the NET-DIST section?",
    "How many unique LOC:CAB:RU locations appear on the A-side?",
    "Which rack has the highest number of connections?",
    "What is the locode associated with the OOB firewall connections?",
    "How many distinct data halls are represented in the cutsheet?",
    "What devices are listed as FDP (fiber distribution panel) on the Z-side?",
    # --- Cross-Section and Capacity Planning (Q81-Q90) ---
    "What is the connection count for the TIER-4 TO TIER-3 section?",
    "What is the connection count for the TIER-3 TO TIER-2 section?",
    "What is the connection count for the TIER-2 TO TIER-1 section?",
    "How many connections are in the GG1-A sections combined?",
    "How many connections are in the GG1-B sections combined?",
    "How many connections are in the GG1-C sections combined?",
    "What is the total number of storage-related connections (STOR sections)?",
    "How many ROCE infrastructure connections are defined?",
    "What is the infrastructure distribution (INFRA-DIST) connection count?",
    "How many POD-DIST-B connections are present across all four POD-DIST-B sections?",
    # --- Risk and Anomaly Queries (Q91-Q100) ---
    "Are there any Z-MODEL values where the same device is recorded with inconsistent casing (e.g., sn3700 versus SN3700) and which rows are affected?",
    "Are there connections with breakout cables where the optic count might be double-counted?",
    "Which sections have the most connections still in a non-LLDP-verified state indicating installation risk?",
    "How many connections have a LLDP Failed status and which devices are most frequently involved?",
    "Are there any connections where both A and Z optic fields are empty?",
    "Which device models appear on the Z-side with inconsistent naming (e.g., NOKIA-7750-SR-1se versus 7750-SR-1SE)?",
    "How many connections have a cable type of LC-TO-LC SMF versus other cable types?",
    "Are there any connections in the TIER-1 TO TIER-0 sections that are not yet complete?",
    "What is the total count of connections across both the NET-AGG and COMP-AGG sections?",
    "If all currently unverified connections were resolved, what would the final verified connection count be and what is the remaining gap?",
]

# Expected ideal classification for each question
EXPECTED = [
    # Q1-Q15 Optic Inventory
    "optic_count", "optic_count", "optic_count", "optic_count", "optic_count",
    "optic_count", "optic_count", "optic_count", "optic_count", "optic_count",
    "optic_count", "optic_count", "optic_count", "optic_count", "optic_count",
    # Q16-Q30 Cable Status
    "cable_status", "connection_status", "lldp_failures", "connection_status", "lldp_failures",
    "connection_status", "connection_status", "cable_status", "section_completion", "section_completion",
    "connection_status", "section_completion", "connection_status", "connection_status", "lldp_failures",
    # Q31-Q45 Device Connectivity
    "a_device_list", "z_device_list", "device_list", "site_overview", "section_summary",
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    # Q46-Q60 Device Models
    "model_search", "model_search", "model_search", "model_search", "model_search",
    "model_search", "model_search", "model_search", "model_search", "model_search",
    "model_search", "model_search", "model_search", "model_search", "model_search",
    # Q61-Q70 LLDP
    "connection_status", "lldp_failures", "lldp_failures", "link_status", "lldp_failures",
    "connection_status", "lldp_neighbor_mismatch", "connection_status", "connection_status", "connection_status",
    # Q71-Q80 Location/Rack
    "section_summary", "section_summary", "rack_summary", "section_summary", "section_summary",
    "rack_summary", "rack_summary", "section_summary", "data_hall_summary", "role_lookup",
    # Q81-Q90 Cross-Section
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    # Q91-Q100 Risk/Anomaly
    "model_search", "optic_count", "connection_status", "lldp_failures", "optic_count",
    "model_search", "optic_count", "section_completion", "section_summary", "connection_status",
]


def run_block(start, end):
    """Run a block of questions and report results."""
    print(f"\n{'='*80}")
    print(f"  BLOCK Q{start+1}-Q{end}  ")
    print(f"{'='*80}")

    passed = 0
    failed = 0
    general_fallbacks = 0

    for i in range(start, end):
        q = QUESTIONS[i]
        qtype = classify_question(q)
        expected = EXPECTED[i]

        # Extract relevant params
        params = {}
        if qtype in ("optic_count",):
            ot = extract_optic_type(q)
            if ot: params["optic"] = ot
        if qtype in ("model_search",):
            model = extract_model(q)
            if model: params["model"] = model
        if qtype in ("section_summary", "section_completion"):
            sn = extract_section_name(q)
            if sn: params["section"] = sn
        if qtype in ("device_detail", "device_connections"):
            dn = extract_device_name(q)
            if dn: params["device"] = dn
        if qtype in ("rack_summary", "location_lookup"):
            loc = extract_location(q)
            if loc: params["location"] = loc

        match = "OK" if qtype == expected else "MISS"
        if qtype == "general":
            general_fallbacks += 1
            match = "GENERAL"

        if match == "OK":
            passed += 1
        else:
            failed += 1

        param_str = f"  params={params}" if params else ""
        flag = "" if match == "OK" else " <<<"
        print(f"  Q{i+1:3d} [{match:7s}] got={qtype:25s} exp={expected:25s}{param_str}{flag}")

    print(f"\n  Block result: {passed} passed, {failed} missed, {general_fallbacks} fell to general")
    return passed, failed, general_fallbacks


def main():
    assert len(QUESTIONS) == 100, f"Expected 100 questions, got {len(QUESTIONS)}"
    assert len(EXPECTED) == 100, f"Expected 100 expectations, got {len(EXPECTED)}"

    total_pass = 0
    total_fail = 0
    total_general = 0

    for block_start in range(0, 100, 10):
        p, f, g = run_block(block_start, block_start + 10)
        total_pass += p
        total_fail += f
        total_general += g

    print(f"\n{'='*80}")
    print(f"  FINAL: {total_pass}/100 correct, {total_fail} misclassified, {total_general} fell to general")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
