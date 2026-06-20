# method_04_memory_update_versioning

Purpose: test whether update/version handling improves `knowledge-update`
questions where old and new facts may coexist.

Core idea:

- detect update-like questions
- sort candidate memories by extracted date and score
- group candidates by a coarse semantic signature
- keep the newest candidate first and mark older candidates as shadowed

Code entry point: `eval.longmemeval.methods.MemoryUpdateVersioningEngine`.

Run:

```powershell
python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/method_04_memory_update_versioning `
  --output eval/results/memory_methods/method_04_memory_update_versioning.json `
  --limit 50 `
  --resume-auto `
  --timeout 240 `
  --method-config experiments/memory_methods/method_04_memory_update_versioning/config.json
```

