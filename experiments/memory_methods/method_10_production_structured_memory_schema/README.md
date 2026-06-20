# method_10_production_structured_memory_schema

Purpose: move the source-grounded memory improvement out of benchmark-only
wrappers and into the production `memory2` object model.

This method is intentionally not another stronger prompt, reranker, or
tool-chain coercion layer. The method config only identifies the experiment to
the benchmark harness; `build_method_engine` leaves the engine unchanged because
the behavior is implemented in the production memory store and default memory
engine.

Implemented production changes:

- Add `memory_raw_events` for append-only source refs with `session_key`,
  `speaker_id`, `speaker`, `message_index` / `seq`, `timestamp`, `date`, and
  `content`.
- Add `memory_entities` for person/entity names and source refs.
- Add `memory_event_facts` for predicate/subject/value/time/source/confidence.
- Add `memory_assertions` for long-term memory summary, kind, validity state,
  `version_of`, and source refs.
- Add `memory_relation_facts` for person-person relation evidence.
- Sync structured projections when memory items are inserted, reinforced,
  superseded, replaced, merged, edited, or deleted.
- Backfill structured projections lazily when old frozen-memory workspaces are
  queried.
- Backfill authoritative raw message content from `SessionStore` during method
  evaluation so `memory_raw_events` contains speaker/content metadata, not only
  source_ref-derived ids.
- Attach the resulting evidence under `signals.structured_evidence` in
  `recall_memory` results without prefixing or rewriting the human-readable
  summary.

Paper basis:

- APEX-MEM: append-only temporal/entity/event memory.
- Memora/FAMA: explicit validity state and obsolete-memory handling.
- OCR-Memory: stable source anchors and faithful evidence recovery.
- MemGuide: slot-filled retrieval should use source-grounded evidence, not only
  semantic similarity.

Expected target:

- Reduce speaker-attribution and who-first errors by preserving
  `speaker_id`, `speaker`, `message_index`, and `source_ref` through the memory
  layer.
- Reduce update/version errors by giving each long-term assertion a validity
  state and optional `version_of`.
- Avoid the method_07 failure mode by keeping evidence in a separate signal
  rather than polluting summaries.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_10_production_structured_memory_schema `
  --limit 50 `
  --timeout 240
```

Current status:

- Production schema implementation added.
- Unit tests for schema creation, evidence projection, version sync, and default
  engine signal attachment pass.
- Full 50-example SocialMemBench evaluation completed.
- Raw run had two Mimo 429-polluted answers (`socialmem_Q5_r3e5f6a7`,
  `socialmem_Q6_a5s3c2`); both cases were rerun and merged into the adjusted
  result.

Results:

| result | judge_acc | F1 | avg_elapsed_s | note |
| --- | ---: | ---: | ---: | --- |
| raw full run | 0.6800 | 0.3184 | 18.4200 | contained two Mimo 429 polluted answers |
| adjusted full run | 0.7000 | 0.3328 | 18.5648 | polluted cases rerun and merged |

Per-type adjusted judge accuracy:

| type | judge_acc |
| --- | ---: |
| knowledge-update | 0.7273 |
| single-session-preference | 0.7647 |
| single-session-user | 0.6364 |

Interpretation:

Method 10 is an infrastructure success but an answer-quality negative result.
It proves the production store can preserve source-grounded raw events and
versionable assertions, and it avoids the summary-pollution failure from
method_07. However, simply exposing `signals.structured_evidence` still leaves
the final model free to pick the wrong candidate. Speaker attribution,
who-first ordering, and implicit preference inference remain weak. The next
method should query the structured tables directly and construct a compact
selected evidence table before final answer generation.

Artifacts:

- `experiments/memory_methods/method_10_production_structured_memory_schema/config.json`
- `experiments/memory_methods/method_10_production_structured_memory_schema/source_snapshot/README.md`
- `eval/results/memory_methods/method_10_production_structured_memory_schema.json`
- `eval/results/memory_methods/method_10_production_structured_memory_schema_adjusted.json`
- `eval/results/memory_methods/method_10_production_structured_memory_schema_raw_with_429.json`
- `eval/results/memory_methods/method_10_production_structured_memory_schema_rerun_q5_r3e5f6a7.json`
- `eval/results/memory_methods/method_10_production_structured_memory_schema_rerun_q6_a5s3c2.json`
- `experiments/memory_methods/method_10_production_structured_memory_schema/metrics.json`
- `experiments/memory_methods/method_10_production_structured_memory_schema/error_cases.json`
- `experiments/memory_methods/method_10_production_structured_memory_schema/metrics_raw_with_429.json`
- `experiments/memory_methods/method_10_production_structured_memory_schema/error_cases_raw_with_429.json`
