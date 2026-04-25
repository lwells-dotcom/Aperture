# Atlas DCT Query Results
**Site:** ELD (US-CENTRAL-08A / US-LZL01 Ellendale)
**Source file:** `MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx`
**Loaded by:** demo_user — 2026-04-24
**Total rows:** 44,246 connections | 56,183 raw rows ingested
**Data halls:** dh202, dh204

---

## Sanity Checks

| Check | Result |
|---|---|
| Total rows | 44,246 ✓ |
| Has cable_id | 0 (reclassified to cable_type) |
| Has cable_type | 44,200 |
| Unique A-side devices | 1,626 |
| Unique Z-side devices | 13,838 |
| Total unique devices (both sides) | 13,839 |
| Unique sections | 71 |
| Unique A-side LOC:CAB:RU locations | 1,625 |
| Unique racks | 479 |
| Data halls | 2 (dh202, dh204) |

---

## Cable Type Breakdown

| Cable Type | Count | % |
|---|---|---|
| CAT6a | 24,801 | 56.1% |
| MPO12-SMF | 15,476 | 35.0% |
| LC-TO-LC SMF | 3,893 | 8.8% |
| MPO8-SMF | 24 | 0.1% |
| CAT6a + LC/LC FIBER | 6 | ~0% |

---

## Status Breakdown

| Status (normalized) | Count |
|---|---|
| not_run | 27,815 |
| complete | 12,035 |
| not_terminated | 2,486 |
| addition | 1,071 |
| unknown (blank raw status) | 838 |
| human_verified | 1 |

**Overall completion rate:** 12,036 / 44,246 = **27.20%**
**Connections needing attention:** 32,210

> Note: 838 rows have a blank raw `status` value — stored as `unknown` normalized. Consider defaulting these to `not_run`.

---

## Optic Inventory (Q1–Q15)

### Full Optic Inventory (Both Sides Combined)

| Optic Type | Total |
|---|---|
| OSFP-800G-2DR4 | 19,764 |
| QSFP112-400G-DR4 | 10,872 |
| QSFP28-100G-DR1 | 5,915 |
| OSFP-800G-2FR4 | 1,440 |
| QSFPDD-400G-DR4 | 566 |
| QSFP28-100G-DR1-LOW-PWR | 88 |
| SFP-BASE-10G-LR | 49 |
| SFP-10G-LR | 32 |
| QSFPDD-400G-LR4 | 28 |
| CAT6a to 1000BASE-LX/LH SFP via MEDIA CONVERTER CHASSIS | 12 |
| QSFPDD-400G-PLR4 | 8 |
| QSFP28-100G-LR1 | 4 |
| MAM1Q00A-QSA28:10G-SFP-LR | 4 |
| QSFP28-100G-LR4 | 4 |
| SFP-BASE-1G-LX | 1 |

### Specific Optic Queries

| Question | Answer |
|---|---|
| QSFP28-100G-DR1-LOW-PWR total | 88 (all Z-side) |
| JNP-QSFP-100G-LR4-LU | 0 — not present |
| QSFP28-100G-DR1 A-side | 2,904 |
| QSFPDD-400G-DR4 Z-side | 292 |
| QSFPDD-400G-LR4-LU | 0 — not present (use QSFPDD-400G-LR4) |
| SFP-BASE-10G-LR total | 49 |
| QSFP-100G-LR4-L Z-side | 0 — not present |
| Highest count optic type | OSFP-800G-2DR4 at 19,764 |
| A/Z optic mismatches | 11,284 (all intentional breakout pairs) |
| QSFPDD-400G-LR4-LU Z-side | 0 |
| MAM1Q00A-QSA28:10G-SFP-LR | 4 connections |
| Both sides populated | 19,376 (43.79%) |
| Dash as optic value | 0 (nulls/blanks used instead) |

### Pending Optic % (from optic_inventory_combined)

| Optic Type | Cable Count | Pending | % Pending |
|---|---|---|---|
| CAT6a to 1000BASE-LX/LH SFP via MEDIA CONVERTER CHASSIS | 6 | 6 | 100.0% |
| OSFP-800G-2FR4 | 720 | 576 | 80.0% |
| QSFPDD-400G-PLR4 | 8 | 6 | 75.0% |
| OSFP-800G-2DR4 | 15,428 | 9,356 | 60.6% |
| QSFPDD-400G-LR4 | 28 | 14 | 50.0% |
| QSFP28-100G-DR1 | 2,904 | 902 | 31.1% |
| QSFPDD-400G-DR4 | 274 | 7 | 2.6% |
| SFP-BASE-10G-LR | 27 | 0 | 0.0% |
| SFP-10G-LR | 16 | 0 | 0.0% |

