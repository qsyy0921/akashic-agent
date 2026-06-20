# Akashic Personal Assistant Memory Evaluation

## Scope

This report tracks algorithm-related memory experiments for Akashic as a
personal assistant. It intentionally excludes QQ/Telegram connectivity, MCP
tools, browser automation, and UI deployment.

Primary dataset:

- SocialMemBench full local export:
  `eval/agent_memory_bench_socialmem_full_local.json`
- Primary development comparison limit: 50 examples
- Larger validation run: 200 examples for
  `method_17_structured_answer_contract`
- Model/Judge: `mimo-v2.5-pro`

## Baseline Pipeline

Current benchmark path:

1. Replay haystack conversations into `SessionStore`.
2. Run markdown consolidation and memory2 post-response extraction.
3. Ask the agent the benchmark question.
4. Agent must call `recall_memory`; it may also call `search_messages` and
   `fetch_messages`.
5. Score with F1, EM, and Mimo LLM-as-judge.

## Baseline Result

Frozen baseline artifact:

- Metrics: `experiments/memory_methods/baseline_current/metrics.json`
- Error cases: `experiments/memory_methods/baseline_current/error_cases.json`
- Comparison table: `eval/results/memory_methods/comparison.md`

Current 50-example SocialMemBench baseline:

| metric | value |
| --- | ---: |
| n | 50 |
| judge_acc | 0.7600 |
| F1 | 0.3103 |
| EM | 0.0000 |
| avg_elapsed_s | 22.5652 |
| judge_false | 12 |

Per type:

| type | n | judge_acc | F1 |
| --- | ---: | ---: | ---: |
| knowledge-update | 11 | 0.7273 | 0.3505 |
| single-session-preference | 17 | 0.8235 | 0.2715 |
| single-session-user | 22 | 0.7273 | 0.3202 |

Current error labels from the baseline summary:

| error type | count |
| --- | ---: |
| memory_write_failure | 9 |
| memory_update_not_versioned | 3 |
| personal_preference_attribution_error | 3 |
| retrieved_but_answer_wrong | 2 |
| retrieved_but_evidence_unused | 1 |

Note: the frozen baseline result was produced before `react_stats` was written
into result JSON, so its `avg_token_usage` remains `null`. New method runs now
persist agent-loop token estimates under `result.react_stats`, and the summary
script records `avg_token_usage` from `turn_input_sum_tokens` with
`avg_token_usage_source = react_stats.turn_input_sum_tokens estimate`.

## Implemented Method Switch

Benchmark-only strategy wrappers live in:

- `eval/longmemeval/methods.py`

The benchmark runtime accepts:

```powershell
--method-config experiments/memory_methods/<method_id>/config.json
```

This patches both:

- explicit `recall_memory` / `memorize` / `forget_memory` tool binding
- implicit retrieval pipeline used for prompt memory injection

Production QQ/Telegram/MCP/browser services are not touched.

## Method Registry

| method | strategy | status |
| --- | --- | --- |
| baseline_current | baseline | metrics recorded |
| method_01_intent_aware_retrieval | intent_aware_retrieval | full 50 recorded |
| method_02_structured_memory_schema | structured_memory_schema | full 50 recorded |
| method_03_evidence_fetch_rerank | evidence_fetch_rerank | full 50 recorded |
| method_04_memory_update_versioning | memory_update_versioning | full 50 recorded |
| method_05_hybrid_intent_temporal_rerank | hybrid_intent_temporal_rerank | full 50 recorded |
| method_06_adaptive_intent_versioned | adaptive_intent_versioned | full 50 recorded |
| method_07_source_grounded_slot_resolver | source_grounded_slot_resolver | full 50 recorded; negative result |
| method_08_raw_message_first_resolver | raw_message_first_resolver | implemented; smoke only, not in full comparison |
| method_09_deterministic_attribution_resolver | deterministic_attribution_resolver | full 50 recorded; negative result |
| method_10_production_structured_memory_schema | production_structured_memory_schema | full 50 recorded; infrastructure result |
| method_11_structured_candidate_resolver | structured_candidate_resolver | full 50 recorded; negative result |
| method_12_question_aware_structured_router | question_aware_structured_router | full 50 recorded; recovers baseline |
| method_13_slot_decision_answer_planner | slot_decision_answer_planner | full 50 recorded; negative result |
| method_14_consolidated_memory_write_quality | consolidated_memory_write_quality | full 50 recorded; ties best result |
| method_15_consolidated_fact_precision_rerank | consolidated_fact_precision_rerank | full 50 recorded; negative result |
| method_16_conservative_precision_gate | conservative_precision_gate | full 50 recorded; negative result |
| method_17_structured_answer_contract | structured_answer_contract | full 50 recorded; 200-example validation recorded |

