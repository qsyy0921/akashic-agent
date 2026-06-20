# Source Snapshot

Implementation entry points:

- `eval/longmemeval/qa_runner.py`
  - sets benchmark-only question context before each QA turn.
- `eval/longmemeval/methods.py`
  - `set_benchmark_question_context`
  - `QuestionAwareStructuredRouterMemoryEngine`
  - `QuestionAwareStructuredRouterRecallTool`
  - `_question_aware_structured_search`
  - `_question_aware_structured_resolution`
  - `_question_aware_select_evidence_rows`
- `memory2/store.py`
  - `search_raw_events` supplies structured raw-event reads with optional
    `session_key`, `speaker_id`, and date filters.

Design boundary:

- Benchmark-only method wrapper.
- Does not change QQ, Telegram, MCP, browser, or production channel behavior.
- Reuses production `memory2` schema but changes answer-time retrieval policy
  only when the method config strategy is `question_aware_structured_router`.
