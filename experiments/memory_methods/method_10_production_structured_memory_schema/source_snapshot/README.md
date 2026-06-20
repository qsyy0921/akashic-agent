# Source Snapshot

Method 10 is implemented in production memory code rather than a benchmark-only
wrapper.

Primary files:

- `memory2/store.py`
  - structured tables: `memory_raw_events`, `memory_entities`,
    `memory_event_facts`, `memory_assertions`, `memory_relation_facts`
  - projection sync: `_sync_structured_item_by_id`,
    `_sync_structured_item`, `_delete_structured_items`
  - query API: `get_structured_evidence_for_items`
- `plugins/default_memory/engine.py`
  - `_attach_structured_evidence`
  - query paths attach `signals.structured_evidence`
- `eval/longmemeval/methods.py`
  - recognizes `production_structured_memory_schema` as a no-wrapper
    production-schema method
- `tests/test_memory2_structured_schema.py`
  - schema, projection, versioning, and default-engine signal tests
