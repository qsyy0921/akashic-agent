# Source Snapshot

Implementation entry points:

- `eval/longmemeval/methods.py`
  - `ConsolidatedMemoryWriteQualityEngine`
  - `ConsolidatedMemoryWriteQualityRecallTool`
  - `_backfill_method14_consolidated_facts`
  - `_method14_build_consolidated_facts`
  - `_method14_search_consolidated_facts`

Config:

- `experiments/memory_methods/method_14_consolidated_memory_write_quality/config.json`

Snapshot files:

- `methods.py`
- `test_longmemeval_methods.py`

Design boundary:

- Benchmark-only method wrapper.
- Does not change QQ, Telegram, MCP, browser, or production channel behavior.
- Writes consolidated facts into the copied frozen benchmark workspace only.
- Does not add wider answer-time raw-event tables.
