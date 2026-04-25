## Cable status and completion questions. Ellendale Cut-Sheet
Question 16: 
## Summary

At site ELD, the cable status breakdown shows six categories. The data comes directly from the ELD site records via a cable_status query returning 6 rows.

## Key Findings

- **"Cable Is Ran: Complete"** accounts for **14,655 cables** at site ELD — making it the second largest status category.
- For context, the largest group is "Cable Not Run" at 29,929, meaning more cables are still pending than completed.
- Other notable statuses include "Cable Is Ran Not Terminated" (2,486) and "Addition" (1,233), which may warrant follow-up from both inventory and field teams.
- There is also 1 "Human Verified" record and 982 records with no status label, which could be worth cleaning up on the inventory side.

Question 18: 
## Summary

The context provided contains connection status data for site ELD, but it only includes six status categories: **Cable Not Run**, **Cable Is Ran Complete**, **Cable Is Ran Not Terminated**, **Addition**, an empty/blank status, and **Human Verified**. There is no `LLDP:  Failed` status present in this dataset.

## Key Findings

- **Zero** connections with a status of `LLDP:  Failed` appear in the ELD site data — that specific status is simply not represented in the results returned from the source query (6 rows, POSTGRES).
- The six statuses returned account for a combined **49,286** connections, and none of them match the requested label.
- It's possible `LLDP:  Failed` exists at other sites or in a different query scope, but based strictly on this context, the count is **0** for site ELD.

Question 20: 
## Summary

The context provided contains only site-level connection status **counts by category** (e.g., Cable Not Run, Cable Is Ran Complete, etc.) for site ELD. It does not include a breakdown by individual device, and there is no `LLDP: Failed` category present in the returned data at all.

## Key Findings

- **No `LLDP: Failed` data is available** in this context — that status does not appear in any of the 6 returned rows from the ELD connection status query.
- The data returned is **aggregated at the site level**, not per device, so even if LLDP: Failed existed here, it would not identify which specific devices are affected.
- To answer your question, you would need a query that filters connections where status = `LLDP: Failed` and groups results **by device name or device ID** — that data was not returned in this pull.

To get the answer you're looking for, a device-level breakdown query against the ELD connection records, filtered specifically on the LLDP: Failed status, would be required.

Question #32
## Summary--Incorrect Union in the sql scripts, the llm couldn't split the data. I added a z_device_list routing pattern. 

The context provided is a device list query result showing all devices at site ELD along with their connection and port counts. However, this data represents a **flat device inventory** — it does not distinguish between A-side and Z-side of a cutsheet. The context contains no field or column indicating which side (A or Z) a device appears on for any given connection. It simply lists unique devices and their total connection counts across all connections they participate in.

To answer "how many unique devices appear on the Z-side of the cutsheet," I would need the actual cutsheet data with explicit A-side and Z-side columns (or equivalent fields like `z_device`, `z_side_device`, etc.) that identify which device is the Z-side termination for each circuit row.

## Key Findings

- **What the context contains:** 13,839 total connection rows aggregated into a device list showing each unique device name with its total connection count and port count. The query type is flagged as `device_list`, not a cutsheet with directional (A/Z) side detail.
- **What is missing:** No A-side or Z-side designation is present in this context. The data cannot be filtered or split by circuit side.
- **To answer your question accurately:** Please provide the cutsheet data with explicit A-side and Z-side device columns, or re-query using a `cutsheet` or `circuit` query type that returns per-connection records with side designations. That would allow a precise count of unique Z-side devices.