### QSFP28-100G-DR1-LOW-PWR Sections

| Section | A-Count | Z-Count | Total |
|---|---|---|---|
| TIER-1 TO TIER-0 C3 | 0 | 64 | 64 |
| MGMT-CORE | 0 | 24 | 24 |

---

## Cable Status and Completion (Q16–Q30)

| Question | Answer |
|---|---|
| "Cable Is Ran: Complete" | 12,035 |
| LLDP Passed | 0 |
| LLDP Failed | 0 |
| % complete or LLDP-verified | 27.20% (12,036 of 44,246) |
| Devices with LLDP Failed | None — LLDP not run |
| LLDP Passed : LLDP Failed ratio | N/A |
| Human Verified | 1 |
| Overall completion rate | 27.20% |
| Non-complete connections | 32,210 |
| Connections needing attention | 32,210 |
| LLDP Passed : Cable Complete ratio | 0 : 12,035 |

### Most Incomplete Sections

| Section | Incomplete |
|---|---|
| TIER-1 TO TIER-0 D3 | 14,472 |
| TIER-1 TO TIER-0 C3 | 10,026 |
| INFRA-DIST-D | 2,401 |
| DH202 T1 ROCE MGMT | 1,056 |
| TIER-1 TO TIER-0 A4 (T0 x3) | 1,044 |
| TIER-2 TO TIER-1 D | 864 |
| TIER-3 TO TIER-2 1 D | 576 |

### 100% Complete Sections (sample)

- Compute Mgmt In-Row Grid A1/A2 racks (all)
- TIER-3 TO TIER-2 1 A & 1 B
- TIER-1 TO TIER-0 A1, A2, B1, C1, C2
- INFRA-DIST-B
- DH202 OVERFLOW T0 ROCE MGMT
- ROCE INFRA AGGS DH202

### Sections With Zero Completions (0% done)

- TIER-1 TO TIER-0 D3 (14,472)
- INFRA-DIST-D (2,401)
- TIER-2 TO TIER-1 D (864)
- TIER-3 TO TIER-2 1 D (576)
- TIER-1 TO TIER-0 D1, D2 (216 each)
- TIER-1 TO TIER-0 E1 (192)
- TIER-3 TO TIER-2 1 E (144)
- STOR INFRA-DIST A3 (130)
- DH204 OVERFLOW T0 ROCE MGMT (108)
- INFRA-DIST-E (62)
- TIER-1 TO TIER-0 A3 (54)
- TIER-2 TO TIER-1 GRID:E (36)
- Compute Mgmt In-Row Grid E1 Racks 2/3/4/5 (36 each)

> **Key finding:** Entire D-plane (all tiers) is 0% complete and represents the critical path to site completion.

---

## Device Model Inventory (Q31–Q45)

### All Models

| Model | Unique Devices | Total Connections |
|---|---|---|
| GPU-GB300-02 | 5,184 | 20,736 |
| GB300-NVLINK-SW | 2,592 | 5,184 |
| PS-1RU-03 | 2,304 | 2,304 |
| SN5610 | 1,592 | 24,388 |
| SN2201 | 847 | 25,574 |
| CDU-4RU-03 | 288 | 288 |
| STOR-COLD-02 | 210 | 840 |
| SN3700 | 138 | 3,212 |
| CPU-GP2-01 | 120 | 480 |
| OM2216-C14 | 111 | 1,466 |
| INF-MED-01 | 80 | 320 |
| CPU-GP2-02 | 75 | 300 |
| CM8148 | 62 | 1,352 |
| CPU-GP2-06 | 60 | 240 |
| SN4700 | 49 | 1,106 |
| STOR-COLD-01 | 42 | 168 |
| DF-3060-CHILD | 32 | 80 |
| NET-6X100G-01 | 16 | 106 |
| 1U-1N-GEN5-1NIC | 12 | 36 |
| 7750-SR-1SE | 7 | 60 |
| NGFW-4245 | 4 | 52 |
| PTX10002-36QDD | 4 | 98 |
| NET-6X100G-02 | 4 | 14 |
| SN3420 | 2 | 32 |
| 22/PID-/313334//SDN/ | 1 | 2 |
| Fiber Room 2 FDP | 1 | 14 |
| FDP | 1 | 1 |
| PA-1420 | 1 | 13 |

**Model with most connections:** SN2201 at 25,574

> Note: `DF-3060` is stored as `DF-3060-CHILD`. `22/PID-/313334//SDN/` appears to be a malformed Excel parse artifact.
> `PROLIANT-DL360-GEN10-PLUS` and `CPU-HPE-01` are not present in this cutsheet.

