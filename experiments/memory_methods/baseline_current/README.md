# baseline_current

Purpose: freeze the current Akashic memory behavior as the comparison baseline.

This method does not alter retrieval, schema, reranking, or update handling. It
uses the production default memory engine exactly as the benchmark runtime builds
it.

Run:

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:MIMO_API_KEY = [Environment]::GetEnvironmentVariable("MIMO_API_KEY", "User")

python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/baseline_current `
  --output eval/results/memory_methods/baseline_current.json `
  --limit 50 `
  --resume-auto `
  --timeout 240
```

Summarize:

```powershell
python -m eval.longmemeval.summarize_method_results `
  --result eval/results/memory_methods/baseline_current.json `
  --method-dir experiments/memory_methods/baseline_current `
  --method-id baseline_current `
  --run-log eval/runs/memory_methods/baseline_current.log
```