All full-recorded methods were evaluated with the same frozen baseline memory workspace,
same 50 SocialMemBench examples, same Mimo model, and the same QA+judge path.
The frozen-memory protocol isolates answer-time memory strategy effects from
ingest/consolidation variance.

## Method Results

| method | judge_acc | delta | F1 | avg_elapsed_s | avg_token_usage |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline_current | 0.7600 | - | 0.3103 | 22.5652 | null |
| method_01_intent_aware_retrieval | 0.8200 | +0.0600 | 0.3221 | 38.6134 | 36199.62 |
| method_02_structured_memory_schema | 0.6600 | -0.1000 | 0.3065 | 31.1114 | 32843.56 |
| method_03_evidence_fetch_rerank | 0.7400 | -0.0200 | 0.3070 | 30.5268 | 38942.50 |
| method_04_memory_update_versioning | 0.8200 | +0.0600 | 0.3078 | 27.5494 | 34880.10 |
| method_05_hybrid_intent_temporal_rerank | 0.7600 | +0.0000 | 0.3221 | 37.5998 | 34914.34 |
| method_06_adaptive_intent_versioned | 0.8200 | +0.0600 | 0.2889 | 36.0494 | 40883.56 |
| method_07_source_grounded_slot_resolver | 0.7000 | -0.0600 | 0.2967 | 30.6364 | 33733.76 |
| method_09_deterministic_attribution_resolver | 0.6600 | -0.1000 | 0.2850 | 27.9876 | 67537.26 |
| method_10_production_structured_memory_schema | 0.7000 | -0.0600 | 0.3328 | 18.5648 | 34293.36 |
| method_11_structured_candidate_resolver | 0.7000 | -0.0600 | 0.3223 | 18.5076 | 41335.90 |
| method_12_question_aware_structured_router | 0.7600 | +0.0000 | 0.3059 | 19.1188 | 42599.64 |
| method_13_slot_decision_answer_planner | 0.7400 | -0.0200 | 0.3164 | 19.6274 | 53384.12 |
| method_14_consolidated_memory_write_quality | 0.8200 | +0.0600 | 0.3157 | 18.4036 | 48646.60 |
| method_15_consolidated_fact_precision_rerank | 0.7400 | -0.0200 | 0.3004 | 16.6288 | 49419.94 |
| method_16_conservative_precision_gate | 0.7400 | -0.0200 | 0.3180 | 20.1548 | 52555.80 |
| method_17_structured_answer_contract | 0.8800 | +0.1200 | 0.2992 | 22.8964 | 50340.80 |

Per-type judge accuracy:

| method | knowledge-update | preference | user |
| --- | ---: | ---: | ---: |
| baseline_current | 0.7273 | 0.8235 | 0.7273 |
| method_01_intent_aware_retrieval | 0.7273 | 0.7647 | 0.9091 |
| method_02_structured_memory_schema | 0.7273 | 0.5882 | 0.6818 |
| method_03_evidence_fetch_rerank | 0.7273 | 0.6471 | 0.8182 |
| method_04_memory_update_versioning | 0.6364 | 0.8824 | 0.8636 |
| method_05_hybrid_intent_temporal_rerank | 0.7273 | 0.8824 | 0.6818 |
| method_06_adaptive_intent_versioned | 0.9091 | 0.7647 | 0.8182 |
| method_07_source_grounded_slot_resolver | 0.6364 | 0.7059 | 0.7273 |
| method_09_deterministic_attribution_resolver | 0.3636 | 0.8235 | 0.6818 |
| method_10_production_structured_memory_schema | 0.7273 | 0.7647 | 0.6364 |
| method_11_structured_candidate_resolver | 0.6364 | 0.8235 | 0.6364 |
| method_12_question_aware_structured_router | 0.6364 | 0.8824 | 0.7273 |
| method_13_slot_decision_answer_planner | 0.6364 | 0.7647 | 0.7727 |
| method_14_consolidated_memory_write_quality | 0.8182 | 0.7647 | 0.8636 |
| method_15_consolidated_fact_precision_rerank | 0.9091 | 0.6471 | 0.7273 |
| method_16_conservative_precision_gate | 0.6364 | 0.6471 | 0.8636 |
| method_17_structured_answer_contract | 0.9091 | 0.7647 | 0.9545 |

