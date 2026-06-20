# method_11_structured_candidate_resolver

Purpose: consume the production `memory2` structured schema during candidate
selection instead of merely exposing `signals.structured_evidence` to the final
model.

Method 10 proved the schema can preserve raw source evidence, but the final
answer path still ignored or misused that evidence. Method 11 changes the
answer-time recall payload:

- Keep the adaptive retrieval route from method 06 for ordinary memory items.
- Backfill all benchmark user messages into `memory_raw_events`, not just
  source refs already attached to memory items.
- Query `memory_raw_events` directly for slot-specific evidence rows.
- Build a compact `selected_evidence_table` with `speaker_id`, `speaker`,
  `message_index`, `seq`, `date`, `source_ref`, matched slots, and quote.
- Produce `candidate_resolution` before final generation for speaker,
  who-first, exception, implicit-preference, option-person, and update slots.
- Do not rewrite memory summaries.
- Do not wrap or constrain `search_messages` / `fetch_messages`.

Paper basis:

- APEX-MEM: append-only temporal/entity/event memory.
- MemSearcher: compact working memory built for the current question.
- OCR-Memory: stable source anchors and faithful evidence recovery.
- MemGuide: slot-specific retrieval and missing-slot filtering.
- Memora/FAMA: validity/version awareness for stale or updated memory.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_11_structured_candidate_resolver `
  --limit 50 `
  --timeout 240
```

Current status:

- Implemented.
- Full 50-example SocialMemBench benchmark completed.
- Raw provider-polluted artifacts preserved:
  - `eval/results/memory_methods/method_11_structured_candidate_resolver_raw_with_429.json`
  - `experiments/memory_methods/method_11_structured_candidate_resolver/metrics_raw_with_429.json`
  - `experiments/memory_methods/method_11_structured_candidate_resolver/error_cases_raw_with_429.json`
- Four Mimo provider-polluted answers were rerun and merged into the adjusted
  standard result.

Adjusted result:

| metric | value |
| --- | ---: |
| n | 50 |
| judge_acc | 0.7000 |
| F1 | 0.3223 |
| EM | 0.0000 |
| avg_elapsed_s | 18.5076 |
| avg_token_usage | 41335.90 |
| judge_false | 15 |

Per-type judge accuracy:

| type | judge_acc |
| --- | ---: |
| knowledge-update | 0.6364 |
| single-session-preference | 0.8235 |
| single-session-user | 0.6364 |

Interpretation:

- Negative quality result: method_11 ties method_10 at 70%, below the 76%
  baseline and the current 82% best methods.
- It successfully turns provider-polluted rerun cases into valid judged answers,
  but the architecture still does not improve full-run accuracy.
- The selected evidence payload is too wide/noisy for many questions
  (`avg_tool_result_chars` is about 58.7k), and the final model can still ignore
  ordering, attribution, or implicit-preference evidence.
- The next method should use the current user question itself, not only the
  model's generated recall query, to build a smaller session/entity-scoped
  evidence table before final answering.

Artifacts:

- `experiments/memory_methods/method_11_structured_candidate_resolver/config.json`
- `experiments/memory_methods/method_11_structured_candidate_resolver/source_snapshot/README.md`
- `eval/results/memory_methods/method_11_structured_candidate_resolver.json`
- `experiments/memory_methods/method_11_structured_candidate_resolver/metrics.json`
- `experiments/memory_methods/method_11_structured_candidate_resolver/error_cases.json`
