# Atlas Grounding Quality Test Questions

**Sources:** CUTSHEET_DEMO_V2.xlsx (49,860 rows, single CUTSHEET tab) and MASTER-US-WEST-09A-US-QNC01-PRIME-QUINCY CUTSHEET.csv (4,331 rows, Quincy production). Questions grounded in both files — production values take precedence where the two diverge.
**Purpose:** LLM grounding quality testing — these questions should be answerable from cutsheet data alone.
**Sections present (demo):** OOB-FW, BACKBONE MGMT, MGMT-CORE, RACK xx MGMT-DIST, RACK xx NET+CON, STOR, GRID-AGG (A/B/C), POD-DIST (B/C), INFRA-DIST, TIER-3/2/1/0 interconnects, DH201, DH203
**Sections present (production):** GG1-A, GG1-B, GG1-C, NET-AGG, COMP-AGG, NET-DIST, COMP-DIST, FBS, TIER-4 TO TIER-3, TIER-3 TO TIER-2, DH2 ROW N MGMT + CON, UFM-PATH
**Status values (demo):** Cable Is Ran: Complete, Cable Is Ran: Not Terminated, Cable Not Run, Human Verified, PROBLEM: No Optics, REMOVE CABLING
**Status values (production):** `LLDP:  Passed` (double-space, 79.7%), `LLDP:  Failed` (double-space, 6.9%), Cable Is Ran: Complete (13.0%), Human Verified (0.4%)

---

## Optic Inventory Counts

1. How many QSFP28-100G-DR1-LOW-PWR optics are in the cutsheet?
2. How many JNP-QSFP-100G-LR4-LU optics are deployed across all sections?
3. What is the total count of QSFP28-100G-DR1 optics on the A-side?
4. How many QSFPDD-400G-DR4 optics appear on the Z-side?
5. How many QSFPDD-400G-LR4-LU optics are present across all sections?
6. How many SFP-BASE-10G-LR optics are present across all connections?
7. How many QSFP-100G-LR4-L optics appear in the Z-side column?
8. What is the complete optic inventory breakdown showing count per optic type?
9. Which optic type has the highest total count across both A and Z sides?
10. Are there any connections where the A-side optic does not match the Z-side optic type?
11. How many connections have a QSFPDD-400G-LR4-LU optic on the Z-side?
12. How many connections list a MAM1Q00A-QSA28:10G-SFP-LR optic?
13. What percentage of connections have optic data populated on both sides?
14. How many connections show a dash ("-") as the optic value indicating no optic installed?
15. Which sections contain the most QSFP28-100G-DR1-LOW-PWR optics?

---

## Cable Status and Completion

16. How many cables have a status of "Cable Is Ran: Complete"?
17. How many connections show a status of `LLDP:  Passed` (note: two spaces after the colon)?
18. How many connections show a status of `LLDP:  Failed` (note: two spaces after the colon)?
19. What percentage of all connections are marked complete or LLDP-verified?
20. Which devices have the highest count of `LLDP:  Failed` connections?
21. What is the ratio of `LLDP:  Passed` to `LLDP:  Failed` connections across the entire cutsheet?
22. How many connections have been "Human Verified"?
23. What is the overall cable completion rate for the site?
24. Which section has the highest number of incomplete connections?
25. Which section has the best completion percentage?
26. How many connections are in a non-complete state across all sections?
27. Are there any sections where zero cables are complete?
28. What is the ratio of `LLDP:  Passed` to `Cable Is Ran: Complete` connections?
29. What is the total number of connections that need attention (not complete and not LLDP-verified)?
30. Which sections have the highest concentration of `LLDP:  Failed` connections?

---

## Device Connectivity and Topology

31. How many unique devices appear on the A-side of the cutsheet?
32. How many unique devices appear on the Z-side of the cutsheet?
33. What is the total number of unique physical devices across both sides?
34. How many connections are listed in total in the cutsheet?
35. How many topology sections are defined in the cutsheet?
36. What network tiers are represented in the cutsheet (Tier-0 through Tier-4)?
37. How many TIER-3 TO TIER-2 connections are in the cutsheet?
38. How many TIER-2 TO TIER-1 connections exist?
39. How many TIER-1 TO TIER-0 connections are defined?
40. What is the connection count for the OOB-FW section?
41. How many connections are in the BACKBONE MGMT section?
42. What sections make up the management plane (OOB, MGMT, BACKBONE)?
43. How many GG1-A connections are defined (all GG1-A sections combined)?
44. How many GG1-B connections are defined across all GG1-B sections?
45. How many GG1-C connections are defined?

