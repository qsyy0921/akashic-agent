# method_15_consolidated_fact_precision_rerank

Purpose: test whether Method 14's compact facts can be made more reliable by
candidate-group precision reranking instead of broadening the final-answer
payload.

Method 14 reached 82% but still failed when a generic consolidated fact
overrode a more precise raw slot evidence row. Method 15 keeps the Method 14
write-side consolidation and adds a benchmark-only recall reranker:

- parse benchmark options without crossing line boundaries;
- group compact facts and disputed raw rows by candidate;
- require speaker/source_ref support plus slot-specific predicate support;
- prefer earliest source-grounded rows for `who first` questions;
- prefer contradiction or bypass evidence for `does everyone` questions;
- return a compact `candidate_precision_decision` instead of a wide evidence
  table.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_15_consolidated_fact_precision_rerank `
  --limit 50 `
  --timeout 240
```

Current status:

- Full 50-example benchmark completed.
- Clean result after preserving and replacing provider-polluted cases:
  - judge_acc: 0.7400
  - F1: 0.3004
  - avg_elapsed_s: 16.6288
  - knowledge-update: 0.9091
  - single-session-preference: 0.6471
  - single-session-user: 0.7273
- Negative result: it fixed selected attribution cases but introduced more
  regressions than improvements.

Expected target:

- Fix cases where Method 14 retrieved correct raw evidence but selected an
  unrelated consolidated preference fact.
- Preserve Method 14's low latency and 82% accuracy.
- Exceed 82% if the higher-precision candidate decision avoids regressions.

Observed result:

- Improved over Method 14 on `socialmem_Q4_d4e5f6a7`,
  `socialmem_Q8_v4s4c1`, and `socialmem_Q6_a5s3c2`.
- Regressed 7 cases that Method 14 had answered correctly.
- Main failure: broad precision reranking exposed too much answer-time
  candidate machinery and sometimes selected the norm setter or the later
  speaker instead of the exception/current answer.
- Raw polluted run is preserved as
  `eval/results/memory_methods/method_15_consolidated_fact_precision_rerank_raw_with_pollution.json`.

Artifacts:

- `experiments/memory_methods/method_15_consolidated_fact_precision_rerank/config.json`
- `experiments/memory_methods/method_15_consolidated_fact_precision_rerank/source_snapshot/`
- `eval/results/memory_methods/method_15_consolidated_fact_precision_rerank.json`
- `experiments/memory_methods/method_15_consolidated_fact_precision_rerank/metrics.json`
- `experiments/memory_methods/method_15_consolidated_fact_precision_rerank/error_cases.json`
