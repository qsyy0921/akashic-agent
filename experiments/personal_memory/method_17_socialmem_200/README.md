# method_17_socialmem_200

Purpose: validate the current best personal-memory method on a larger
SocialMemBench slice than the original 50-example development run.

Scope:

- Dataset: `eval/agent_memory_bench_socialmem_full_local.json`
- Limit: 200 examples, offset 0
- Model and judge: `mimo-v2.5-pro`
- Workspace: `runtime/eval/memory_methods/method_17_structured_answer_contract_200`
- Method config: `experiments/personal_memory/method_17_socialmem_200/config.json`

Method:

`method_17_structured_answer_contract` keeps Method 14 compact consolidated
facts, Method 16's conservative precision gate, and a narrow verified
`final_answer_contract` for high-confidence option-style slots.

Run sequence:

```powershell
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"

python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/socialmem_mimo_200 `
  --limit 200 `
  --ingest-only `
  --resume-auto `
  --workers 4 `
  --timeout 240

python -m eval.longmemeval.prepare_method_workspace `
  --source-workspace runtime/eval/socialmem_mimo_200 `
  --target-workspace runtime/eval/memory_methods/method_17_structured_answer_contract_200 `
  --archive-existing
```

The first QA attempts with 4 and 2 workers hit Mimo 429 rate limits and produced
provider-polluted answers. Those interrupted runs are preserved under:

- `eval/results/personal_memory/method_17_socialmem_200_interrupted_w4_polluted/`
- `eval/results/personal_memory/method_17_socialmem_200_interrupted_w2_polluted/`

The final clean run used provider throttling:

```powershell
$env:AKASHIC_LLM_MAX_RETRIES="6"
$env:AKASHIC_LLM_RETRY_BASE_DELAY_S="10"
$env:AKASHIC_LLM_RETRY_MAX_DELAY_S="90"
$env:AKASHIC_LLM_MIN_INTERVAL_S="4"

python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/method_17_structured_answer_contract_200 `
  --output eval/results/personal_memory/method_17_socialmem_200.json `
  --limit 200 `
  --qa-only `
  --resume-auto `
  --workers 1 `
  --timeout 300 `
  --judge-max-tokens 4096 `
  --method-config experiments/memory_methods/method_17_structured_answer_contract/config.json
```

Result:

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

Per type:

| type | n | judge_acc | F1 |
| --- | ---: | ---: | ---: |
| knowledge-update | 52 | 0.7885 | 0.3574 |
| single-session-preference | 61 | 0.6066 | 0.2413 |
| single-session-user | 87 | 0.7356 | 0.3041 |

Failure labels:

| label | count |
| --- | ---: |
| retrieved_but_evidence_unused | 30 |
| retrieved_but_answer_wrong | 28 |
| personal_preference_attribution_error | 24 |
| memory_update_not_versioned | 11 |

Interpretation:

- The 50-example result of 88% was optimistic. On the larger 200-example slice,
  the method stabilizes at 71%.
- Knowledge-update remains the strongest category at 78.85%.
- Single-session-preference is the weakest category at 60.66%, mostly because
  the agent retrieves relevant evidence but fails to infer the latent preference
  or attributes it to the wrong person.
- The biggest remaining algorithmic issue is not raw recall availability; many
  failures are `retrieved_but_evidence_unused` or `retrieved_but_answer_wrong`.
  The answer planner needs better evidence selection and contradiction/update
  handling after retrieval.

Artifacts:

- `eval/results/personal_memory/method_17_socialmem_200.json`
- `experiments/personal_memory/method_17_socialmem_200/metrics.json`
- `experiments/personal_memory/method_17_socialmem_200/error_cases.json`
- `experiments/personal_memory/method_17_socialmem_200/source_snapshot/`