Error label counts:

| method | write fail | update not versioned | pref attribution | retrieved wrong | evidence unused |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline_current | 9 | 3 | 3 | 2 | 1 |
| method_01_intent_aware_retrieval | 7 | 3 | 4 | 2 | 0 |
| method_02_structured_memory_schema | 12 | 3 | 7 | 4 | 1 |
| method_03_evidence_fetch_rerank | 9 | 3 | 6 | 4 | 0 |
| method_04_memory_update_versioning | 6 | 4 | 2 | 2 | 1 |
| method_05_hybrid_intent_temporal_rerank | 8 | 3 | 2 | 3 | 1 |
| method_06_adaptive_intent_versioned | 6 | 1 | 4 | 1 | 2 |
| method_07_source_grounded_slot_resolver | 8 | 4 | 5 | 3 | 0 |
| method_09_deterministic_attribution_resolver | 5 | 7 | 3 | 5 | 2 |
| method_10_production_structured_memory_schema | 11 | 3 | 4 | 1 | 3 |
| method_11_structured_candidate_resolver | 5 | 4 | 3 | 0 | 2 |
| method_12_question_aware_structured_router | 4 | 4 | 2 | 1 | 3 |
| method_13_slot_decision_answer_planner | 8 | 4 | 4 | 0 | 4 |
| method_14_consolidated_memory_write_quality | 0 | 2 | 4 | 6 | 3 |
| method_15_consolidated_fact_precision_rerank | 0 | 1 | 6 | 3 | 10 |
| method_16_conservative_precision_gate | 0 | 4 | 6 | 4 | 9 |
| method_17_structured_answer_contract | 0 | 1 | 4 | 1 | 5 |

## Larger SocialMemBench Validation

The 50-example method table is useful for fast ablation, but it is a small
development slice. I expanded the current best method,
`method_17_structured_answer_contract`, to the first 200 SocialMemBench
personal-memory examples.

Run artifacts:

- Result: `eval/results/personal_memory/method_17_socialmem_200.json`
- Metrics: `experiments/personal_memory/method_17_socialmem_200/metrics.json`
- Error cases: `experiments/personal_memory/method_17_socialmem_200/error_cases.json`
- Experiment notes: `experiments/personal_memory/method_17_socialmem_200/README.md`

The baseline memory workspace was first ingested with 200 examples, then copied
as a frozen method workspace before QA-only evaluation. Mimo rate limits made
the first 4-worker and 2-worker QA attempts unusable because they produced
provider-polluted answers. The official clean 200-example result used one
worker plus provider throttling:

- `AKASHIC_LLM_MAX_RETRIES=6`
- `AKASHIC_LLM_RETRY_BASE_DELAY_S=10`
- `AKASHIC_LLM_RETRY_MAX_DELAY_S=90`
- `AKASHIC_LLM_MIN_INTERVAL_S=4`

Clean 200-example result:

| metric | value |
| --- | ---: |
| n | 200 |
| judge_acc | 0.7100 |
| F1 | 0.2988 |
| EM | 0.0000 |
| runtime_errors | 0 |
| judge_null | 0 |
| provider_polluted_answers | 0 |
| avg_elapsed_s | 29.181 |
| p50_elapsed_s | 27.715 |
| p95_elapsed_s | 50.860 |
| avg_input_tokens_estimate | 71482.765 |
| avg_tool_calls | 2.44 |

Per-type judge accuracy:

| type | n | judge_acc | F1 |
| --- | ---: | ---: | ---: |
| knowledge-update | 52 | 0.7885 | 0.3574 |
| single-session-preference | 61 | 0.6066 | 0.2413 |
| single-session-user | 87 | 0.7356 | 0.3041 |

