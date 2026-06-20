# Source Snapshot

Method 11 implementation entry points:

- `memory2/store.py`
  - `search_raw_events`
  - `upsert_raw_events_from_messages`
- `eval/longmemeval/methods.py`
  - `StructuredCandidateResolverMemoryEngine`
  - `StructuredCandidateResolverRecallTool`
  - `_backfill_all_raw_events_to_structured_schema`
  - `_structured_candidate_search`
  - `_structured_candidate_resolution`
  - `_option_resolution_candidates`
- `eval/longmemeval/run_memory_method_batch.py`
  - default method list includes `method_11_structured_candidate_resolver`
- `tests/test_longmemeval_methods.py`
  - method 11 recall tool behavior test
- `tests/test_memory2_structured_schema.py`
  - raw-event schema search test
