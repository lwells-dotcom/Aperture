"""
query_lexicon.py - Central keyword dictionaries and synonym sets for query routing.

All domain-specific vocabulary lives here so routers and extractors share
a single source of truth.  Sets are frozensets for immutability and O(1) lookup.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Intent signal word sets
# ---------------------------------------------------------------------------

COUNT_WORDS: frozenset[str] = frozenset({
    "count", "how", "many", "total", "number", "summary", "breakdown",
    "tally", "sum", "aggregate",
})

STATUS_WORDS: frozenset[str] = frozenset({
    "status", "state", "health", "progress", "complete", "completed",
    "failed", "pending", "verified", "passed",
})

OPTIC_WORDS: frozenset[str] = frozenset({
    "optic", "optics", "transceiver", "transceivers",
    "sfp", "sfp+", "qsfp", "qsfp28", "qsfp56", "qsfp-dd", "qsfpdd",
})

CABLE_WORDS: frozenset[str] = frozenset({
    "cable", "cables", "fiber", "fibers", "smf", "mmf",
    "single-mode", "multi-mode", "lc-to-lc",
})

DEVICE_WORDS: frozenset[str] = frozenset({
    "device", "devices", "switch", "switches", "router", "routers",
    "server", "servers", "node", "nodes", "gpu", "gpus", "compute",
})

ROLE_WORDS: frozenset[str] = frozenset({
    "role", "roles", "fdp", "cdu", "pdu", "tor", "ups",
    "spine", "leaf", "fabric", "patch",
    "fiber distribution panel", "top-of-rack", "top of rack",
})

LOCATION_WORDS: frozenset[str] = frozenset({
    "rack", "racks", "cabinet", "cabinets", "cab", "location",
    "ru", "rackunit", "enclosure",
})

SECTION_WORDS: frozenset[str] = frozenset({
    "section", "sections", "tier", "tiers", "topology",
})

BURNDOWN_WORDS: frozenset[str] = frozenset({
    "burndown", "neighbor", "link-status", "link",
})

LLDP_WORDS: frozenset[str] = frozenset({
    "lldp",
})

FAIL_WORDS: frozenset[str] = frozenset({
    "fail", "failed", "failure", "failures", "error", "errors",
    "issue", "issues", "problem", "problems",
})

MISMATCH_WORDS: frozenset[str] = frozenset({
    "match", "mismatch", "mismatches", "expect", "expected",
    "wrong", "incorrect", "differ", "differs", "difference",
    "actual",
})

COMPLETION_WORDS: frozenset[str] = frozenset({
    "complete", "completed", "completion", "incomplete",
    "best", "highest", "lowest", "zero", "worst",
    "percentage", "rate",
})

SIDE_WORDS: frozenset[str] = frozenset({
    "a-side", "z-side", "a side", "z side",
    "aside", "zside",
})

DETAIL_WORDS: frozenset[str] = frozenset({
    "detail", "details", "info", "information", "about", "show",
    "describe", "tell",
})

LIST_WORDS: frozenset[str] = frozenset({
    "list", "all", "every", "inventory", "show", "which", "what",
    "exist", "exists",
})

RANKING_WORDS: frozenset[str] = frozenset({
    "most", "highest", "greatest", "max", "busiest", "top",
    "least", "fewest", "lowest", "min",
})

CONNECTION_WORDS: frozenset[str] = frozenset({
    "connection", "connections", "link", "links", "port", "ports",
})

SITE_WORDS: frozenset[str] = frozenset({
    "site", "overview", "stats",
})

IP_WORDS: frozenset[str] = frozenset({
    "ip", "address", "ipv4", "ipv6", "subnet", "vrf",
})

DIFF_WORDS: frozenset[str] = frozenset({
    "diff", "difference", "differences", "changed", "change", "changes",
    "delta", "compare", "comparison", "versus", "vs",
    "added", "removed", "new", "missing", "modified",
})

UPLOAD_WORDS: frozenset[str] = frozenset({
    "upload", "uploads", "version", "versions", "revision", "revisions",
    "previous", "latest", "last", "recent", "history",
})

CROSS_SITE_WORDS: frozenset[str] = frozenset({
    "across", "between", "compare", "comparison", "cross-site",
    "sites", "both", "multiple", "different",
})

TREND_WORDS: frozenset[str] = frozenset({
    "trend", "trending", "trends", "progression", "progress", "progressed",
    "evolve", "evolution", "improving", "worsening", "growing",
    "shrinking", "trajectory", "timeline", "historical",
})

# ---------------------------------------------------------------------------
# Named topology section prefixes (used by section extractor)
# ---------------------------------------------------------------------------

KNOWN_SECTION_PREFIXES: frozenset[str] = frozenset({
    "BACKBONE", "OOB-FW", "OOB", "FBS", "MGMT-CORE", "MGMT-DIST", "MGMT",
    "GRID-AGG", "INFRA-DIST", "POD-DIST", "ROCE",
    "NET-AGG", "COMP-AGG", "NET-DIST", "COMP-DIST", "UFM-PATH",
})

# ---------------------------------------------------------------------------
# Role keyword -> canonical filter term mapping
# ---------------------------------------------------------------------------

ROLE_KEYWORD_MAP: list[tuple[str, str]] = [
    (r"\bFDPs?\b|fiber\s+distribution\s+panels?", "FDP"),
    (r"\bCDUs?\b", "CDU"),
    (r"\bPDUs?\b", "PDU"),
    (r"\b(?:TORs?|top[\s-]of[\s-]rack)\b", "TOR"),
    (r"\bUPSs?\b", "UPS"),
    (r"\bpatch\s+panel\b", "patch"),
    (r"\bspine\b", "spine"),
    (r"\bleaf\b", "leaf"),
    (r"\bfabric\b", "fabric"),
]