Main failure labels:

| error type | count |
| --- | ---: |
| retrieved_but_evidence_unused | 30 |
| retrieved_but_answer_wrong | 28 |
| personal_preference_attribution_error | 24 |
| memory_update_not_versioned | 11 |

Interpretation:

- The 50-example 88% result was optimistic. The larger 200-example estimate is
  71%, so the project should use 71% as the more credible current score for
  this method.
- Knowledge-update is the strongest category at 78.85%.
- Single-session-preference is the weakest category at 60.66%.
- The main bottleneck has moved from pure retrieval availability to answer
  planning after retrieval. Many failures already contain relevant evidence,
  but the agent either ignores it, chooses the wrong cited fact, or fails to
  infer the latent personal preference.

## Result Interpretation

`intent-aware retrieval` is the best accuracy-oriented single method in this
round. It improves overall judge accuracy by 6 points and especially helps
`single-session-user` questions, but it is expensive: average latency rises
from 22.6s to 38.6s because it runs an additional intent-specific retrieval
lane before baseline fallback.

`memory update/versioning` reaches the same overall judge accuracy as
intent-aware retrieval while keeping latency closer to baseline. It reduces
memory write and preference attribution errors, but it does not yet solve
knowledge-update directly; the knowledge-update judge accuracy drops from
0.7273 to 0.6364. This means the current heuristic is useful for general
answer selection but too coarse for conflict resolution.

`structured memory schema` alone is not enough. It exposes useful fields
(`person_id`, `memory_type`, `source_ref`, validity windows), but without a
stronger retrieval/rerank policy the model appears to over-attend to extra
metadata or weaker records. This method should be treated as infrastructure for
later compound methods, not as an answer-quality improvement by itself.

`evidence fetch + rerank` is also insufficient as a standalone heuristic. It
reduces evidence-unused cases, but it increases retrieved-wrong and preference
attribution errors. The lesson is that lexical/source bonuses need to be paired
with intent-aware filters and stronger contradiction handling.

`hybrid intent + temporal rerank` did not improve the overall score. It matched
baseline accuracy, helped stable preference and some timeline cases, but hurt
`single-session-user` attribution badly. The main lesson is that generic
evidence/query overlap is not enough for "who said it", "who first", and
relationship questions; those need speaker-aware evidence chains.

`adaptive intent + versioned retrieval` ties the best overall accuracy at 82%
and gives the strongest knowledge-update accuracy so far: 90.9%. The tradeoff
is higher latency and token usage than `method_04`, with weaker preference and
speaker-attribution performance than the best per-type variants. It is a useful
direction, but not yet a clear production replacement.

`source-grounded slot resolver` is a negative result. It was motivated by
MemGuide, OCR-Memory, MemSearcher, APEX-MEM, and Memora/FAMA: decompose
questions into slots, expose source anchors, and force exact evidence for
speaker/order/update cases. In this implementation, the evidence rows were
attached to `recall_memory` records through `signals` and also prepended to
summaries. That made provenance visible, but it degraded answer quality:
overall judge accuracy dropped to 70%. The failure mode is important: the model
often had enough raw evidence but over-weighted procedural hints or the wrong
candidate person. This suggests that source-grounding should not be done by
polluting human-readable summaries. It needs either a separate raw-message
resolver or a production memory schema with explicit event/entity/source tables.

`deterministic attribution resolver` is also a negative full-run result. It
made the source_ref decision deterministic at answer time and even constrained
follow-up `search_messages` / `fetch_messages` calls after a high-confidence
selection. This fixed the targeted `quieter venues` case in an isolated v3
retry, but the full 50-example run dropped to 66% judge accuracy. The biggest
regression is knowledge-update accuracy, which fell to 36.4%. The lesson is
that benchmark-level tool-chain coercion can repair a single evidence path but
does not provide a reliable memory architecture. Source-grounded attribution
needs to be represented in the stored memory schema itself, with explicit
event/entity/source/version state, rather than enforced by wrappers around the
final model's tool choices.

