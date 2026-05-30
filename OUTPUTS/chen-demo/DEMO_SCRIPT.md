# Atlas Demo Script — Chen Goldberg
**Date:** Monday, June 1, 2026
**Presenter:** Lamar Wells
**Duration:** 10-15 minutes

---

## Pre-demo checklist

Before Chen arrives:

1. Open a browser to `http://localhost:5000` (main Atlas page)
2. Have the 08A cutsheet file ready on your desktop
3. Verify Postgres is running (`docker ps` or check Kind cluster)
4. Open a second browser tab to `http://localhost:5000/dashboard` (don't show yet)
5. Pre-verify your PIN so the session is active (username: demo_user)

---

## The pitch (30 seconds)

"Atlas is the tool I built to answer questions about our data center cutsheets. You upload an Excel cutsheet, it parses every connection, device, and optic into Postgres, and then you can ask it natural language questions. No SQL. No digging through 19MB spreadsheets. It also pulls live data from NetBox for real-time fleet visibility."

---

## Act 1 — Upload a cutsheet (2 minutes)

1. Click "Upload & Count" with the 08A cutsheet
2. While it processes, explain: "This is parsing the full Ellendale 08A master cutsheet. It finds every optic type, normalizes statuses, strips section headers, and loads it all into Postgres with dedup."
3. When results appear, point out the optic count breakdown
4. Note the blue info banner: "The database load runs in the background so you're not waiting."

---

## Act 2 — Ask questions (5-7 minutes)

Wait about 30 seconds after upload for Postgres ingest to finish, then ask these in order. Each one shows a different capability.

### Q1: Big picture
```
Give me an overview of this site
```
Shows site_overview — total connections, devices, status breakdown. Good opener.

### Q2: Core use case (optic counts)
```
How many QSFP-DD optics are there?
```
Shows optic_count with breakout dedup. The number that matters for procurement.

### Q3: Section drill-down
```
Summarize the SPINE section
```
Shows section_summary — devices, connections, and status within a logical section of the cutsheet.

### Q4: Device search
```
How many DGX B200 servers are in this cutsheet?
```
Shows model_search — finds exact model matches across all data halls.

### Q5: Rack-level detail
```
What's in rack 041 in DH202?
```
Shows rack_summary — every device, optic, and cable touching that rack.

### Q6: Cable types
```
What cable types are used in DH202?
```
Shows cable_type_summary — DAC vs AOC vs fiber breakdown by data hall.

### Q7: Connection status
```
What connections are still pending in DH204?
```
Shows connection_status — filters by status, shows what's left to complete.

### Q8: (If time) Cross-upload diff
```
What uploads do we have?
```
Shows upload_list — demonstrates version tracking.

---

## Act 3 — Rack Analyzer (2 minutes)

1. Click the "Rack Analyzer" tab
2. Upload the same cutsheet as "Cutsheet Master"
3. Set Room = DH2, Rack = 121
4. Click "Query Rack"
5. Walk through: summary, devices in rack, optic summary, internal vs outgoing cables
6. "Every cable label is downloadable as a CSV. The field team uses these for install day."

---

## Act 4 — Live Dashboard (2 minutes)

1. Click "Live Dashboard" in the header
2. Show the KPI tiles at the top (devices, interfaces, optics across all sites)
3. Show the site dropdown — "This auto-discovers every site in NetBox"
4. Scroll to the device breakdown chart and optic breakdown
5. "This refreshes every 15 minutes from NetBox. The data you see is always current."

---

## Anticipated questions & answers

**"How does it avoid hallucinating answers?"**
"There's no LLM-generated SQL. Every query is a parameterized template. The AI classifies your question into one of 30+ query types, runs the exact SQL template, and then summarizes the result. The data is always real."

**"Can this work for other sites?"**
"Yes. The column mapping is profile-based. Each site gets a profile that maps its cutsheet layout to our standard schema. Adding a new site is a config change, not a code change."

**"What's the tech stack?"**
"Python, Flask, Postgres, Docker. The AI layer is Anthropic's Claude. It runs on Kind locally right now. The infrastructure is ready for deployment to our clusters."

**"Is the data secure?"**
"PIN-based auth with HMAC tokens. Rate limiting on all endpoints. No cutsheet data leaves the local environment. The AI API call sends only the relevant Postgres query results as context, not the raw cutsheet."

**"How long did this take to build?"**
"I've been building this over the last few months. It started as an optic counter script and grew into a full query engine as the team's questions got more complex."
