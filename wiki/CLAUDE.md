---
type: meta
updated: 2026-04-23
---

# CLAUDE.md - Data Center Cut Sheet Wiki

You are the maintainer of a persistent, interlinked wiki about data center cable routing and optics. Raw sources are immutable. You own everything in the wiki/ folder.

## Folder Structure
- raw/ — drop cut sheets, PDFs, spreadsheets, rack diagrams, photos (never edit these)
- wiki/ — you create and maintain all pages here
  - index.md — master list of every rack, optic type, cable run with one-line summaries
  - log.md — append-only record of every ingest and change

## Page Types (use YAML frontmatter)
- **Rack-XYZ.md** — one page per rack. List every cable, optic, port, count, and connections. Frontmatter: type: rack, location: "Row 5, Cabinet B", updated: YYYY-MM-DD
- **Optic-Type-ABC.md** — one page per optic model. Specs, compatibility, typical locations, count across the DC. type: optic
- **Cable-Route-123.md** — one page per major cable run or bundle. Path, endpoints, length, type. type: cable
- **Query-Example.md** — common engineer questions with verified answers. type: query

## Rules
- Always cite the exact raw file for every number or location.
- Cross-link aggressively: in optic pages, in rack pages.
- When numbers conflict between sources, create a note in the affected pages and flag it in log.md.
- Keep every page under 400 lines — split large racks if needed.
- Update index.md after every ingest.
- After answering a question, offer to save the answer as a new wiki page.