---

## Topology and Section Counts

### Tier Interconnects

| Section | Count |
|---|---|
| TIER-1 TO TIER-0 (all) | 32,124 (72.6% of all connections) |
| TIER-2 TO TIER-1 (all) | 1,980 |
| TIER-3 TO TIER-2 (all) | 1,584 |
| TIER-4 TO TIER-3 | 0 — not present |

### TIER-3 TO TIER-2 Breakdown

| Section | Count |
|---|---|
| TIER-3 TO TIER-2 1 C | 576 |
| TIER-3 TO TIER-2 1 D | 576 |
| TIER-3 TO TIER-2 1 A | 144 |
| TIER-3 TO TIER-2 1 B | 144 |
| TIER-3 TO TIER-2 1 E | 144 |

### TIER-2 TO TIER-1 Breakdown

| Section | Count |
|---|---|
| TIER-2 TO TIER-1 C | 864 |
| TIER-2 TO TIER-1 D | 864 |
| TIER-2 TO TIER-1 A | 144 |
| TIER-2 TO TIER-1 B | 72 |
| TIER-2 TO TIER-1 GRID:E (x2) | 36 |

### Management & Infrastructure Sections

| Section | Count |
|---|---|
| DH202 T1 ROCE MGMT | 1,192 |
| MGMT-DIST | 411 |
| INFRA-DIST-D | 2,401 |
| INFRA-DIST-C | 2,395 |
| DH202 T2 ROCE MGMT | 272 |
| BACKBONE MGMT | 114 |
| DH202/DH204 OVERFLOW T0 ROCE MGMT | 108 each |
| MGMT-CORE | 90 |
| STOR INFRA-DIST A3 | 130 |
| BACKBONE/OPTICAL MGMT | 20 |
| OOB-FW | 11 |
| ROCE INFRA AGGS DH202 | 6 |

**ROCE total:** 1,686 connections
**INFRA-DIST total:** 5,152 connections

### Sections Not Present in This Cutsheet

The following section names return 0 results — they belong to other sites or naming conventions:
- GG1-A, GG1-B, GG1-C
- NET-AGG, COMP-AGG
- NET-DIST
- POD-DIST-B (site uses POD-DIST-C and POD-DIST-D)
- TIER-4 TO TIER-3

### POD-DIST Sections

| Section | Count |
|---|---|
| POD-DIST-C 1 | 33 |
| POD-DIST-C 3 | 25 |
| POD-DIST-D 1 | 25 |
| POD-DIST-D 2 | 25 |
| POD-DIST-D 3 | 25 |
| POD-DIST-C 2 | 17 |

---

## LLDP and Link Verification (Q61–Q70)

| Question | Answer |
|---|---|
| LLDP Passed | 0 |
| LLDP Failed | 0 |
| Human Verified | 1 |
| Any verified (Complete + LLDP + Human) | 12,036 |
| Pending LLDP verification | 31,372 |
| Burndown link status data | None (burndown tab not in upload) |
| Neighbor mismatch (burndown vs cutsheet) | 0 (burndown empty) |

> **Key finding:** LLDP has not been run on this site. All 12,036 "verified" connections carry manual `Cable Is Ran: Complete` status. LLDP data will populate Q61–Q66 once verification begins.

---

## Location and Rack Queries (Q71–Q80)

| Question | Answer |
|---|---|
| Unique racks | 479 |
| Unique A-side LOC:CAB:RU | 1,625 |
| Data halls | 2 (dh202, dh204) |
| OOB-FW locode | US-LZL01 → US-LZL01 |
| NET-AGG devices | 0 — section not present |
| COMP-AGG devices | 0 — section not present |

### Busiest Racks (by A-side connections)

| Rack | Connections |
|---|---|
| dh202:001 | 828 |
| dh204:021 | 740 |
| dh204:121 | 722 |
| dh204:201 | 722 |
| dh202:201 | 722 |

### OOB-FW Connections (11 total)

Single firewall `oob-fw-01-r041-us-central-08a` connecting to:
- `Midco 1G OOB DIA` (external circuit)
- Console servers: `con-01-r041`, `con-01-r042`, `dh202-con-01-r043/r044`
- Jump/ZTP hosts: `dh202-metal-jump01`, `dh202-metal-ztp01`
- Mgmt-core switches: `dh202-mgmt-core-01a-r043`, `dh202-mgmt-core-02a-r044`, `mgmt-core-01a-r041`, `mgmt-core-02a-r042`

### Z-side FDP Devices

