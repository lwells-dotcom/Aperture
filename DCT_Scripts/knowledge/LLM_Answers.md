## ## Summary

At site QCY, there are **3,707 total connection records** across all status categories. Of those, **3,436 are either LLDP-verified or marked as cable run complete**, which works out to roughly **92.7% of all connections** in a confirmed/complete state.

## Key Findings

- **LLDP Passed: 2,953** — the largest group, representing about **79.7%** of all connections on their own
- **Cable Is Ran Complete: 483** — adds another **13.0%**, covering physically completed runs that may not yet have LLDP confirmation
- **Combined complete/verified: 3,436 out of 3,707 = ~92.7%**
- **LLDP Failed: 255** (~6.9%) — these are the primary concern; they represent connections that were attempted but did not pass verification and likely need remediation
- **Human Verified: 14** (~0.4%) — a small subset manually confirmed, possibly where automated checks weren't applicable
- **Unclassified (null status): 2** — negligible but worth cleaning up in the source data

## The 255 LLDP failures are the most actionable gap. At 6.9% of total connections, resolving those would push the site to near-complete verified status. The 14 human-verified entries suggest some edge cases are already being handled manually, which is worth tracking to ensure they don't grow as a workaround.