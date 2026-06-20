# Akashic Memory Method Experiments

This directory keeps reproducible memory-method experiments for the Akashic
personal assistant benchmark.

Each method owns:

- `README.md`: method motivation and run command
- `config.json`: benchmark method switch
- `source_snapshot/`: code entry point or implementation reference
- `metrics.json`: generated after summarizing a run
- `run.log`: raw run log or placeholder
- `error_cases.json`: generated error analysis

Default SocialMemBench command shape:

1. Prepare a frozen-memory workspace copied from the baseline ingest:

```powershell
python -m eval.longmemeval.prepare_method_workspace `
  --source-workspace runtime/eval/socialmem_mimo_50 `
  --target-workspace runtime/eval/memory_methods/<method_id> `
  --archive-existing
```

2. Run QA and judge on the copied memory with the method wrapper enabled:

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:MIMO_API_KEY = [Environment]::GetEnvironmentVariable("MIMO_API_KEY", "User")

python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/<method_id> `
  --output eval/results/memory_methods/<method_id>.json `
  --limit 50 `
  --qa-only `
  --resume-auto `
  --timeout 240 `
  --method-config experiments/memory_methods/<method_id>/config.json
```

This frozen-memory mode is the default comparison protocol for methods that
only change retrieval, schema annotation, reranking, or answer-time version
selection. It avoids mixing method effects with repeated ingest/consolidation
variance.

For `baseline_current`, omit `--method-config` or use its config; both mean no
strategy wrapper.

After a run:

```powershell
python -m eval.longmemeval.summarize_method_results `
  --result eval/results/memory_methods/<method_id>.json `
  --method-dir experiments/memory_methods/<method_id> `
  --method-id <method_id> `
  --run-log eval/runs/memory_methods/<method_id>.log
```

Batch mode for sequential method runs:

```powershell
python -m eval.longmemeval.run_memory_method_batch `
  --methods method_02_structured_memory_schema method_03_evidence_fetch_rerank method_04_memory_update_versioning method_05_hybrid_intent_temporal_rerank method_06_adaptive_intent_versioned method_07_source_grounded_slot_resolver method_08_raw_message_first_resolver method_09_deterministic_attribution_resolver method_10_production_structured_memory_schema method_11_structured_candidate_resolver method_12_question_aware_structured_router method_13_slot_decision_answer_planner method_14_consolidated_memory_write_quality method_15_consolidated_fact_precision_rerank method_16_conservative_precision_gate method_17_structured_answer_contract `
  --limit 50
```

Current 50-example SocialMemBench comparison:

| method | judge_acc | F1 | avg_elapsed_s | outcome |
| --- | ---: | ---: | ---: | --- |
| baseline_current | 0.7600 | 0.3103 | 22.5652 | baseline |
| method_01_intent_aware_retrieval | 0.8200 | 0.3221 | 38.6134 | improves accuracy, high latency |
| method_02_structured_memory_schema | 0.6600 | 0.3065 | 31.1114 | regresses alone |
| method_03_evidence_fetch_rerank | 0.7400 | 0.3070 | 30.5268 | slightly below baseline |
| method_04_memory_update_versioning | 0.8200 | 0.3078 | 27.5494 | improves accuracy with lower latency than method_01 |
| method_05_hybrid_intent_temporal_rerank | 0.7600 | 0.3221 | 37.5998 | negative result; hybrid rerank hurts attribution |
| method_06_adaptive_intent_versioned | 0.8200 | 0.2889 | 36.0494 | ties best overall; best knowledge-update accuracy |
| method_07_source_grounded_slot_resolver | 0.7000 | 0.2967 | 30.6364 | negative result; source hints in summaries hurt option reasoning |
| method_09_deterministic_attribution_resolver | 0.6600 | 0.2850 | 27.9876 | negative result; targeted fix did not generalize |
| method_10_production_structured_memory_schema | 0.7000 | 0.3328 | 18.5648 | production schema landed, but answer-time use is still weak |
| method_11_structured_candidate_resolver | 0.7000 | 0.3223 | 18.5076 | negative result; direct raw-event candidate resolver still too noisy |
| method_12_question_aware_structured_router | 0.7600 | 0.3059 | 19.1188 | recovers baseline; preference improves, but no overall breakthrough |
| method_13_slot_decision_answer_planner | 0.7400 | 0.3164 | 19.6274 | negative result; targeted exception fix did not generalize |
| method_14_consolidated_memory_write_quality | 0.8200 | 0.3157 | 18.4036 | positive result; ties best accuracy with lower latency |
| method_15_consolidated_fact_precision_rerank | 0.7400 | 0.3004 | 16.6288 | negative result; broad precision rerank regresses more than it fixes |
| method_16_conservative_precision_gate | 0.7400 | 0.3180 | 20.1548 | negative result; user questions improve but knowledge-update collapses |
| method_17_structured_answer_contract | 0.8800 | 0.2992 | 22.8964 | positive result; first method to break the 82% ceiling |

`method_07_source_grounded_slot_resolver` hit one transient Mimo 429 during the
raw run. The contaminated answer is preserved in
`metrics_raw_with_429.json` / `error_cases_raw_with_429.json`; the comparison
table uses the adjusted result after a clean single-case rerun.

`method_08_raw_message_first_resolver` is implemented and smoke-tested, but is
not yet in the full 50-example comparison table. Its targeted check on
`socialmem_Q4_d4e5f6a7` still failed even though the raw evidence table and
candidate hints identified Jordan. This points to the next bottleneck:
speaker attribution needs a deterministic resolver or structured
event/entity/source schema, not more model-facing hints.

