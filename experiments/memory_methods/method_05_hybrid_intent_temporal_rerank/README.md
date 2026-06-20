# method_05_hybrid_intent_temporal_rerank

Purpose: test whether a combined retrieval strategy can beat the best isolated
methods on SocialMemBench personal-assistant memory questions.

Core idea:

- keep baseline retrieval so broad event-window memories are not lost
- add an intent-specific lane for preference/profile/update questions
- rerank with evidence availability, query-term overlap, and memory type fit
- use temporal hints only for earliest/latest questions
- preserve both old and new records for "changed over time" questions

Code entry point: `eval.longmemeval.methods.HybridIntentTemporalRerankEngine`.

Result:

- `judge_acc`: 0.7600
- `F1`: 0.3221
- `avg_elapsed_s`: 37.5998
- `knowledge-update`: 0.7273
- `single-session-preference`: 0.8824
- `single-session-user`: 0.6818

Interpretation: this is a useful negative result. The hybrid reranker helps
some stable preference and timeline cases, but it over-reranks attribution and
relationship questions where speaker identity matters more than generic
evidence/query overlap.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
python -m eval.longmemeval.run_memory_method_batch `
  --methods method_05_hybrid_intent_temporal_rerank `
  --limit 50 `
  --timeout 240
```
