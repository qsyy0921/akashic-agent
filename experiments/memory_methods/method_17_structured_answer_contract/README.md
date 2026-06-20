# method_17_structured_answer_contract

Purpose: test whether high-confidence source-backed slots should bypass
free-form final answer interpretation.

Method 15 and Method 16 showed that adding more candidate/evidence payload can
fix targeted cases but regress the full benchmark. Method 17 keeps Method 14's
compact consolidated facts, reuses Method 16's narrow precision gate, and adds
a tiny verified `final_answer_contract`.

The benchmark postprocessor only applies the contract when:

- the question has explicit options;
- the slot is one of speaker attribution, who-first/message-order, or explicit
  norm-exception;
- the selected answer is one of the options;
- the answer has source refs;
- a slot-specific verifier confirms the evidence:
  - speaker attribution: speaker plus predicate quote;
  - who-first: earliest global `source_ref` order;
  - norm exception: direct bypass/exception quote.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_17_structured_answer_contract `
  --limit 50 `
  --timeout 240
```

Current status:

- Implemented.
- Full 50-example SocialMemBench benchmark complete.
- Clean adjusted result is now the standard method result.

Result:

| metric | value |
| --- | ---: |
| n | 50 |
| judge_acc | 0.8800 |
| delta_vs_baseline | +0.1200 |
| delta_vs_method_14 | +0.0600 |
| F1 | 0.2992 |
| avg_elapsed_s | 22.8964 |
| avg_token_usage_estimate | 50340.80 |
| judge_false | 6 |
| judge_null | 0 |

Per type:

| type | judge_acc | F1 |
| --- | ---: | ---: |
| knowledge-update | 0.9091 | 0.3136 |
| single-session-preference | 0.7647 | 0.2837 |
| single-session-user | 0.9545 | 0.3040 |

Interpretation:

- Method 17 is the first recorded method to break the 82% ceiling.
- It improves over the 76% baseline by 12 points and over Method 14 by 6 points.
- The gain comes from avoiding free-form reinterpretation on narrow,
  high-confidence option slots while keeping broad preference/update questions
  on the Method 14 path.
- Only two final-answer contracts fired in the clean full run:
  `socialmem_Q4_d4e5f6a7` for Jordan's quiet-venue attribution and
  `socialmem_Q6_a5s3c2` for Vera's council-norm exception. Both were correct
  and cited a single source ref.
- Remaining failures are still concentrated in implicit preference inference
  and nuanced update trajectories, especially when the gold answer expects a
  latent reason rather than the literal stated preference.

Run notes:

- The raw full run produced one provider-polluted result:
  `socialmem_Q6_c0s5c1` returned `处理消息时出错，请稍后再试。`.
- Raw artifacts are preserved as:
  - `eval/results/memory_methods/method_17_structured_answer_contract_raw_with_pollution.json`
  - `experiments/memory_methods/method_17_structured_answer_contract/metrics_raw_with_pollution.json`
  - `experiments/memory_methods/method_17_structured_answer_contract/error_cases_raw_with_pollution.json`
- The affected case was rerun cleanly with offset 4 and merged first into the
  raw result because `merge_results` is first-wins for duplicate `question_id`.

Artifacts:

- `experiments/memory_methods/method_17_structured_answer_contract/config.json`
- `experiments/memory_methods/method_17_structured_answer_contract/source_snapshot/`
- `eval/results/memory_methods/method_17_structured_answer_contract.json`
- `experiments/memory_methods/method_17_structured_answer_contract/metrics.json`
- `experiments/memory_methods/method_17_structured_answer_contract/error_cases.json`