---

## Device Model Queries

46. How many CPU-GP2-02 devices appear in the cutsheet?
47. How many DF-3060 devices are present?
48. How many SN4700 switches are in the dataset?
49. How many SN3700 switches appear?
50. How many SN3420 switches are listed?
51. How many SN2201 switches appear in the cutsheet?
52. How many 7750-SR-1SE routers are in the cutsheet?
53. How many PA-1420 firewalls are present?
54. How many CM8148 devices appear?
55. How many OM2216-C14 devices are listed?
56. How many NET-6X100G-01 devices appear in the cutsheet?
57. How many 1U-1N-GEN5-1NIC devices appear?
58. What is the complete device model inventory sorted by count?
59. Which device model has the most connections in the dataset?
60. Are there any PROLIANT-DL360-GEN10-PLUS or CPU-HPE-01 devices and how many connections do they have?

---

## LLDP and Link Verification

61. How many connections have the exact status `LLDP:  Passed` (two spaces after the colon)?
62. How many connections have the exact status `LLDP:  Failed` (two spaces after the colon)?
63. Which devices have the most `LLDP:  Failed` connections?
64. Are there any connections in the burndown sheet with link status "down"?
65. Which sections have the highest concentration of `LLDP:  Failed` statuses?
66. What is the ratio of `LLDP:  Passed` to `LLDP:  Failed` connections expressed as a percentage?
67. Are there any connections where the current LLDP neighbor does not match the expected Z-side device?
68. Which ports have been human verified versus LLDP verified?
69. How many connections are pending LLDP verification?
70. What is the total count of connections with any verified status (`LLDP:  Passed` or Human Verified)?

---

## Location and Rack Queries

71. What devices are installed in the NET-AGG section?
72. What devices appear in the COMP-AGG section?
73. How many racks are represented in the cutsheet across all data halls?
74. What connections are associated with the GG1-A section?
75. What connections are associated with the NET-DIST section?
76. How many unique LOC:CAB:RU locations appear on the A-side?
77. Which rack has the highest number of connections?
78. What is the locode associated with the OOB firewall connections?
79. How many distinct data halls are represented in the cutsheet?
80. What devices are listed as FDP (fiber distribution panel) on the Z-side?

---

## Cross-Section and Capacity Planning

81. What is the connection count for the TIER-4 TO TIER-3 section?
82. What is the connection count for the TIER-3 TO TIER-2 section?
83. What is the connection count for the TIER-2 TO TIER-1 section?
84. How many connections are in the GG1-A sections combined?
85. How many connections are in the GG1-B sections combined?
86. How many connections are in the GG1-C sections combined?
87. What is the total number of storage-related connections (STOR sections)?
88. How many ROCE infrastructure connections are defined?
89. What is the infrastructure distribution (INFRA-DIST) connection count?
90. How many POD-DIST-B connections are present across all four POD-DIST-B sections?

---

## Risk and Anomaly Queries

91. Are there any Z-MODEL values where the same device is recorded with inconsistent casing (e.g., "sn3700" versus "SN3700") and which rows are affected?
92. Are there connections with breakout cables where the optic count might be double-counted?
93. Which sections have the most connections still in a non-LLDP-verified state indicating installation risk?
94. How many connections have a `LLDP:  Failed` status and which devices are most frequently involved?
95. Are there any connections where both A and Z optic fields are empty?
96. Which device models appear on the Z-side with inconsistent naming (e.g., "NOKIA-7750-SR-1se" versus "7750-SR-1SE")?
97. How many connections have a cable type of "LC-TO-LC SMF" versus other cable types?
98. Are there any connections in the TIER-1 TO TIER-0 sections that are not yet complete?
99. What is the total count of connections across both the NET-AGG and COMP-AGG sections?
100. If all currently unverified connections (not `LLDP:  Passed` and not `Human Verified`) were resolved, what would the final verified connection count be and what is the remaining gap?
