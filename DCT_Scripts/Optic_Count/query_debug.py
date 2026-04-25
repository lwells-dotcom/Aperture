"""
query_debug.py - Optional classifier trace output for the Atlas query router.

Generates structured debug metadata showing why a question was classified
a particular way.  Useful for diagnosing misroutes and building test cases.
"""

from __future__ import annotations

from typing import Any, Dict

from query_intent import IntentResult, QuestionContext, build_context, classify_with_context


def debug_classify(question: str) -> Dict[str, Any]:
    """Classify a question and return full debug output.

    Returns a dict suitable for JSON serialization or logging:
    {
        "question": "...",
        "question_type": "...",
        "confidence": "high|medium|low",
        "matched_domain": "...",
        "reason": "...",
        "matched_signals": [...],
        "extractors": {
            "device": "...",
            "location": "...",
            "model": "...",
            "section": "...",
            "section_filter": "...",
            "optic": "...",
            "role": "...",
            "side": "...",
            "ip": "..."
        }
    }
    """
    result, ctx = classify_with_context(question)

    return {
        "question": question,
        "question_type": result.question_type,
        "confidence": result.confidence,
        "matched_domain": result.matched_domain,
        "reason": result.reason,
        "matched_signals": result.matched_signals,
        "extractors": {
            "device": ctx.extracted_device,
            "location": ctx.extracted_location or None,
            "model": ctx.extracted_model or None,
            "section": ctx.extracted_section or None,
            "section_filter": ctx.extracted_section_filter or None,
            "optic": ctx.extracted_optic or None,
            "role": ctx.extracted_role or None,
            "side": ctx.extracted_side or None,
            "ip": ctx.extracted_ip or None,
        },
    }
