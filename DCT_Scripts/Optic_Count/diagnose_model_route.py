#!/usr/bin/env python3
"""Trace the exact model_search routing pipeline step by step."""
import sys
sys.path.insert(0, "/app")

from query_intent import classify_with_context
from atlas_query_router import build_query_params, execute_query, format_results_for_llm, route_question
import query_extractors as ext

question = "How many SN5610s are there?"
site_id = 1
upload_id = 3

print(f"Question: {question!r}")
print(f"site_id={site_id}, upload_id={upload_id}")
print()

# Step 1: Classification
intent, ctx = classify_with_context(question)
print(f"1. classify_with_context:")
print(f"   question_type = {intent.question_type}")
print(f"   confidence    = {intent.confidence}")
print(f"   extracted_model = {ctx.extracted_model!r}")
print()

# Step 2: Extract model directly
raw_model = ext.extract_model(question)
print(f"2. extract_model = {raw_model!r}")
print()

# Step 3: Build params
params = build_query_params(question, intent.question_type, site_id, upload_id=upload_id, ctx=ctx)
print(f"3. build_query_params:")
for k, v in sorted(params.items()):
    print(f"   {k} = {v!r}")
print()

# Step 4: Check which SQL mode
mode = params.get("model_search_mode", "list")
print(f"4. model_search_mode = {mode!r}")
print()

# Step 5: Execute query
try:
    rows, elapsed = execute_query(intent.question_type, params)
    print(f"5. execute_query:")
    print(f"   rows returned = {len(rows)}")
    print(f"   elapsed = {elapsed}s")
    if rows:
        print(f"   first row = {rows[0]}")
    else:
        print(f"   (EMPTY - this is the bug)")
except Exception as e:
    print(f"5. execute_query FAILED: {e}")
    import traceback
    traceback.print_exc()
    rows = []

print()

# Step 6: Format
if rows:
    formatted = format_results_for_llm(intent.question_type, rows, question)
    print(f"6. format_results_for_llm:")
    print(f"   {formatted[:500]}")
print()

# Step 7: Full route_question
print("7. Full route_question result:")
result = route_question(question, site_id, upload_id=upload_id)
for k, v in sorted(result.items()):
    val_str = repr(v)
    if len(val_str) > 200:
        val_str = val_str[:200] + "..."
    print(f"   {k} = {val_str}")