`method_09_deterministic_attribution_resolver` was evaluated on the full 50
examples. It fixed the targeted `socialmem_Q4_d4e5f6a7` case in one isolated
retry by constraining recall/search/fetch around the selected source_ref, but
the full run regressed to 66% judge accuracy. The result shows that
benchmark-level tool-chain coercion is brittle: it can repair one
speaker-attribution path while harming knowledge-update and richer preference
questions. The next direction should be a production event/entity/source memory
schema, not stronger wrapper prompts.

`method_10_production_structured_memory_schema` moved that schema into
production `memory2`: raw events, entities, event facts, assertions, relation
facts, validity state, source refs, and raw-message backfill from `SessionStore`.
It also exposes `signals.structured_evidence` without rewriting summaries. The
full adjusted result is 70%: better than method_09, but still below the 76%
baseline and the 82% best methods. The remaining failures show that schema
availability is not enough; the next method must actually use the schema for
slot-specific candidate extraction, source-grounded ordering, and version
selection before final generation.

`method_11_structured_candidate_resolver` does consume `memory_raw_events`
directly and builds a selected evidence table before final generation, but the
full adjusted result is still 70%. This is a useful negative result: simply
moving from visible schema signals to direct raw-event candidate search does not
solve attribution or update reasoning if the evidence table is too broad and the
tool only sees the model's generated recall query. The next method should be
question-aware and narrower: use the original benchmark question/options, infer
session/entity scope first, remove generic terms, and return only a compact
4-8 row evidence set.

`method_12_question_aware_structured_router` implements that narrower route. It
passes the original benchmark question/options into the recall tool, searches
`memory_raw_events` with a `session_key` filter first, removes generic and
option-name-only matches, and returns `slot_decision` plus a smaller selected
evidence table. The adjusted full result is 76%, recovering the baseline and
improving over method_11 by 6 points. It also reduces retrieval failures from 8
to 4 and reaches the best recorded single-session-preference accuracy so far
(88.24%). It still does not beat the 82% best methods because exception,
negation, and nuanced update cases remain vulnerable to final-answer misuse of
the retrieved evidence.

`method_13_slot_decision_answer_planner` turns `slot_decision` into an explicit
`answer_plan` with per-option support rows, ordered evidence, update evidence,
and final-answer constraints. It fixed the targeted
`socialmem_Q2_e1f2a3b4` exception case after aligning the planner's selected
answer back into `candidate_resolution`, but the full run regressed to 74%.
The main lesson is that thickening answer-time payloads increases noise and
token cost: `avg_tool_result_chars` rose to 82610.56 and preference accuracy
fell from method_12's 0.8824 to 0.7647. The next direction should improve
memory write/consolidation quality rather than adding more answer-time wrapper
logic.

`method_14_consolidated_memory_write_quality` tests that direction by writing
compact `preference_fact`, `exception_fact`, `relationship_fact`,
`decision_fact`, and `update_trajectory_fact` records into the copied frozen
workspace before answer-time retrieval. It recovers method_13's regression and
ties the best recorded accuracy at 82%, while reducing average latency to
18.4036s. It is still not the success threshold because it does not exceed 82%.
The remaining 9 errors are mostly `retrieved_but_answer_wrong`,
`personal_preference_attribution_error`, and `retrieved_but_evidence_unused`,
so the next method should improve compact-fact precision and final-answer
selection rather than broadening retrieval.

`method_15_consolidated_fact_precision_rerank` tried that compact-fact
precision path by grouping consolidated facts and a small disputed raw scan by
candidate. It fixed `socialmem_Q4_d4e5f6a7`, `socialmem_Q8_v4s4c1`, and
`socialmem_Q6_a5s3c2`, but it regressed 7 cases that method_14 had answered
correctly. The adjusted full result is 74%, so the lesson is that candidate
reranking cannot be exposed broadly as another answer-time payload; it needs a
stricter answer contract or verifier.

`method_16_conservative_precision_gate` narrowed Method 15 to option-style
speaker, who-first, and explicit norm-exception questions. It also fixed the
who-first tie-break by using global `source_ref` order before local
`message_index`. Targeted smoke improved the intended cases, but the full run
still landed at 74%. Its single-session-user accuracy is strong at 86.36%, but
knowledge-update drops to 63.64%, so conservative gating alone is not enough.
The next method should avoid letting the final model freely reinterpret
retrieved facts and should produce a small structured final-answer contract
with source-backed verification.

`method_17_structured_answer_contract` implements that narrower final-answer
control. It keeps Method 14's compact facts, refuses broad answer-time
candidate tables, and only emits a tiny verified `final_answer_contract` for
high-confidence option slots. The clean adjusted full run reaches 88% judge
accuracy, the first result above the 82% ceiling. It improves especially on
`single-session-user` (95.45%) and keeps `knowledge-update` strong (90.91%),
while `single-session-preference` remains the main weak area at 76.47%.
Only two contracts fired in the clean run, both correct and source-backed:
Jordan's quiet-venue attribution and Vera's council-norm exception. The
remaining errors show the next bottleneck is implicit preference inference and
fine-grained trajectory interpretation, not broad retrieval.

The full machine-readable comparison is in:

- `eval/results/memory_methods/comparison.json`
- `eval/results/memory_methods/comparison.md`

Interview-oriented analysis is in:

- `interview/memory_evaluation_report.md`
