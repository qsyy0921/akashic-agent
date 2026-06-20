# Source Snapshot

Implementation entry points:

- `eval/longmemeval/methods.py`
  - `ConsolidatedFactPrecisionMemoryEngine`
  - `ConsolidatedFactPrecisionRecallTool`
  - `_method15_candidate_precision_decision`
  - `_method15_session_disputed_rows`
  - `_method15_raw_row_score`

Config:

- `experiments/memory_methods/method_15_consolidated_fact_precision_rerank/config.json`

Design boundary:

- Benchmark-only method wrapper.
- Does not change QQ, Telegram, MCP, browser, or production channel behavior.
- Reuses Method 14's consolidated facts in the copied frozen benchmark workspace.
- Performs only a small session-scoped raw scan for disputed slots.
