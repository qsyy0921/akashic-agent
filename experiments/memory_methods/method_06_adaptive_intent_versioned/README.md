# method_06_adaptive_intent_versioned

Purpose: test whether the strongest observed per-intent strategies compose into
a better personal-assistant memory method.

Routing policy:

- speaker attribution, relationship, role, and most temporal-change questions:
  use `IntentAwareRetrievalEngine`
- stable preferences, dietary/food preference questions, and stance-style
  profile questions: use `MemoryUpdateVersioningEngine`, which is effectively
  baseline retrieval unless the query is update-like
- capability or willingness changes involving hiking/outdoor participation:
  use versioning, because this was a concrete Method 01 failure case

Code entry point: `eval.longmemeval.methods.AdaptiveIntentVersionedEngine`.

Result:

- `judge_acc`: 0.8200
- `F1`: 0.2889
- `avg_elapsed_s`: 36.0494
- `knowledge-update`: 0.9091
- `single-session-preference`: 0.7647
- `single-session-user`: 0.8182

Interpretation: this ties the best overall score from Method 01 and Method 04,
but reaches the best knowledge-update score so far. It does not beat the best
overall method because stable preference and speaker-attribution questions
still need stronger source-grounded reasoning.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_06_adaptive_intent_versioned `
  --limit 50 `
  --timeout 240
```