`production structured memory schema` implements that production representation
inside `memory2`: raw events, entity rows, event facts, memory assertions,
relation facts, validity state, `version_of`, and source refs. It also backfills
raw message content from `SessionStore` for frozen benchmark workspaces and
exposes the evidence as `signals.structured_evidence` without modifying
summaries. The adjusted full result is still only 70%, matching method_07 and
below the 76% baseline. This is a useful infrastructure result rather than a
quality win: it proves the storage layer can preserve faithful speaker/source
evidence, but the final answer path still often ignores or misuses it. The
remaining failures are especially clear on `who first`, speaker attribution,
and implicit preference cases. The next method should read the structured
tables directly for slot-specific candidate extraction and source-grounded
reranking, instead of merely exposing structured evidence to the model.

`structured candidate resolver` is the first method that actually consumes the
production `memory_raw_events` table during answer-time recall. It backfills all
raw benchmark messages, searches structured rows directly, builds
`selected_evidence_table`, and emits candidate-resolution fields before final
generation. The adjusted full result is still 70%, so it is a negative quality
result rather than a breakthrough. It improves the plumbing but not the final
score because the evidence set is still too broad and noisy: average tool
result size rises to about 58.7k characters, and 8 of the 15 false cases are
still labeled `retrieval_failure`. The next architecture bottleneck is
question-aware narrowing: the resolver must use the original question/options,
infer session/entity scope first, and return a much smaller source-grounded
evidence set before final answering.

`question-aware structured router` implements that narrowing step. The QA runner
sets a benchmark-only question context before each answer turn, and the
replacement `recall_memory` tool reads the original question/options, filters
`memory_raw_events` by `session_key = lme:<question_id>` first, removes generic
terms and option-name-only scoring, and returns a compact `slot_decision` with
supporting and contradicting source refs. The adjusted full result recovers the
baseline at 76%, improves over method_11 by 6 points, cuts `retrieval_failure`
from 8 to 4, and gives the best single-session-preference accuracy so far
(88.24%). It still does not beat the 82% best methods. The remaining failures
show a different bottleneck: for exception/negation and nuanced update
questions, retrieved evidence is often present but the final answer still
chooses the wrong contrast or trajectory. The next method should build a
slot-specific answer plan from `slot_decision`, not widen retrieval again.

`slot decision answer planner` tested that hypothesis by converting
`slot_decision` into an explicit `answer_plan`: per-option support rows,
ordered evidence, update evidence, and final-answer constraints. It fixed the
targeted `socialmem_Q2_e1f2a3b4` exception case after the planner's answer was
aligned back into `candidate_resolution`, but the full 50-example result
regressed to 74%. Compared with method_12, it improved
`single-session-user` accuracy from 0.7273 to 0.7727, but preference accuracy
fell from 0.8824 to 0.7647 and the estimated input-token cost rose from
42599.64 to 53384.12. This is a useful negative result: answer-time planning
can repair isolated option/exception cases, but making the retrieval payload
larger reintroduces noise and does not solve memory-write or versioning
failures. The next optimization should move the structure earlier into memory
write/consolidation: durable exception facts, relationship facts, and update
trajectories with source refs, rather than another final-answer wrapper.

`consolidated memory write quality` implements that write-side hypothesis. It
backfills compact `preference_fact`, `exception_fact`, `relationship_fact`,
`decision_fact`, and `update_trajectory_fact` records from `memory_raw_events`
inside the copied frozen benchmark workspace, preserving source refs, speaker,
date, message index, and quote. The adjusted full run ties the best recorded
accuracy at 82% and is the fastest 82% method so far: 18.4036s average latency.
It also improves `knowledge-update` over method_04 and `single-session-user`
over method_12/13. It still does not meet the success criterion because it does
not exceed 82%, and preference accuracy remains 0.7647. The remaining errors
show a narrower bottleneck: compact facts are often retrieved, but final answer
selection still picks the wrong contrast or over-attributes a preference.

`consolidated fact precision rerank` tested whether that final selection
problem could be fixed by grouping compact facts and a small disputed raw-event
scan by candidate. It repaired three Method 14 failures, including the quiet
venue attribution and the council-norm exception, but the full run regressed to
74%. The negative result is important: broad answer-time candidate reranking
adds another noisy control surface. Even when a candidate decision is available,
it can choose the wrong later speaker or over-focus on a norm setter rather than
the exception.

