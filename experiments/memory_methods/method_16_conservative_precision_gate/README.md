# method_16_conservative_precision_gate

Purpose: recover Method 14's stable 82% baseline while keeping only the parts
of Method 15 that helped source-grounded option attribution.

Method 15 fixed a few cases, but its broad precision payload regressed more
questions than it improved. Method 16 is therefore deliberately conservative:

- keep Method 14 compact consolidated facts and source refs;
- apply precision reranking only to narrow option-style questions:
  - `who said` / `who expressed`;
  - `who first` / message-order attribution;
  - explicit `norm ... everyone` exception questions;
- otherwise return the original Method 14 recall payload unchanged;
- fix `who first` tie-breaking by using the global `source_ref` order before
  local `message_index`.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_16_conservative_precision_gate `
  --limit 50 `
  --timeout 240
```

Current status:

- Implemented after Method 15 full-run showed a negative result.
- Full 50-example benchmark completed.
- Clean result after preserving and replacing provider-polluted cases:
  - judge_acc: 0.7400
  - F1: 0.3180
  - avg_elapsed_s: 20.1548
  - knowledge-update: 0.6364
  - single-session-preference: 0.6471
  - single-session-user: 0.8636
- Negative result overall, despite improving single-session-user accuracy.

Expected target:

- Preserve Method 14's 82% result.
- Keep Method 15 improvements on `socialmem_Q4_d4e5f6a7` and
  `socialmem_Q6_a5s3c2`.
- Avoid Method 15 regressions on broad preference/update questions.

Observed result:

- Targeted smoke showed the gate could fix `socialmem_Q4_n2e5f6a7` by using
  global `source_ref` order for `who first`.
- Full run still landed at 74%; the biggest regression was knowledge-update
  accuracy dropping to 63.64%.
- This suggests the next method should stop relying on final-model free-form
  use of recall payloads and instead emit a small structured answer contract or
  verifier-backed final answer for high-confidence slots.
- Raw polluted run is preserved as
  `eval/results/memory_methods/method_16_conservative_precision_gate_raw_with_pollution.json`.

Artifacts:

- `experiments/memory_methods/method_16_conservative_precision_gate/config.json`
- `experiments/memory_methods/method_16_conservative_precision_gate/source_snapshot/`
- `eval/results/memory_methods/method_16_conservative_precision_gate.json`
- `experiments/memory_methods/method_16_conservative_precision_gate/metrics.json`
- `experiments/memory_methods/method_16_conservative_precision_gate/error_cases.json`
