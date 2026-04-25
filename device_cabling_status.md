# Device Inventory & Cabling Status Report
**Location:** US-CENTRAL-08A (Ellendale)  
**Source File:** 1776937291_demo_user_MASTER-US-CENTRAL-08A-US-LZL01-ELLENDALE.xlsx  
**Generated:** April 2026

## 1. Device Count Summary

| Device Model                  | In Service | Not In Service | Total |
|-------------------------------|------------|----------------|-------|
| PA-1420                       | 1          | 0              | 1     |
| 7750-SR-1SE                   | 4          | 2              | 6     |
| OM2216-C14                    | 46         | 65             | 111   |
| SN2201                        | 445        | 402            | 847   |
| SN3700                        | 96         | 42             | 138   |
| SN4700                        | 26         | 23             | 49    |
| NGFW-4245                     | 4          | 0              | 4     |
| SN3420                        | 2          | 0              | 2     |
| CM8148                        | 37         | 25             | 62    |
| SN5610                        | 626        | 966            | 1,592 |
| FDP                           | 1          | 0              | 1     |
| NET-6X100G-01                 | 16         | 0              | 16    |
| NET-6X100G-02                 | 3          | 1              | 4     |
| 22/PID-/313334//SDN/          | 1          | 0              | 1     |
| CPU-GP2-02                    | 75         | 0              | 75    |
| INF-MED-01                    | 80         | 0              | 80    |
| CPU-GP2-01                    | 120        | 0              | 120   |
| 1U-1N-GEN5-1NIC               | 12         | 0              | 12    |
| DF-3060-CHILD                 | 8          | 0              | 8     |
| GPU-GB300-02                  | 1,584      | 3,600          | 5,184 |
| GB300-NVLINK-SW               | 90         | 2,502          | 2,592 |
| CDU-4RU-03                    | 10         | 278            | 288   |
| PS-1RU-03                     | 512        | 1,792          | 2,304 |
| PTX10002-36QDD                | 0          | 4              | 4     |
| Fiber Room 2 FDP              | 0          | 1              | 1     |
| STOR-COLD-02                  | 0          | 210            | 210   |
| STOR-COLD-01                  | 0          | 42             | 42    |
| CPU-GP2-06                    | 0          | 60             | 60    |
| **TOTAL**                     | **3,799**  | **10,015**     | **13,814** |

---

## 2. Cabling Status Overview

**Total Connections:** 49,286  
**Cables Not Yet Run:** 29,929 (**60.7%**) ← **Major Concern**

### Cabling Breakdown

| Status                        | Count    | % of Total |
|-------------------------------|----------|------------|
| Cable Not Run                 | 29,929   | 60.7%      |
| Cable Is Ran Complete         | 14,655   | 29.7%      |
| Cable Is Ran Not Terminated   | 2,486    | 5.0%       |
| Addition                      | 1,233    | 2.5%       |
| Human Verified                | 1        | ~0%        |

**Summary:**
- Fully complete + human verified: **14,656**
- Still needs work (Not Run + Not Terminated + Addition): **33,648**
- Partially completed cables (Ran but Not Terminated): **2,486** ← Requires immediate follow-up

---

## 3. Rack dh202:041 Overview (Row 41 Focus)

### Devices Installed in Rack dh202:041

| Position | DNS / Hostname                          | Device Model     | Status    |
|----------|-----------------------------------------|------------------|-----------|
| 42       | 42dh202:041:42dh202-pp-ru42-r041-us-central-08a | Fiber Room 2 FDP | Pending   |
| 33       | mlr1-us-central-08a                     | 7750-SR-1SE      | Installed |
| 28       | dsr1-us-central-08a                     | 7750-SR-1SE      | Installed |
| 28       | (Patch Panel)                           | 22/PID-/313334//SDN/ | Installed |
| 22       | con-01-r041-us-central-08a              | OM2216-C14       | Installed |
| 20       | net-01-r041-us-central-08a              | SN2201           | Installed |
| 18       | mgmt-core-01a-r041-us-central-08a       | SN3700           | Installed |
| 10       | oob-fw-01-r041-us-central-08a           | **PA-1420**      | Installed |
| 8        | fip1-us-central-08a                     | NET-6X100G-01    | Installed |

**Note:** Rack dh202:041:10 contains the **PA-1420** firewall (`oob-fw-01-r041-us-central-08a`) under **BACKBONE MGMT / OOB-FW**. It has **5x SFP-BASE-10G-LR** optics installed. No detailed port-to-port cabling information is available for this specific rack position.

---

## 4. Optic Summary

| Optic Type                  | Count |
|-----------------------------|-------|
| MAM1Q00A-QSA28:10G-SFP-LR   | 1     |
| QSFP28-100G-DR1             | 19    |
| QSFPDD-400G-DR4             | 23    |
| QSFPDD-400G-LR4             | 1     |
| SFP-BASE-10G-LR             | 5     |

### Detailed Optic Placement (dh202:041)

| Location          | Port              | Optic Type                  |
|-------------------|-------------------|-----------------------------|
| dh202:041:8       | enp67s0f0np0      | QSFP28-100G-DR1             |
| dh202:041:8       | enp67s0f1np1      | QSFP28-100G-DR1             |
| dh202:041:8       | enp199s0f0np0     | QSFP28-100G-DR1             |
| dh202:041:8       | enp199s0f1np1     | QSFP28-100G-DR1             |
| dh202:041:10      | ethernet1/15      | SFP-BASE-10G-LR             |
| dh202:041:10      | ethernet1/16      | SFP-BASE-10G-LR             |
| dh202:041:10      | ethernet1/17      | SFP-BASE-10G-LR             |
| dh202:041:10      | ethernet1/18      | SFP-BASE-10G-LR             |
| dh202:041:10      | ethernet1/21      | SFP-BASE-10G-LR             |
| dh202:041:18      | swp1              | QSFP28-100G-DR1             |
| dh202:041:18      | swp2–swp10        | QSFP28-100G-DR1 (9x)        |
| dh202:041:18      | swp24             | MAM1Q00A-QSA28:10G-SFP-LR   |
| dh202:041:18      | swp26             | QSFP28-100G-DR1             |
| dh202:041:18      | swp31–swp32       | QSFP28-100G-DR1 (2x)        |
| dh202:041:20      | swp49             | QSFP28-100G-DR1             |
| dh202:041:20      | swp51             | QSFP28-100G-DR1             |
| dh202:041:28      | 1/1/c1–c4, c7–c10, c13–c16, c19, c21, c25–c26 | QSFPDD-400G-DR4 (multiple) |
| dh202:041:28      | 1/1/c31           | QSFPDD-400G-LR4             |
| dh202:041:33      | 1/1/c1, c7, c13–c14 | QSFPDD-400G-DR4 (4x)      |

---

**End of Report**

**Recommendations:**
1. Prioritize termination of the 2,486 “Ran but Not Terminated” cables.
2. Accelerate running the 29,929 pending cables (61% incomplete).
3. Verify and document port-to-port connections for critical devices like the PA-1420 OOB firewall.
