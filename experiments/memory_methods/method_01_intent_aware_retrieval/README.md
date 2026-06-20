# method_01_intent_aware_retrieval

Purpose: test whether query-intent routing improves personal-assistant memory
recall.

Core idea:

- classify the question as preference, user profile, relationship,
  knowledge-update, or event
- query the most likely memory kinds first
- merge that lane with the unchanged baseline lane

Code entry point: `eval.longmemeval.methods.IntentAwareRetrievalEngine`.

Run:

```powershell
python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/method_01_intent_aware_retrieval `
  --output eval/results/memory_methods/method_01_intent_aware_retrieval.json `
  --limit 50 `
  --resume-auto `
  --timeout 240 `
  --method-config experiments/memory_methods/method_01_intent_aware_retrieval/config.json
```

