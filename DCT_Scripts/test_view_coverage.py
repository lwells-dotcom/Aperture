#!/usr/bin/env python3
"""
H3 coverage analysis: categorize all 100 test questions by whether they can be
served by materialized views vs base table queries.

Materialized views available:
  optic_inventory_by_side   - optic counts by site/optic_type/side
  optic_inventory_combined  - deduplicated cable counts by site/optic_type  (R21 fix)
  cable_status_summary      - status counts by site/section/status/status_normalized
  device_summary            - device name/model/connection_count/port_count by site

Coverage labels:
  VIEW     - question type can be fully served by a materialized view
  PARTIAL  - view has the primary data but is missing one dimension
             (e.g. section_summary: cable_status_summary has section+cnt but not a/z device counts)
  BASE     - requires querying base tables (row-level detail, side-specific lists, burndown, etc.)

Run: python test_view_coverage.py
"""
import os
import sys
import types

# ── Mock psycopg2 so we can import the router without a live Postgres driver ──
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


# ---------------------------------------------------------------------------
# View coverage map
# None   = BASE  (must query cutsheet_connections or burndown_connections directly)
# tuple  = VIEW  (all needed data is in one of these materialized views)
# "partial" = view has primary data but missing a dimension
# ---------------------------------------------------------------------------

VIEW_MAP = {
    # optic_inventory_combined: cable_count, a_count, z_count, in_service, failed, pending
    "optic_count":            ("optic_inventory_combined", "optic_inventory_by_side"),

    # cable_status_summary: site_id, section, status, status_normalized, cnt
    "cable_status":           ("cable_status_summary",),
    "connection_status":      ("cable_status_summary",),
    "section_completion":     ("cable_status_summary",),

    # section_summary needs a_device + z_device counts; cable_status_summary has cnt per
    # section but not device counts — partial coverage only
    "section_summary":        "partial:cable_status_summary",

    # device_summary: site_id, device_name, model, connection_count, port_count
    "device_list":            ("device_summary",),
    "model_search":           ("device_summary",),

    # No per-side device view exists (device_summary flattens A+Z into one row)
    "a_device_list":          None,
    "z_device_list":          None,

    # Composite: would need device_summary + cable_status_summary + optic_inventory_combined
    "site_overview":          None,

    # No locode dimension in any view
    "data_hall_summary":      None,

    # No rack / loc_cab_ru dimension in any view
    "rack_summary":           None,

    # Needs row-level detail with device names — views only have counts
    "lldp_failures":          None,

    # burndown_connections — separate table, no view
    "link_status":            None,
    "lldp_neighbor_mismatch": None,

    # Row-level connection detail
    "device_connections":     None,
    "device_detail":          None,

    # raw_row JSONB lookup
    "ip_lookup":              None,

    # Pattern match on device name
    "node_compute":           None,

    # Specific rack lookup
    "location_lookup":        None,

    # Composite: no single view covers this
    "general":                None,
}

