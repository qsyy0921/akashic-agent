# Source Snapshot

Implementation entry points:

- `eval/longmemeval/methods.py`
  - `SlotDecisionAnswerPlannerMemoryEngine`
  - `SlotDecisionAnswerPlannerRecallTool`
  - `_slot_decision_plan_expand_rows`
  - `_slot_decision_answer_plan`
  - `_answer_plan_aligned_resolution`
  - `_answer_plan_per_option_evidence`
  - `_answer_plan_ordered_evidence`
  - `_answer_plan_update_evidence`
- `eval/longmemeval/qa_runner.py`
  - benchmark-only question context for original question/options.
- `memory2/store.py`
  - `search_raw_events` for session-scoped structured raw-event lookup.

Design boundary:

- Benchmark-only method wrapper.
- Does not change QQ, Telegram, MCP, browser, or production channel behavior.
- Reuses production `memory2` raw-event schema and method_12 question context.
- Does not rewrite memory summaries or constrain `search_messages` /
  `fetch_messages`.

Snapshot files:

- `methods.py`: copied implementation snapshot after the full method_13 run.
- `test_longmemeval_methods.py`: copied regression-test snapshot after the
  full method_13 run.
