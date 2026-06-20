# method_14_consolidated_memory_write_quality

Purpose: test whether moving structure into memory write/consolidation works
better than adding more answer-time payload.

Method 13 fixed an isolated exception case, but full accuracy regressed because
the answer-time `answer_plan` made tool payloads larger and noisier. Method 14
keeps the best adaptive answer-time route from method_06 and adds a
benchmark-only consolidation/backfill step on the frozen workspace:

- Write compact `preference_fact`, `exception_fact`, `relationship_fact`,
  `decision_fact`, and `update_trajectory_fact` records from `memory_raw_events`.
- Store each fact as a normal `memory_items` row, so existing retrieval can find
  it.
- Preserve source refs, speaker, speaker_id, date, message_index, and quote in
  `extra_json`.
- Register a recall tool that shows a compact `consolidated_fact_table` first,
  then falls back to method_12 style question-aware raw-event evidence.
- Keep the write-side cap high enough to avoid truncating late-session facts;
  the answer-time payload still only returns the top compact facts.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_14_consolidated_memory_write_quality `
  --limit 50 `
  --timeout 240
```

Final 50-example SocialMemBench result:

| metric | value |
| --- | ---: |
| judge_acc | 0.8200 |
| F1 | 0.3157 |
| avg_elapsed_s | 18.4036 |
| avg_token_usage_est | 48646.60 |
| judge_false | 9 |
| judge_null | 0 |

Per-type judge accuracy:

| type | n | judge_acc | F1 |
| --- | ---: | ---: | ---: |
| knowledge-update | 11 | 0.8182 | 0.3383 |
| single-session-preference | 17 | 0.7647 | 0.2787 |
| single-session-user | 22 | 0.8636 | 0.3330 |

Error labels:

| label | count |
| --- | ---: |
| memory_update_not_versioned | 2 |
| personal_preference_attribution_error | 4 |
| retrieved_but_answer_wrong | 6 |
| retrieved_but_evidence_unused | 3 |

Conclusion:

- Positive result, but not a breakthrough: method_14 ties the current best
  0.8200 accuracy from methods 01/04/06 instead of exceeding it.
- Consolidated facts make the method faster than the previous best methods
  and recover method_13's regression from 0.7400 to 0.8200.
- The approach improves knowledge-update over method_04 and improves
  single-session-user over method_12/13, but preference accuracy remains below
  method_12's 0.8824.
- The remaining bottleneck is not broad retrieval coverage; it is final-answer
  precision over compact facts, especially preference attribution and cases
  where retrieved evidence is present but the wrong contrast is chosen.

Provider-cleanup notes:

- The raw full run hit two Mimo provider-polluted answers and one null judge.
- Raw artifacts are preserved as
  `method_14_consolidated_memory_write_quality_raw_with_429.json`,
  `metrics_raw_with_429.json`, and `error_cases_raw_with_429.json`.
- Only the affected cached case files were deleted and rerun with the same
  method config; the clean reruns were merged into
  `method_14_consolidated_memory_write_quality_adjusted.json`.
- The standard result path and `metrics.json` use the adjusted clean result.

Verification:

- Targeted smoke on `socialmem_Q2_e1f2a3b4` selects Sam through
  `candidate_resolution.decision_rule = consolidated_fact`.
- Unit and regression tests passed:
  `tests/test_longmemeval_methods.py`,
  `tests/test_memory2_structured_schema.py`,
  `tests/test_longmemeval_judge.py`.

Artifacts:

- `experiments/memory_methods/method_14_consolidated_memory_write_quality/config.json`
- `experiments/memory_methods/method_14_consolidated_memory_write_quality/source_snapshot/`
- `eval/results/memory_methods/method_14_consolidated_memory_write_quality.json`
- `experiments/memory_methods/method_14_consolidated_memory_write_quality/metrics.json`
- `experiments/memory_methods/method_14_consolidated_memory_write_quality/error_cases.json`