QUESTIONS = [
    # Q1-Q15 Optic Inventory
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
    "How many connections have a QSFPDD-400G-LR4-LU optic on the Z-side?",
    "How many connections list a MAM1Q00A-QSA28:10G-SFP-LR optic?",
    "What percentage of connections have optic data populated on both sides?",
    "How many connections show a dash as the optic value indicating no optic installed?",
    "Which sections contain the most QSFP28-100G-DR1-LOW-PWR optics?",
    # Q16-Q30 Cable Status
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
    # Q31-Q45 Device Connectivity
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
    # Q46-Q60 Device Models
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
    # Q61-Q70 LLDP
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
    # Q71-Q80 Location/Rack
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
    # Q81-Q90 Cross-Section
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
    # Q91-Q100 Risk/Anomaly
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

EXPECTED = [
    # Q1-Q15
    "optic_count", "optic_count", "optic_count", "optic_count", "optic_count",
    "optic_count", "optic_count", "optic_count", "optic_count", "optic_count",
    "optic_count", "optic_count", "optic_count", "optic_count", "optic_count",
    # Q16-Q30
    "cable_status", "connection_status", "lldp_failures", "connection_status", "lldp_failures",
    "connection_status", "connection_status", "cable_status", "section_completion", "section_completion",
    "connection_status", "section_completion", "connection_status", "connection_status", "lldp_failures",
    # Q31-Q45
    "a_device_list", "z_device_list", "device_list", "site_overview", "section_summary",
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    # Q46-Q60
    "model_search", "model_search", "model_search", "model_search", "model_search",
    "model_search", "model_search", "model_search", "model_search", "model_search",
    "model_search", "model_search", "model_search", "model_search", "model_search",
    # Q61-Q70
    "connection_status", "lldp_failures", "lldp_failures", "link_status", "lldp_failures",
    "connection_status", "lldp_neighbor_mismatch", "connection_status", "connection_status", "connection_status",
    # Q71-Q80
    "section_summary", "section_summary", "rack_summary", "section_summary", "section_summary",
    "rack_summary", "rack_summary", "section_summary", "data_hall_summary", "z_device_list",
    # Q81-Q90
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    "section_summary", "section_summary", "section_summary", "section_summary", "section_summary",
    # Q91-Q100
    "model_search", "optic_count", "connection_status", "lldp_failures", "optic_count",
    "model_search", "optic_count", "section_summary", "section_summary", "connection_status",
]

assert len(QUESTIONS) == 100
assert len(EXPECTED) == 100


BLOCK_LABELS = [
    "Optic Inventory (Q1-Q15)",
    "Cable Status (Q16-Q30)",
    "Device Topology (Q31-Q45)",
    "Device Models (Q46-Q60)",
    "LLDP/Links (Q61-Q70)",
    "Location/Rack (Q71-Q80)",
    "Cross-Section (Q81-Q90)",
    "Risk/Anomaly (Q91-Q100)",
]


def coverage_label(qtype: str) -> str:
    v = VIEW_MAP.get(qtype)
    if v is None:
        return "BASE"
    if isinstance(v, str) and v.startswith("partial:"):
        return f"PARTIAL ({v.split(':',1)[1]})"
    return f"VIEW ({', '.join(v)})"


def main():
    view_q = []
    partial_q = []
    base_q = []

    block_stats = []

    for block_idx, label in enumerate(BLOCK_LABELS):
        start = block_idx * (15 if block_idx < 2 else (10 if block_idx < 4 else 10))
        # recalculate proper block boundaries
        if block_idx == 0:
            start, end = 0, 15
        elif block_idx == 1:
            start, end = 15, 30
        elif block_idx == 2:
            start, end = 30, 45
        elif block_idx == 3:
            start, end = 45, 60
        elif block_idx == 4:
            start, end = 60, 70
        elif block_idx == 5:
            start, end = 70, 80
        elif block_idx == 6:
            start, end = 80, 90
        else:
            start, end = 90, 100

        b_view = b_partial = b_base = 0

        print(f"\n{'='*80}")
        print(f"  {label}")
        print(f"{'='*80}")

        for i in range(start, end):
            q = QUESTIONS[i]
            actual = classify_question(q)
            exp = EXPECTED[i]
            classify_ok = "OK" if actual == exp else "MISS"
            cov = coverage_label(actual)

            if cov.startswith("VIEW"):
                b_view += 1
                view_q.append(i + 1)
            elif cov.startswith("PARTIAL"):
                b_partial += 1
                partial_q.append(i + 1)
            else:
                b_base += 1
                base_q.append(i + 1)

            miss_flag = " <<<" if classify_ok != "OK" else ""
            print(
                f"  Q{i+1:3d} [{classify_ok}] {actual:25s}  {cov}{miss_flag}"
            )

        block_stats.append((label, b_view, b_partial, b_base, end - start))
        print(
            f"\n  Block: {b_view} VIEW, {b_partial} PARTIAL, {b_base} BASE"
            f"  ({round(100*(b_view+b_partial)/(end-start))}% view-eligible)"
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("  H3 COVERAGE SUMMARY")
    print(f"{'='*80}")
    print(f"  VIEW    (fully served by matview) : {len(view_q):3d}/100 questions")
    print(f"  PARTIAL (view covers primary data): {len(partial_q):3d}/100 questions")
    print(f"  BASE    (requires base tables)    : {len(base_q):3d}/100 questions")
    print(
        f"\n  View-eligible (VIEW + PARTIAL)    : {len(view_q)+len(partial_q)}/100 "
        f"({round(100*(len(view_q)+len(partial_q))/100)}%)"
    )
    print(f"\n  {'Block':<35} {'VIEW':>5} {'PARTIAL':>8} {'BASE':>5} {'Total':>6}")
    print(f"  {'-'*60}")
    for label, bv, bp, bb, total in block_stats:
        print(f"  {label:<35} {bv:>5} {bp:>8} {bb:>5} {total:>6}")

    print(f"\n  Questions requiring base tables ({len(base_q)}):")
    # Group by type
    by_type: dict = {}
    for i in range(100):
        actual = classify_question(QUESTIONS[i])
        cov = coverage_label(actual)
        if cov.startswith("BASE"):
            by_type.setdefault(actual, []).append(i + 1)
    for qtype, qnums in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"    {qtype:30s}: {len(qnums)} questions  {qnums}")

    print(f"\n  Biggest view-coverage gap: section_summary ({len(by_type.get('section_summary', []))} questions)")
    print(
        "  If cable_status_summary were extended with a_devices/z_devices,\n"
        "  section_summary could move from BASE to VIEW — adding ~25 more view-eligible questions."
    )
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
