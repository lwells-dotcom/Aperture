# LLM Routing Improvement Ideas

Observations from reading `demo_auth_ai.py`, `query_intent.py`, `atlas_query_router.py`,
and `atlas_postgres_context.py`. All suggestions are scoped to the question→LLM path.

---

## 1. Confidence-based model tiering

**Current:** Every question uses the same model (`ANTHROPIC_MODEL`, default `claude-sonnet-4-6`),
regardless of whether the answer is "42 SN5610s" or a multi-step SOX compliance analysis.

**Idea:** Map classification confidence + question type to a model tier:

```
high confidence + simple lookup  →  claude-haiku-4-5   (fast, cheap)
high confidence + complex/multi  →  claude-sonnet-4-6  (current default)
low/medium confidence            →  claude-opus-4-8    (more reasoning power)
compliance mode                  →  claude-sonnet-4-6  (as today)
```

Where to wire it: `ask_grounded()` already receives `sheet_context` which carries
`confidence` and `question_type` from the router. Add a `_select_model()` helper there
that reads those fields and picks the tier. Keep the `ANTHROPIC_MODEL` env var as a
ceiling/override so ops can pin a specific model in production.

**Benefit:** Reduces cost/latency for routine lookups (~80% of traffic), and improves
answer quality on ambiguous questions that currently fall through with low confidence.

---

## 2. OpenAI fallback is missing retry logic and uses wrong temperature

**Current:** `_call_anthropic` has a 4-attempt exponential-backoff retry loop (lines 688–710).
`_call_openai` has zero retries and hardcodes `temperature=1` regardless of whether the
question is data-grounded.

**Idea:**

1. Add the same retry loop to `_call_openai` — transient 5xx/429 errors are as likely
   there as on Anthropic.
2. Thread `temperature` through `_call_openai` the same way it's passed to `_call_anthropic`,
   and compute `is_data_grounded` before the provider branch so the same `temp` is
   used on both paths (lines 782–793).

```python
# before the provider branch
temp = 0.0 if is_data_grounded else 0.3

if anthropic_key:
    return _call_anthropic(messages, model, anthropic_key, temperature=temp)

return _call_openai(messages, model, openai_key, temperature=temp)
```

---

## 3. Prompt caching for the system prompt

**Current:** Every request sends the full system prompt (grounded, compliance, or default)
from scratch. The grounded system prompt is ~300–400 tokens; the compliance system prompt
is much longer.

**Idea:** Use Anthropic's prompt caching. Mark the static system prompt block with
`"cache_control": {"type": "ephemeral"}`. Cache TTL is 5 minutes, so repeated questions
within a session hit the cache and skip the input-token billing and encoding latency for
the static portion.

Minimal change in `_call_anthropic`: convert the `system` string to a content array:

```python
payload = {
    ...
    "system": [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ],
}
```

No other changes needed. The Anthropic API accepts this format in place of the
plain string.

**Benefit:** Measurable latency reduction and ~60–90% input-token cost savings on the
system-prompt portion for back-to-back questions in the same session.

---

## 4. Token-budget-aware context truncation

**Current:** `route_question` returns a `token_estimate` (word count of the formatted
result), but nothing acts on it. A large result set — e.g., a `device_list` with hundreds
of rows — is sent verbatim into the LLM context. At some size this degrades answer quality
or hits `max_tokens` limits.

**Idea:** In `build_postgres_context` (or `format_results_for_llm`), check
`token_estimate` against a configurable `MAX_CONTEXT_TOKENS` threshold. If exceeded,
truncate the rows list and append a note like:

```
[Results truncated to 200 rows (N total). Ask a more specific question for full results.]
```

A reasonable initial threshold is 6,000 tokens (~24,000 characters), leaving headroom
for the system prompt and answer within the 8,192 `max_tokens` output budget.

---

## 5. Cache SQL results for repeated identical questions

**Current:** Two users asking "How many SN5610s are in EWR1?" within seconds of each other
each trigger a full SQL query + LLM call independently.

