# method_13_slot_decision_answer_planner

Purpose: move beyond method_12's retrieval-only improvement by turning
`slot_decision` into a stricter `answer_plan` before final generation.

Method 12 recovered the baseline and improved preference questions, but the
remaining failures show that retrieved evidence is still misused by the final
answer step. Method 13 keeps method_12's question-aware structured retrieval
and adds:

- `answer_plan.selected_answer` only when confidence is high or medium.
- `per_option_evidence` with support/missing rows for options.
- `ordered_evidence` sorted by `message_index` for who-first/order cases.
- `old_evidence` and `new_evidence` for update/change trajectory cases.
- `final_answer_constraints` that state exactly what the final answer must
  cover.
- Extra session-scoped option searches for answer planning, without rewriting
  memory summaries or wrapping `search_messages` / `fetch_messages`.

Paper basis:

- MemGuide: slot filling and missing-slot filtering.
- MemSearcher: compact question-specific working memory.
- OCR-Memory: source anchors and faithful evidence recovery.
- APEX-MEM / Memora-FAMA: query-time conflict and version resolution.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_13_slot_decision_answer_planner `
  --limit 50 `
  --timeout 240
```

Current status:

- Implemented and evaluated on the full 50-example SocialMemBench slice.
- Python compile check passed for the changed evaluation files.
- Unit/regression checks passed:
  - `pytest -q -c NUL -p no:cacheprovider tests\test_longmemeval_methods.py`
  - `pytest -q -c NUL -p no:cacheprovider tests\test_longmemeval_methods.py tests\test_memory2_structured_schema.py tests\test_longmemeval_judge.py`
- Targeted smoke on `socialmem_Q2_e1f2a3b4` now selects Sam consistently in
  `answer_plan`, `candidate_resolution`, and the final answer.
- Full benchmark result is a negative result: `judge_acc=0.7400`, below the
  `baseline_current=0.7600` and below the 0.8200 best methods.

Full-run metrics:

| metric | value |
| --- | ---: |
| n | 50 |
| judge_acc | 0.7400 |
| delta_vs_baseline | -0.0200 |
| F1 | 0.3164 |
| avg_elapsed_s | 19.6274 |
| avg_token_usage_est | 53384.12 |
| knowledge-update | 0.6364 |
| single-session-preference | 0.7647 |
| single-session-user | 0.7727 |

Failure counts:

| label | count |
| --- | ---: |
| memory_write_failure | 8 |
| memory_update_not_versioned | 4 |
| personal_preference_attribution_error | 4 |
| retrieval_failure | 1 |
| retrieved_but_evidence_unused | 4 |

Expected improvement target:

- Improve method_12's remaining exception/negation and who-first failures.
- Preserve method_12's stronger single-session-preference behavior.
- Avoid method_09-style hard wrapper coercion by making `selected_answer`
  conditional on evidence confidence.

Observed outcome:

- The targeted exception/negation failure was fixed after aligning
  `answer_plan.selected_answer` back into `candidate_resolution`.
- The full result regressed because the extra answer-plan expansion increased
  `avg_tool_result_chars` from method_12's 60520.86 to 82610.56 and increased
  `avg_token_usage_est` from 42599.64 to 53384.12.
- Preference accuracy dropped from method_12's 0.8824 to 0.7647, while
  single-session-user rose from 0.7273 to 0.7727. Knowledge-update stayed at
  0.6364.
- This suggests the next bottleneck is not answer-time evidence formatting.
  The durable memory itself needs better consolidation of exceptions,
  preference attribution, relationships, and update trajectories.

Artifacts:

- `experiments/memory_methods/method_13_slot_decision_answer_planner/config.json`
- `experiments/memory_methods/method_13_slot_decision_answer_planner/source_snapshot/README.md`
- `experiments/memory_methods/method_13_slot_decision_answer_planner/source_snapshot/methods.py`
- `experiments/memory_methods/method_13_slot_decision_answer_planner/source_snapshot/test_longmemeval_methods.py`
- `eval/results/memory_methods/method_13_slot_decision_answer_planner.json`
- `experiments/memory_methods/method_13_slot_decision_answer_planner/metrics.json`
- `experiments/memory_methods/method_13_slot_decision_answer_planner/error_cases.json`