`conservative precision gate` narrowed the same idea to option-style
speaker/who-first/norm-exception questions and fixed the `who first` tie-break
by sorting candidates with global `source_ref` order before local
`message_index`. Targeted smoke behaved as expected, but the full 50-example
run still landed at 74%. It keeps strong single-session-user accuracy at
86.36%, but knowledge-update falls to 63.64%. This shows the next bottleneck is
not simply candidate gating; it is final-answer control. The next method should
produce a small structured final-answer contract or verifier-backed answer from
the selected source refs, instead of asking the final model to freely reinterpret
retrieved facts.

`structured answer contract` is the first method to break the previous 82%
ceiling. It keeps Method 14's compact consolidated facts, inherits Method 16's
narrow precision gate, and only applies a tiny `final_answer_contract` after a
slot-specific verifier confirms that the selected option is supported by source
refs. The clean adjusted result reaches 88% judge accuracy: +12 points over the
baseline and +6 points over Method 14. It keeps knowledge-update strong at
90.91% and raises single-session-user accuracy to 95.45%. The contract fired
only twice in the clean run, on Jordan's quiet-venue attribution and Vera's
council-norm exception; both were correct and cited one exact source ref. This
supports the design conclusion from the negative Method 15/16 runs: do not add
larger candidate tables to the final model; instead, use a small verified
answer contract only when the evidence is narrow and source-backed. The
remaining failures are mostly implicit preference inference and nuanced update
trajectory interpretation.

## Run Notes

`method_04_memory_update_versioning` hit a transient Mimo 429 during one case.
The affected cached result had `pred = null` and `judge = null`, so that case
was deleted from the method workspace and rerun with the same method config.
Final method_04 metrics have:

- `runtime_errors = 0`
- `judge_null = 0`
- `n = 50`

`method_05_hybrid_intent_temporal_rerank` also hit a transient Mimo 429 where
the agent returned `处理消息时出错，请稍后再试。`. That cached case was deleted
and rerun with `--resume-auto`, leaving final metrics with `runtime_errors = 0`
and `judge_null = 0`.

During `method_06_adaptive_intent_versioned`, the run exposed a tool robustness
bug: `fetch_messages` assumed every `evidence` item was a dict, but the model
can pass source refs as strings. The tool now normalizes mixed evidence shapes
including dicts, strings, and JSON-array strings. The polluted 429 case was also
deleted and rerun with `--resume-auto`.

`method_07_source_grounded_slot_resolver` hit one transient Mimo 429 in the raw
50-case run. The raw metrics are preserved as
`metrics_raw_with_429.json` / `error_cases_raw_with_429.json`. The affected
case was rerun in a clean copied workspace and judged correct, then merged into
`eval/results/memory_methods/method_07_source_grounded_slot_resolver_adjusted.json`.
The standard `metrics.json` and comparison table use this adjusted result.

`method_08_raw_message_first_resolver` replaces benchmark `recall_memory` with
a raw-message-first tool. It returns ordinary memory items plus a separate
`evidence_table` searched directly from `SessionStore`; it does not rewrite
memory summaries. The runtime smoke passed. A targeted retry of the
`quieter venues` attribution failure still answered Priya even though
`candidate_answer_hints` ranked Jordan first with the exact term `quieter`.
This suggests the next improvement must make attribution/ordering/exception
resolution deterministic or schema-backed before final generation.

`method_09_deterministic_attribution_resolver` implemented that deterministic
benchmark wrapper. The raw full run hit one Mimo 429 on
`socialmem_Q5_r3e5f6a7`; the polluted raw artifacts are preserved as
`method_09_deterministic_attribution_resolver_raw_with_429.json` and
`metrics_raw_with_429.json`. The affected case was rerun in a clean copied
workspace, judged correct, and merged into
`method_09_deterministic_attribution_resolver_adjusted.json`. The standard
`metrics.json` and comparison table use the adjusted 66% result.

`method_10_production_structured_memory_schema` hit two Mimo 429-polluted
answers in the raw full run:

- `socialmem_Q5_r3e5f6a7`
- `socialmem_Q6_a5s3c2`

