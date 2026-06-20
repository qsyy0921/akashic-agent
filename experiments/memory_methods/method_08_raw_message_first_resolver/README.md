# method_08_raw_message_first_resolver

Purpose: test the method_07 lesson directly.  Source grounding should happen
through a separate raw-message evidence table, not by prepending procedural
metadata to memory summaries.

This is a benchmark-only method. It does not change production QQ/Telegram/MCP
services, browser sessions, or long-term memory ingestion.

Method design:

- Keep the underlying memory engine conservative.
- Replace the benchmark `recall_memory` tool with `RawMessageFirstRecallTool`.
- The mandatory first `recall_memory` call still returns ordinary memory items.
- The same response also includes `evidence_table` rows searched directly from
  `SessionStore`.
- Each evidence row contains `speaker_id`, `speaker`, `message_index`, `seq`,
  `timestamp/date`, `source_ref`, `quote`, `preview`, and matched slots.
- The memory `summary` field is not rewritten.

Paper basis:

- MemGuide: retrieve by missing slots, not only semantic similarity.
- OCR-Memory: answer from stable source anchors and recover faithful evidence.
- MemSearcher: build compact question-relevant working memory at answer time.
- APEX-MEM / Memora: keep source-grounded evidence and resolve conflicts at
  query time.

Expected target:

- Recover from method_07's 70% regression by separating raw evidence from
  memory summaries.
- Improve speaker attribution, who-first ordering, exception/negation, and
  update questions without harming preference questions as much as method_07.

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8="1"
$env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")

python -m eval.longmemeval.run_memory_method_batch `
  --methods method_08_raw_message_first_resolver `
  --limit 50 `
  --timeout 240
```

Current status:

- Implementation added.
- Unit tests passed.
- Runtime smoke passed on 1 SocialMemBench case with `--skip-judge`.
- Targeted check on `socialmem_Q4_d4e5f6a7` still failed.

Smoke artifacts:

- `eval/results/memory_methods/method_08_raw_message_first_resolver_smoke.json`
- `eval/results/memory_methods/method_08_raw_message_first_resolver_target_q4_d4e5f6a7_v3.json`

Targeted observation:

- The raw `evidence_table` included Jordan's row:
  `source_ref=lme:socialmem_Q4_d4e5f6a7:13`, `speaker=Jordan`,
  `message_index=14`, quote `Quieter. Better.`
- `candidate_answer_hints` correctly ranked Jordan above Priya because Jordan
  matched the distinctive term `quieter`.
- Mimo still answered Priya because Priya had the longer explicit phrase
  `somewhere you can actually have a conversation`.

Interpretation:

Method 08 proves that raw evidence can be exposed cleanly without summary
pollution, but the model still does not reliably execute source-grounded
speaker attribution. The next step should not be another hint field. It should
move the attribution decision into a deterministic resolver or a structured
event/entity/source schema before the final LLM answer.
