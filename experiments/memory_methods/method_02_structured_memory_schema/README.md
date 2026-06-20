# method_02_structured_memory_schema

Purpose: test whether structured memory fields help the agent reason over
retrieved memories instead of treating every result as an untyped summary.

Added fields:

- `person_id`
- `memory_type`
- `session_id`
- `timestamp`
- `source_ref`
- `confidence`
- `valid_from`
- `valid_to`

Code entry point: `eval.longmemeval.methods.StructuredMemorySchemaEngine`.

Run:

```powershell
python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/method_02_structured_memory_schema `
  --output eval/results/memory_methods/method_02_structured_memory_schema.json `
  --limit 50 `
  --resume-auto `
  --timeout 240 `
  --method-config experiments/memory_methods/method_02_structured_memory_schema/config.json
```