| Device | Model | Connections |
|---|---|---|
| dh202-pp-ru42-r041-us-central-08a | Fiber Room 2 FDP | 14 |
| FDP Patch Panel | 22/PID-/313334//SDN/ | 1 |
| FDP Patch Panel | Patch Panel | 1 |
| Midco 1G OOB DIA | FDP | 1 |

---

## Risk and Anomaly Analysis (Q91–Q100)

| Question | Answer |
|---|---|
| Model casing inconsistencies | None — data is clean |
| Nokia/7750 naming variants | Only one: `7750-SR-1SE` — consistent |
| Breakout double-count risk | None — T2 switches connect 18-way (expected fat-tree topology) |
| Both optics empty | 24,835 (all CAT6a copper, no optics by design) |
| NET-AGG + COMP-AGG total | 0 — not present |
| LLDP Failed | 0 |
| Malformed model values | `22/PID-/313334//SDN/` (1 device, 2 connections) |

### TIER-1 TO TIER-0 Incomplete vs Complete

| Section | Total | Incomplete | Status |
|---|---|---|---|
| TIER-1 TO TIER-0 D3 | 14,472 | 14,472 | 100% incomplete |
| TIER-1 TO TIER-0 C3 | 15,138 | 10,026 | 66% incomplete |
| TIER-1 TO TIER-0 A4 (x3) | 1,062 | 1,044 | 98% incomplete |
| TIER-1 TO TIER-0 D1 | 216 | 216 | 100% incomplete |
| TIER-1 TO TIER-0 D2 | 216 | 216 | 100% incomplete |
| TIER-1 TO TIER-0 E1 (x1) | 192 | 192 | 100% incomplete |
| TIER-1 TO TIER-0 A3 (x3) | 54 | 54 | 100% incomplete |
| TIER-1 TO TIER-0 A1 | 144 | 0 | ✓ Complete |
| TIER-1 TO TIER-0 A2 | 162 | 0 | ✓ Complete |
| TIER-1 TO TIER-0 B1 | 36 | 0 | ✓ Complete |
| TIER-1 TO TIER-0 C1 | 288 | 0 | ✓ Complete |
| TIER-1 TO TIER-0 C2 | 144 | 0 | ✓ Complete |

### Final Gap Analysis

| Metric | Count |
|---|---|
| Total connections | 44,246 |
| Currently verified | 12,036 |
| Remaining gap | **32,210** |
| Target (all resolved) | 44,246 |

---

## Top 10 Most Connected Devices

### A-Side (Spine/Leaf switches)

| Device | Connections |
|---|---|
| dh202-t0-c1-02-r001-us-central-08a | 144 |
| dh202-t0-c1-01-r081-us-central-08a | 144 |
| dh202-t0-c1-01-r101-us-central-08a | 144 |
| dh202-t0-c1-01-r111-us-central-08a | 144 |
| dh202-t0-c1-01-r021-us-central-08a | 144 |
| dh202-t0-c1-01-r011-us-central-08a | 144 |
| dh202-t0-c1-01-r031-us-central-08a | 144 |
| dh202-t0-c1-01-r091-us-central-08a | 144 |
| dh202-t0-c1-01-r001-us-central-08a | 144 |
| dh202-t0-c1-02-r011-us-central-08a | 144 |

### Z-Side (T1 switches)

| Device | Connections |
|---|---|
| dh202-t1c-c3-01-r201-us-central-08a | 34 |
| dh202-t1b-c2-01-r121-us-central-08a | 34 |
| (all T1 switches) | 34 each |

---

## Key Findings Summary

1. **Site is 27.2% complete** — 12,036 of 44,246 connections verified (all manual, no LLDP run)
2. **D-plane is entirely unstarted** — TIER-1→0 D3 (14,472), INFRA-DIST-D (2,401), TIER-2→1 D (864), TIER-3→2 1D (576) are all at 0%
3. **C3 section is 66% incomplete** — 10,026 outstanding connections
4. **32,210 connections** need attention before site completion
5. **LLDP has never been run** — all completions are manual status entries
6. **Data quality is clean** — no model casing issues, no duplicate ports (port reuse is intentional breakout), no literal dash values
7. **GG1-*, NET-AGG, COMP-AGG, NET-DIST, POD-DIST-B** section names do not exist in this cutsheet — test suites targeting these need updating
8. **Dominant optic:** OSFP-800G-2DR4 at 19,764 total — GPU spine breakout pattern (OSFP→QSFP112 is expected mismatch)
9. **838 blank-status rows** stored as `unknown` — consider normalizing to `not_run`
10. **Malformed model:** `22/PID-/313334//SDN/` (1 device) — likely an Excel parse artifact to investigate