The raw artifacts are preserved as
`method_10_production_structured_memory_schema_raw_with_429.json`,
`metrics_raw_with_429.json`, and `error_cases_raw_with_429.json`. Both cases
were rerun with the same workspace and method config; the first remained
judge-incorrect but was no longer a provider failure, and the second became
judge-correct. The adjusted result is saved as
`method_10_production_structured_memory_schema_adjusted.json`, copied to the
standard result path, and summarized into the standard `metrics.json`.

`method_11_structured_candidate_resolver` hit four Mimo provider-polluted
answers in the raw full run:

- `socialmem_Q5_r3e5f6a7`
- `socialmem_Q7_r3a7b8c9`
- `socialmem_Q4_v4s1c2`
- `socialmem_Q6_a5s3c2`

The raw artifacts are preserved as
`method_11_structured_candidate_resolver_raw_with_429.json`,
`metrics_raw_with_429.json`, and `error_cases_raw_with_429.json`. Each affected
case was rerun with the same workspace and method config; all four rerun cases
were judged correct. The adjusted result is saved as
`method_11_structured_candidate_resolver_adjusted.json`, copied to the standard
result path, and summarized into the standard `metrics.json`.

`method_12_question_aware_structured_router` hit six Mimo provider-polluted
answers in the raw full run:

- `socialmem_Q6_c0s5c1`
- `socialmem_Q2_e1f2a3b4`
- `socialmem_Q5_e5f6a7b8`
- `socialmem_Q6_n2a7b8c9`
- `socialmem_Q5_r3e5f6a7`
- `socialmem_Q7_r3a7b8c9`

The raw artifacts are preserved as
`method_12_question_aware_structured_router_raw_with_429.json`,
`metrics_raw_with_429.json`, and `error_cases_raw_with_429.json`. Each affected
case was rerun with the same workspace and method config; one case required a
higher judge token budget because the first clean prediction produced a null
judge. The six clean reruns were merged into
`method_12_question_aware_structured_router_adjusted.json`, copied to the
standard result path, and summarized into the standard `metrics.json`.

`method_14_consolidated_memory_write_quality` hit two Mimo provider-polluted
answers in the raw full run and one null judge:

- `socialmem_Q5_c0s4c1`
- `socialmem_Q4_r3d4e5f6`
- `socialmem_Q5_r3e5f6a7`

The raw artifacts are preserved as
`method_14_consolidated_memory_write_quality_raw_with_429.json`,
`metrics_raw_with_429.json`, and `error_cases_raw_with_429.json`. Only the
affected cached case files were deleted and rerun with the same method config
and a higher judge token budget where needed. The three clean reruns were
merged into `method_14_consolidated_memory_write_quality_adjusted.json`, copied
to the standard result path, and summarized into the standard `metrics.json`.

`method_15_consolidated_fact_precision_rerank` hit four provider-polluted or
null-judge cases in the raw full run:

- `socialmem_Q4_d4e5f6a7`
- `socialmem_Q5_e5f6a7b8`
- `socialmem_Q5_r3e5f6a7`
- `socialmem_Q7_r3a7b8c9`

The polluted raw result is preserved as
`method_15_consolidated_fact_precision_rerank_raw_with_pollution.json`, with
matching raw metrics and error cases. The affected cases were deleted at the
per-case cache level, rerun with `--resume-auto`, merged back into the standard
result path, and summarized into the standard `metrics.json`.

`method_16_conservative_precision_gate` hit four provider-polluted or
null-judge cases in the raw full run:

- `socialmem_Q5_e5f6a7b8`
- `socialmem_Q5_r3e5f6a7`
- `socialmem_Q8_v4s2c1`
- `socialmem_Q8_v4s3c1`

The polluted raw result is preserved as
`method_16_conservative_precision_gate_raw_with_pollution.json`, with matching
raw metrics and error cases. The affected cases were deleted at the per-case
cache level, rerun with `--resume-auto`, merged back into the standard result
path, and summarized into the standard `metrics.json`.

`method_17_structured_answer_contract` hit one provider-polluted case in the
raw full run:

- `socialmem_Q6_c0s5c1`

