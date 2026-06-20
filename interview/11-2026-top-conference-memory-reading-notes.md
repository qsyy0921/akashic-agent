# 2026 Top-Conference Memory Reading Notes

This note keeps source-backed reading notes for improving Akashic as a
personal assistant with long-term memory. It should be treated as the design
grounding for the next memory iteration, not as an exhaustive survey.

## Papers And Benchmarks

### APEX-MEM

Source: https://arxiv.org/abs/2604.14362

Venue claim: ACL 2026 Main.

Core claim: conversational memory should be structured as temporally grounded,
entity-centric events in a property graph. The system keeps append-only history
and resolves conflicts at retrieval time instead of overwriting old facts.

Relevance to Akashic:

- Keep raw event history append-only.
- Add entity/event/time edges, not only free-text summaries.
- Resolve "old vs new" at query time with source evidence.
- This supports our failing knowledge-update and "who first" cases better than
  current heuristic rerank.

### From Recall To Forgetting / Memora

Source: https://arxiv.org/abs/2604.20006

Venue claim: ACL 2026 Findings.

Core claim: personalized-memory evaluation must include obsolete or invalidated
memories. The paper introduces Forgetting-Aware Memory Accuracy (FAMA), which
penalizes reliance on invalid memories.

Relevance to Akashic:

- Add validity state to long-term memory: active, superseded, uncertain,
  forgotten.
- Evaluate not only recall accuracy, but also whether obsolete memories are
  avoided.
- Current method_04/versioning is too shallow because it operates at answer
  time, not in the memory object model.

### MemSearcher

Source: https://arxiv.org/abs/2511.02805

Venue claim: ACL 2026.

Core claim: search agents should keep compact, question-relevant memory during
multi-turn interaction instead of concatenating full history. Training uses
trajectory-level optimization across turns.

Relevance to Akashic:

- Separate "memory construction" from "per-question working memory".
- For a user question, build a compact scratch memory from retrieved evidence
  before final answer generation.
- Avoid feeding broad unrelated memory into the model.

### RecMem

Source: https://arxiv.org/abs/2605.16045

Venue claim: ACL 2026 Findings.

Core claim: eager LLM consolidation on every interaction is wasteful. RecMem
first stores lightweight subconscious memory, then invokes LLM extraction only
when semantically similar interactions recur.

Relevance to Akashic:

- Keep raw events cheap and immediate.
- Use embedding/BM25 recurrence to decide when a memory is worth LLM
  consolidation.
- This fits our QQ/Telegram assistant better than summarizing every message.

### OCR-Memory

Source: https://arxiv.org/abs/2604.26622

Venue claim: ACL 2026 Main.

Core claim: long trajectories can be rendered into anchored visual/textual
memory, then retrieved by locate-and-transcribe to recover verbatim evidence
with less hallucination.

Relevance to Akashic:

- The important idea is not the image trick itself; it is faithful evidence
  recovery with stable anchors.
- Akashic should make every answerable memory traceable to raw source refs and
  exact message windows.

### MemGuide

Source: https://ojs.aaai.org/index.php/AAAI/article/view/40313

Venue: AAAI-26.

Core claim: semantic similarity alone degrades multi-session coherence in
task-oriented dialogue. Intent-aligned retrieval plus missing-slot filtering
improves task success and reduces dialogue length.

Relevance to Akashic:

- Our method_01's intent-aware retrieval is directionally justified.
- But intent should not be just broad labels. It should identify missing slots:
  person, time, source, preference, exception, decision, current status.
- Retrieval should be scored by whether it fills missing slots, not just
  semantic overlap.

### MemoryAgentBench

Source: https://openreview.net/forum?id=DT7JyQC3MR

Code/source: https://github.com/HUST-AI-HYZ/MemoryAgentBench

Venue claim: ICLR 2026.

Core claim: memory agents should be evaluated on accurate retrieval,
test-time learning, long-range understanding, and conflict resolution via
incremental multi-turn interactions.

Relevance to Akashic:

- Our SocialMemBench-only evaluation is not enough.
- Add conflict-resolution and selective-forgetting-style tests.
- Keep the incremental interaction setup, not only one-shot retrieval QA.

### MemoryArena

Source: https://memoryarena.github.io/

Venue claim from project/GitHub ecosystem: ICML 2026.

Core claim: recall-only benchmarks do not show whether memory helps later
actions. MemoryArena evaluates multi-session Memory-Agent-Environment loops
across web navigation, preference-constrained planning, progressive search, and
formal reasoning.

Relevance to Akashic:

- Add an action-oriented eval, not just "answer a question from memory".
- For personal assistant behavior, test whether remembered preferences change
  future recommendations or proactive pushes.

### AMA-Bench

Source: https://github.com/AMA-Bench/AMA-Bench

Venue claim: ICML 2026.

Core claim: evaluate long-horizon agent trajectories with a two-stage
interface: memory construction from trajectory, then retrieval for questions.
It also supports evaluating external tool-using agents.

Relevance to Akashic:

- Preserve the two-stage method interface in our eval harness.
- Add external-agent eval mode for the full Akashic loop, not only memory
  engine wrappers.
- Record reasoning trace and retrieved evidence for judging.

### Memory Injection Attacks

Source: https://openreview.net/forum?id=i7J62t2wtV

Venue: ICLR 2026 MemAgents workshop.

Core claim: attackers can poison an agent memory bank through query-only
interactions, without directly editing memory storage.

Relevance to Akashic:

- Memory admission must include trust/source checks.
- User-provided statements should not automatically become stable facts.
- Long-term memory should store provenance, confidence, and scope.

## Design Conclusions For Akashic

The next serious improvement should not be another broad reranker. It should be
a source-grounded memory architecture:

1. Raw event log is always append-only.
2. Long-term memory records are structured events with entity, speaker, time,
   source_ref, confidence, and validity state.
3. Memory consolidation is asynchronous and recurrence-triggered, not eager for
   every message.
4. Retrieval decomposes the question into missing slots, then fills those slots
   with evidence.
5. Query-time resolution handles conflicts and obsolete memories explicitly.
6. Final answers must cite exact source windows for attribution and temporal
   claims.
7. Evaluation must include recall QA, update/forgetting QA, and action-oriented
   multi-session tasks.

## Immediate Next Experiment

Implement a `method_07_source_grounded_slot_resolver` as a benchmark-only
wrapper before touching production:

- classify question slots: person, speaker, time, update, exception, preference,
  relation, decision
- retrieve candidate memory and raw messages
- build a compact evidence table with `speaker_id`, `message_index`,
  `source_ref`, `timestamp`, and quote preview
- answer only from that evidence table
- track failures by missing slot instead of generic "retrieved wrong"

Expected target: improve the remaining "who said", "who first", "exception",
and implicit-preference failures that method_06 still misses.
