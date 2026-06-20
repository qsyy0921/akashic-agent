# method_12_question_aware_structured_router

Purpose: fix method_11's overly broad raw-event retrieval by letting the
answer-time recall tool see the original benchmark question and options before
it searches `memory_raw_events`.

Method 11 proved that directly querying production raw events is not enough:
the selected table was still too wide and noisy. Method 12 changes the
answer-time recall payload:

- Keep the adaptive retrieval route from method 06 for ordinary memory items.
- Reuse the production structured schema from method 10.
- Reuse method 11's full raw-event backfill, but search `memory_raw_events`
  with `session_key = lme:<question_id>` first.
- Use the original question/options, not only the model's generated
  `recall_memory` query.
- Remove generic words and option-name-only matches from candidate scoring.
- Return a compact `selected_evidence_table` capped at 8 rows by default.
- Emit `slot_decision` with selected candidate, supporting source refs,
  contradicting source refs, confidence, and evidence gap.

Paper basis:

- MemSearcher: construct compact question-relevant working memory instead of
  broad history context.
- MemGuide: fill missing slots with intent-aware retrieval rather than semantic
  similarity alone.
- OCR-Memory: preserve faithful source anchors and exact source windows.
- APEX-MEM and Memora/FAMA: preserve append-only raw events and resolve
  conflicts at query time.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_12_question_aware_structured_router `
  --limit 50 `
  --timeout 240
```

Current status:

- Implemented.
- Full 50-example SocialMemBench benchmark completed.
- Raw provider-polluted artifacts preserved:
  - `eval/results/memory_methods/method_12_question_aware_structured_router_raw_with_429.json`
  - `experiments/memory_methods/method_12_question_aware_structured_router/metrics_raw_with_429.json`
  - `experiments/memory_methods/method_12_question_aware_structured_router/error_cases_raw_with_429.json`
- Six Mimo provider-polluted answers were rerun and merged into the adjusted
  standard result.

Adjusted result:

| metric | value |
| --- | ---: |
| n | 50 |
| judge_acc | 0.7600 |
| F1 | 0.3059 |
| EM | 0.0000 |
| avg_elapsed_s | 19.1188 |
| avg_token_usage | 42599.64 |
| judge_false | 12 |

Per-type judge accuracy:

| type | judge_acc |
| --- | ---: |
| knowledge-update | 0.6364 |
| single-session-preference | 0.8824 |
| single-session-user | 0.7273 |

Interpretation:

- Positive relative to method_11: judge accuracy recovered from 70% to the 76%
  baseline, and `retrieval_failure` dropped from 8 to 4.
- Strongest effect is on single-session preference questions, improving to
  88.24%, above baseline and all previously recorded methods.
- Still not a breakthrough: it ties baseline and remains below the 82% best
  methods.
- Remaining errors show that question-aware retrieval is not enough for
  exception/negation and nuanced update trajectories. The next method should
  turn `slot_decision` into a stricter answer plan for high-confidence slots
  without widening raw-event context again.

Artifacts:

- `experiments/memory_methods/method_12_question_aware_structured_router/config.json`
- `experiments/memory_methods/method_12_question_aware_structured_router/source_snapshot/README.md`
- `eval/results/memory_methods/method_12_question_aware_structured_router.json`
- `experiments/memory_methods/method_12_question_aware_structured_router/metrics.json`
- `experiments/memory_methods/method_12_question_aware_structured_router/error_cases.json`
