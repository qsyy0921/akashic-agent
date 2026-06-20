# method_03_evidence_fetch_rerank

Purpose: test whether evidence-aware reranking improves final answers when
memory recall returns several weak or loosely related summaries.

Ranking signals:

- original memory score
- source evidence availability
- query-term overlap
- memory-type fit inferred from the question

Code entry point: `eval.longmemeval.methods.EvidenceFetchRerankEngine`.

Run:

```powershell
python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/method_03_evidence_fetch_rerank `
  --output eval/results/memory_methods/method_03_evidence_fetch_rerank.json `
  --limit 50 `
  --resume-auto `
  --timeout 240 `
  --method-config experiments/memory_methods/method_03_evidence_fetch_rerank/config.json
```