**Idea:** Add a short-TTL in-process cache keyed on `(normalized_question, site_id, upload_id)`.
A 60-second TTL handles burst traffic from the web UI (users clicking "ask again") without
serving stale data. The cache key should use the already-normalized question string from
`QuestionContext.normalized`.

```python
_ANSWER_CACHE: dict[tuple, tuple[dict, float]] = {}  # (key) → (result, ts)
_ANSWER_CACHE_TTL = 60  # seconds

def _cache_key(question: str, site_id: int, upload_id: int | None) -> tuple:
    normalized = " ".join(question.lower().split())
    return (normalized, site_id, upload_id)
```

Guard with `_state_lock` (already used elsewhere in `atlas_web_app.py` for shared dicts).

---

## 6. `general` fallback fires too broadly

**Current:** When no domain router matches, `question_type` becomes `"general"`, which
triggers `build_postgres_context_for_general()` — an expensive multi-query composite
context dump. The `general` type also gets `confidence="low"` but the prompt doesn't
meaningfully reflect this to the user.

**Idea:**

1. Before falling through to `general`, try a **semantic similarity step**: compare
   the unmatched question against a small set of representative question embeddings
   (one per question type). If a cosine similarity is above a threshold, route there
   with `confidence="medium"` and a note that it's a fuzzy match. This handles
   paraphrasings that the keyword lexicon misses without adding new routers.

2. Alternatively (simpler, no embeddings), **ask the LLM to classify** the question
   into a known `QUESTION_TYPES` string when confidence is low. This is a cheap
   single-message call with a constrained output (just the type string), which then
   feeds the normal SQL route. Classification models like Haiku are fast enough that
   this adds <200ms.

---

## 7. Compliance mode keyword detection is a blunt instrument

**Current:** `_is_compliance_question()` is a flat `any(token in q for token in keywords)`
scan. It shares none of the infrastructure with the intent router chain (no `QuestionContext`,
no confidence, no `matched_signals` metadata).

**Idea:** Add a `route_compliance_intent` function in `query_intent.py` and insert it at
the top of `_ROUTER_CHAIN` (or run it as a pre-check before the chain). This gives
compliance detection the same `token_set` intersection logic, confidence scoring, and
debug metadata as every other domain. It also consolidates all routing in one place
instead of having a parallel detection path in `demo_auth_ai.py`.

---

## 8. Surface routing metadata to the user

**Current:** The `confidence`, `reason`, and `matched_signals` fields from `IntentResult`
are attached to the LLM context header but are not shown in the web UI response to the
user. A user who asks an ambiguous question has no way to know if the router guessed or
was certain.

**Idea:** Return routing metadata alongside the answer in the API response JSON, and
render a small "debug panel" in the web UI (collapsed by default, expandable) showing:

```
Classified as: model_search (high confidence)
Matched signals: sn5610, how, many
SQL rows: 1 | Query: 23ms | Tokens: 127
```

This makes routing failures immediately visible during development and helps users
self-correct by rephrasing vague questions.

---

## Quick reference: where each idea touches the code

| Idea | Files to change |
|------|----------------|
| 1. Confidence-based model tiering | `demo_auth_ai.py:ask_grounded` |
| 2. OpenAI retry + temperature parity | `demo_auth_ai.py:_call_openai`, `ask_grounded` |
| 3. Prompt caching | `demo_auth_ai.py:_call_anthropic` |
| 4. Token-budget truncation | `atlas_postgres_context.py` or `atlas_query_router.py:format_results_for_llm` |
| 5. Answer cache | `atlas_postgres_context.py` or `atlas_web_app.py` |
| 6. `general` fallback improvement | `query_intent.py`, `atlas_postgres_context.py` |
| 7. Compliance detection unification | `query_intent.py`, `demo_auth_ai.py:_is_compliance_question` |
| 8. Surface routing metadata in UI | `atlas_web_app.py`, frontend template |

---

*Written: 2026-05-31. Based on reading of `demo_auth_ai.py`, `query_intent.py`,
`atlas_query_router.py`, `atlas_postgres_context.py`, `query_lexicon.py`.*
