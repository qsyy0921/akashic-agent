# method_07_source_grounded_slot_resolver

Purpose: test whether answer-time memory retrieval improves when the model sees
explicit source-grounded evidence rows instead of only loose memory summaries.

This is a benchmark-only wrapper. It does not change production ingestion,
Telegram/QQ routing, MCP tools, or browser sessions.

Method design:

- Decompose each question into slots: speaker attribution, message order,
  exception/negation, implicit preference, relationship, update/change, and
  decision/status.
- Retrieve a wider candidate set from baseline and intent-specific lanes.
- Convert each selected memory into compact evidence rows containing
  `memory_id`, `source_ref`, source sequence, date, speaker hint, matched slots,
  and a short preview.
- Add explicit answer rules to `signals.source_grounded_slot_resolver`, so
  `recall_memory` exposes them in its returned JSON.
- For source-sensitive questions, instruct the model to use `fetch_messages`
  before the final answer; if no source refs are available, guide it toward
  `search_messages`.

Paper basis:

- MemGuide / intent-aligned retrieval and missing-slot filtering.
- OCR-Memory / stable source anchors and faithful evidence recovery.
- MemSearcher / compact question-relevant working memory.
- APEX-MEM and Memora/FAMA / query-time conflict and update resolution without
  deleting historical evidence.

Expected target:

- Beat the current best SocialMemBench-50 result of 82% judge accuracy.
- Reduce the observed error classes from method_06: speaker attribution,
  who-first ordering, exception/negation, implicit preference, and update
  trajectory mistakes.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_07_source_grounded_slot_resolver `
  --limit 50 `
  --timeout 240
```

Result:

- Raw full run: `eval/results/memory_methods/method_07_source_grounded_slot_resolver.json`
- One case was contaminated by a transient Mimo 429 and returned
  `处理消息时出错，请稍后再试。`
- Clean rerun for that case:
  `eval/results/memory_methods/method_07_source_grounded_slot_resolver_rerun_q5_n2f6a7b8_clean.json`
- Adjusted result:
  `eval/results/memory_methods/method_07_source_grounded_slot_resolver_adjusted.json`
- Raw metrics with the 429-contaminated answer are preserved as
  `metrics_raw_with_429.json` and `error_cases_raw_with_429.json`

Adjusted metrics:

- `judge_acc`: 0.7000
- `F1`: 0.2967
- `avg_elapsed_s`: 30.6364
- `knowledge-update`: 0.6364
- `single-session-preference`: 0.7059
- `single-session-user`: 0.7273
- `judge_false`: 15

Interpretation:

This is a negative result. The method made evidence provenance visible, but it
also polluted the model-facing summaries with procedural hints and source-ref
metadata. Mimo often retrieved or fetched enough evidence but still chose the
wrong option or over-weighted the wrong person. The result suggests that
source-grounded reasoning should not be implemented by prepending instructions
to memory summaries. The next useful step is a real structured event/source
schema or a separate answer-time resolver that consumes raw messages directly,
not another summary-level reranker.