The polluted raw result is preserved as
`method_17_structured_answer_contract_raw_with_pollution.json`, with matching
raw metrics and error cases. The affected case was deleted at the per-case
cache level, rerun cleanly at offset 4, and merged back into the standard result
path. Because `merge_results` is first-wins for duplicate `question_id`, the
clean single-case rerun was passed before the raw 50-case file. The final
standard metrics have `judge_null = 0`, `runtime_errors = 0`, and `n = 50`.

## Smoke Verification

`method_02_structured_memory_schema` was smoke-tested on one copied
SocialMemBench workspace with `--qa-only --skip-judge`. The result confirmed:

- top-level `memory_method` is written to the output JSON
- per-result `memory_method` is written
- `recall_memory` output includes `signals.structured_schema`
- `react_stats` includes `turn_input_sum_tokens`, `final_call_input_tokens`,
  `cache_prompt_tokens`, and `cache_hit_tokens`

Smoke artifact:

- `eval/results/memory_methods/_smoke_method_02.json`

## Standard Run Command

Prepare a frozen copy of the baseline memory first:

```powershell
python -m eval.longmemeval.prepare_method_workspace `
  --source-workspace runtime/eval/socialmem_mimo_50 `
  --target-workspace runtime/eval/memory_methods/<method_id> `
  --archive-existing
```

Then run the method in QA-only mode:

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

Summarize after each run:

```powershell
python -m eval.longmemeval.summarize_method_results `
  --result eval/results/memory_methods/<method_id>.json `
  --method-dir experiments/memory_methods/<method_id> `
  --method-id <method_id> `
  --run-log eval/runs/memory_methods/<method_id>.log
```

## Interview Summary

The original system already has a complete end-to-end memory path, but the first
measured baseline and method comparison show four algorithmic gaps:

- memory write and consolidation can miss information that raw message search
  can still find
- retrieved summaries are sometimes too weak unless the agent fetches original
  evidence
- knowledge-update cases can still contain old and new candidates together
- naive metadata exposure and naive evidence reranking can degrade answer
  quality unless paired with intent filtering and contradiction handling
- speaker attribution still needs message-index and speaker-id aware reasoning,
  especially for "who first" and "who said it" questions
- implicit preference inference remains weak when the answer requires reading
  avoidance, exception, or negation rather than a direct statement

The experiment framework now lets each improvement be evaluated independently
instead of mixing several changes in the production memory engine. This is
important for interviews because every claimed optimization can be tied to a
method directory, a config, a result file, and an error-case delta.

Best interview phrasing:

> I built a reproducible memory-evaluation harness around the production agent
> loop instead of testing retrieval in isolation. I froze the baseline memory
> store, implemented multiple pluggable answer-time memory strategies, and compared
> them on 50 SocialMemBench personal-assistant examples with Mimo as both model
> and judge. Intent-aware retrieval and memory-update/versioning each improved
> judge accuracy from 76% to 82%; an adaptive router also reached 82% and
> improved knowledge-update accuracy to 90.9%, while schema-only, rerank-only,
> source-wrapper, raw-event candidate-resolver, and answer-plan variants
> regressed. A consolidated-fact write-side method reached 82% with much lower
> latency, and the final-answer-contract method broke the previous ceiling at
> 88% by applying source-backed answer contracts only on high-confidence option
> slots. I then expanded that best method to 200 SocialMemBench examples and
> got a cleaner 71% estimate with zero runtime errors and zero provider-polluted
> answers, which showed the 50-example score was optimistic and exposed the real
> remaining bottleneck: evidence is often retrieved but not used correctly in
> final answer planning. The result was not just a better score, but a clear
> ablation table: which memory technique helps, which adds latency, which
> control surfaces regress, and which failure types remain unresolved.

Next optimization should focus on the six remaining failures:

1. Improve implicit preference inference when the gold answer depends on
   avoidance or deflection rather than a literal stated preference.
2. Keep final-answer contracts narrow; expand them only when the verifier can
   prove the latent preference or update trajectory from source refs.
3. Improve version/trajectory interpretation for multi-session changes such as
   escalating safety concerns, where the current answer over-summarizes later
   recovery instead of the sustained active concern.
4. Store provider prompt/completion usage directly so token cost is actual
   usage rather than `react_stats.turn_input_sum_tokens` estimate.
