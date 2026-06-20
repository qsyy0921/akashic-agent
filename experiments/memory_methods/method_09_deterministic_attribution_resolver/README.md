# method_09_deterministic_attribution_resolver

Purpose: test the Method 08 lesson directly.  Raw evidence alone is not enough
when the final model still chooses the wrong speaker.  This benchmark-only
method moves part of speaker/order/exception/update resolution into a
deterministic resolver before final LLM generation.

This method does not change production QQ/Telegram/MCP services, browser
sessions, or long-term memory ingestion.

Method design:

- Keep the underlying memory engine conservative.
- Reuse Method 08's raw-message-first `recall_memory` path.
- Add `DeterministicResolverRecallTool`.
- Add Method 09-only wrappers around `search_messages` and `fetch_messages`.
  Once recall selects a high-confidence source_ref, later lookup tools are
  constrained to that source_ref.
- Put `deterministic_resolution.selected_candidate` at the front of the tool
  JSON result.
- Select speaker candidates by highest distinctive exact-term source match.
- Select who-first/order candidates by `message_index` / `seq` among relevant
  source-grounded candidates.
- Select exception candidates from negation/exception source terms.
- Select update candidates from latest relevant source evidence while retaining
  previous evidence.

Paper basis:

- MemGuide: decompose questions into missing slots and answer from slot-relevant
  evidence.
- OCR-Memory: recover faithful source anchors before answer generation.
- MemSearcher: construct compact working memory at answer time.
- APEX-MEM / Memora: keep event/source state explicit and resolve conflicts at
  query time.

Expected target:

- Fix the Method 08 targeted failure `socialmem_Q4_d4e5f6a7`, where Jordan's
  exact `quieter` evidence was retrieved but Mimo still chose Priya.
- Improve speaker attribution, who-first ordering, exception/negation, and
  update questions without polluting memory summaries.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_09_deterministic_attribution_resolver `
  --limit 50 `
  --timeout 240
```

Current status:

- Implementation added.
- Unit tests passed.
- Targeted v3 retry for `socialmem_Q4_d4e5f6a7` passed.
- Full 50-example SocialMemBench run completed.

Results:

| result | judge_acc | F1 | avg_elapsed_s | note |
| --- | ---: | ---: | ---: | --- |
| raw full run | 0.6531 | 0.2749 | 27.7386 | contained one Mimo 429 polluted case |
| adjusted full run | 0.6600 | 0.2850 | 27.9876 | 429 case rerun and merged |

Per-type adjusted judge accuracy:

| type | judge_acc |
| --- | ---: |
| knowledge-update | 0.3636 |
| single-session-preference | 0.8235 |
| single-session-user | 0.6818 |

Artifacts:

- `eval/results/memory_methods/method_09_deterministic_attribution_resolver.json`
- `eval/results/memory_methods/method_09_deterministic_attribution_resolver_adjusted.json`
- `eval/results/memory_methods/method_09_deterministic_attribution_resolver_raw_with_429.json`
- `eval/results/memory_methods/method_09_deterministic_attribution_resolver_target_q4_d4e5f6a7_v3.json`
- `experiments/memory_methods/method_09_deterministic_attribution_resolver/metrics.json`
- `experiments/memory_methods/method_09_deterministic_attribution_resolver/error_cases.json`

Interpretation:

Method 09 is a negative full-run result. It proves that deterministic
answer-time source_ref selection can fix an isolated speaker-attribution case,
but wrapper-level coercion does not generalize. It increases tool-chain
fragility, still fails some who-first/order cases, and severely hurts
knowledge-update accuracy. The next method should move from benchmark wrappers
to a production event/entity/source schema with explicit validity/version
state.
