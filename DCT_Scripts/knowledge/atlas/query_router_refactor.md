# Atlas Query Router Refactor Plan

Status: IMPLEMENTED + NEW QUERY TYPES ADDED
Created: 2026-04-19
Implemented: 2026-04-19
Extended: 2026-04-19 (upload_diff, cross_site, trend query types)

## Problem Statement

atlas_query_router.py classify_question() uses a 240-line ordered regex list (_PATTERNS) where routing correctness depends on pattern position. This is functional but increasingly brittle, verbose, and hard to maintain. A small wording change can route a question to the wrong SQL template.

## Current State

- 29 question types in QUESTION_TYPES (22 original + 7 new cross-document types)
- ~90 compiled regex patterns in _PATTERNS (first-match-wins)
- 6 extractor functions (_extract_device_name, _extract_location_pattern, etc.)
- No classification observability (opaque routing decisions)
- Mirrored regex pairs for word order variants (count...optic / optic...count)
- Known false-positive triggers: bare \blldp\b, \bdata\s*halls?\b, generic LOC:CAB:RU

## Findings (ordered by impact)

### HIGH

1. Pattern order fragility: routing depends on position more than clear intent boundaries. Overlapping patterns for optic_count, connection_status, cable_status, section_summary, rack_summary, and location_lookup mean a small wording change easily misroutes.

2. Overly broad patterns create false positives:
   - `\blldp\b` catches any LLDP mention (even in context descriptions)
   - `\bdata\s*halls?\b` catches any data hall mention (even informational)
   - `\b[a-z]{1,4}\d+:\d+:\d+\b` immediately forces location_lookup regardless of actual intent

### MEDIUM

3. Regex verbosity from mirrored patterns: every "X...Y" intent also has "Y...X" as a separate pattern. Same semantic idea encoded 2-6 times. Maintenance risk when one branch gets updated but its mirror doesn't.

4. Extraction regexes are heuristic-heavy:
   - _extract_device_name() uses broad hostname-like patterns that pick up unrelated tokens
   - _extract_location_pattern() mixes exact LOC:CAB:RU with loose rack-token guesses
   - _extract_section_filter() only recognizes a small fixed set of topology words

### LOW

5. Prompt-injection regex in demo_auth_ai.py:116 strips "ignore previous", "system", "assistant" globally. Understandable for safety but can distort legitimate audit/compliance text or notes in the sheet.

## Target Architecture

### Module Structure

```
atlas_query_router.py    # Public facade (route_question, build_query_params, execute_query, format_results_for_llm)
query_intent.py          # Intent classification layer
query_extractors.py      # Focused structured extractors
query_lexicon.py         # Central keyword dictionaries and synonym sets
query_debug.py           # Optional classifier trace output
```

### Core Data Shapes

```python
@dataclass
class QuestionContext:
    raw: str
    normalized: str
    tokens: list[str]
    token_set: set[str]
    has_loc_token: bool
    has_model_token: bool
    has_device_token: bool
    extracted_device: str | None
    extracted_location: str | None
    extracted_model: str | None
    extracted_section: str | None
    extracted_optic: str | None
    extracted_role: str | None
    extracted_side: str | None

@dataclass
class IntentResult:
    question_type: str
    confidence: str       # "high", "medium", "low"
    reason: str           # human-readable tie-break explanation
    matched_domain: str   # "location", "status", "optic", etc.
    matched_signals: list[str]
```

### Classification Flow

1. `normalize_question(question) -> QuestionContext`: lowercase, collapse whitespace, tokenize, run extractors once
2. `classify_question(ctx) -> IntentResult`: run domain routers in priority order
3. `build_query_params(question, qtype, ...)`: reuse extracted fields from QuestionContext (no re-running regexes)

### Domain Routers (priority order)

```
route_burndown_intent(ctx)
route_role_intent(ctx)
route_location_intent(ctx)
route_status_intent(ctx)
route_optic_intent(ctx)
route_device_intent(ctx)
route_section_intent(ctx)
route_site_intent(ctx)
fallback -> "general"
```

### Key Routing Rules

**route_location_intent:**
- Exact LOC:CAB:RU token found -> location_lookup
- rack/cabinet words + most/highest/busiest -> rack_summary
- rack/cabinet words + what/list/show/on/in -> location_lookup

**route_status_intent:**
- lldp + fail -> lldp_failures
- neighbor + mismatch words -> lldp_neighbor_mismatch
- cable words + status/progress words -> cable_status
- connection/link words + status words -> connection_status

**route_optic_intent:**
- Optic token extracted OR optic words + count/summary words -> optic_count

**route_role_intent:**
- Role keywords (FDP, CDU, TOR, etc.) + optional side -> role_lookup
- Side detection attached here, not spread across file

**route_device_intent:**
- Specific hostname + detail words -> device_detail
- Specific hostname + connection words -> device_connections
- Model token + count/list words -> model_search
- Generic device inventory -> device_list

### Lexicon (query_lexicon.py)

```python
COUNT_WORDS = {"count", "how", "many", "total", "number", "summary", "breakdown"}
STATUS_WORDS = {"status", "state", "health", "progress", "complete", "failed", "pending"}
OPTIC_WORDS = {"optic", "optics", "transceiver", "sfp", "sfp+", "qsfp", "qsfp28", "qsfp-dd"}
CABLE_WORDS = {"cable", "cables", "fiber", "fibers"}
DEVICE_WORDS = {"device", "devices", "switch", "router", "server", "node", "gpu", "compute"}
ROLE_WORDS = {"role", "fdp", "cdu", "pdu", "tor", "ups", "spine", "leaf", "fabric"}
LOCATION_WORDS = {"rack", "cabinet", "cab", "location", "ru"}
SECTION_WORDS = {"section", "sections", "tier", "topology"}
BURNDOWN_WORDS = {"burndown", "neighbor", "link-status", "link", "lldp"}
```

### What Stays Regex

Extractors only. Regex is the right tool for:
- Hostnames
- LOC:CAB:RU tokens
- Model IDs (SN5610, PA-1420, 7750-SR-1SE)
- Tier-X to Tier-Y patterns
- IP addresses

### Debug Output Shape

```json
{
  "question_type": "location_lookup",
  "matched_domain": "location",
  "reason": "exact loc:cab:ru token found",
  "matched_signals": ["has_loc_token", "location_words"],
  "extractors": {
    "location": "dh201:042:42",
    "device": null,
    "model": null,
    "section": null,
    "role": null,
    "side": null
  }
}
```

## Implementation Order

1. Add normalized-question helpers and debug metadata (no behavior change)
2. Refactor most error-prone domains first: lldp_failures/connection_status, location_lookup/rack_summary, role_lookup/device_list
3. Move broad count/status/summary intents off regex onto keyword logic
4. Leave structured extractors regex-based (clean up, don't rewrite)
5. Trim old _PATTERNS list once tests prove parity or improvement
6. Tighten safety regex in demo_auth_ai.py separately (narrower instruction-shaped anchors)
7. Build classification test matrix before changing behavior broadly

## Safety Net

Must build a test matrix with representative questions before changing behavior:
- Optic counts, LLDP failures, section summaries, rack lookups, role lookups, model searches, site overviews
- Edge cases with overlapping wording
- Questions that previously misrouted (from 100-question test suite)

## Expected Outcome

- Less verbose (keyword sets replace mirrored regex pairs)
- Less order-dependent (domain routers vs one giant first-match-wins list)
- Easier to reason about (each router has one domain)
- Safer to extend (new question types don't disrupt existing routing)
- Auditable (debug metadata shows why a question was classified a certain way)
