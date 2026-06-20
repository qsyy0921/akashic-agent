---
language:
  - en
license: cc-by-4.0
pretty_name: SocialMemBench
size_categories:
  - 1K<n<10K
task_categories:
  - question-answering
  - text-generation
  - text-classification
task_ids:
  - closed-domain-qa
  - multiple-choice-qa
tags:
  - ai
  - memory
  - social-groups
  - conversational-ai
  - benchmark
  - multi-party
configs:
  - config_name: networks
    data_files: networks.parquet
  - config_name: personas
    data_files: personas.parquet
  - config_name: conversations
    data_files: conversations.parquet
  - config_name: qa
    data_files: qa.parquet
---

# SocialMemBench

A benchmark for evaluating AI memory systems in multi-party social group
conversations. SocialMemBench targets the *memory architecture* (write /
index / retrieve substrate) rather than the raw LLM, and asks whether the
system can recover the right speaker's preference, track group decisions,
distinguish norms from individual stances, and follow how preferences
evolve across sessions.

## Quick start

```python
from datasets import load_dataset

networks = load_dataset("anon4data/socialmembench", "networks", split="train")
conversations = load_dataset("anon4data/socialmembench", "conversations", split="train")
qa = load_dataset("anon4data/socialmembench", "qa", split="train")

# All three configs share `network_id` as the join key.
print(qa[0]["question"], qa[0]["answer"])
```

## Configurations

This dataset has three configurations, joinable on `network_id`.

### `networks` (43 rows)

One row per ego network. Every network is a small social group with
designed personas, relationship edges, and group norms.

| Column | Type | Description |
|---|---|---|
| `network_id` | string | Stable identifier (e.g. `grp_xxxxxxxx`). |
| `group_name` | string | Group name as it appears in chat. |
| `group_type` | string | `close_friends`, `family`, `recreational`, `interest_community`, or `acquaintance_network`. |
| `group_size` | int | Number of personas (4–30). |
| `seed` | int | Generation seed. |
| `tier` | string | `small`, `medium`, or `large`. |
| `personas` | list | Persona objects (see below). |
| `edges` | list | Directed relationship edges between personas. |
| `group_norms` | list | Stated group habits with `truly_universal` and `dissenters` flags. |
| `metadata` | dict | Extra metadata (timespan, noise level, etc.). |

Each persona carries a Big Five profile, a communication profile, a
preference profile (food / activities / social / domain-specific), a
preference history (for Q8 temporal queries), and speaking quirks.

### `conversations` (7355 rows)

One row per chat *turn*, in long form so it streams cleanly.

| Column | Type | Description |
|---|---|---|
| `network_id` | string | Joins to `networks.network_id`. |
| `session_id` | string | Stable session identifier. |
| `session_index` | int | 1-indexed session ordinal. |
| `session_topic` | string | Topic label. |
| `session_date_label` | string | Free-form date label as it appears in chat. |
| `session_date` | string | ISO-8601 date. |
| `session_gap_days` | int | Days since previous session (null for session 1). |
| `active_participants` | list | Persona IDs active in this session (medium/large groups only). |
| `turn_id` | string | Stable turn identifier. |
| `speaker_persona_id` | string | Speaker's persona ID. |
| `speaker_display_name` | string | Speaker's display name. |
| `timestamp` | string | ISO-8601 timestamp. |
| `message` | string | Message text. |
| `message_index` | int | Sequential index within session. |
| `reply_to_turn_id` | string | Turn this replies to (nullable). |

The `planted_challenges` ground-truth field is **stripped from the public
release** to prevent answer leakage. Researchers should evaluate against
the public `qa` config; the planted challenges remain in the source repo
for reproducibility.

### `qa` (1031 rows)

One row per question. Every question carries `evidence_anchors` pointing
to the exact turns that license the ground-truth answer.

| Column | Type | Description |
|---|---|---|
| `qa_id` | string | Stable QA identifier. |
| `network_id` | string | Joins to `networks.network_id`. |
| `query_type` | string | `Q1`–`Q9` (see table below). |
| `query_type_label` | string | Human-readable label. |
| `difficulty` | string | `easy`, `medium`, or `hard`. |
| `question` | string | Question text. |
| `answer` | string | Ground-truth answer (long-form, short, or MC letter). |
| `answer_format` | string | `multiple_choice`, `short_answer`, or `long_form`. |
| `options` | dict | MC only: `{"A": ..., "B": ..., "C": ..., "D": ...}`. |
| `correct_option` | string | MC: letter key. Short answer: canonical value. |
| `evidence_anchors` | list | Turns that ground the answer. |
| `contamination_foil` | string | Q4: plausible-but-wrong attribution distractor. |
| `temporal_anchors` | list | Q8: per-session preference values. |
| `qc_phase1_score` | float | Blind-critic Phase 1 score (0–1). |
| `qc_phase2_grounded` | bool | Grounded in evidence anchors. |
| `qc_phase3_flagged` | bool | Flagged by the blind critic. |
| `source_challenge_id` | string | Planted challenge ID this QA derives from. |

Each `evidence_anchors` entry contains `session_index`, `turn_id`,
`speaker_display_name`, `message_excerpt` (exact quote), and `relevance`
(why this turn supports the answer).

## Query types

| ID | Label | What it tests |
|---|---|---|
| Q1 | single_contact_recall | One person's implicit preference. |
| Q2 | group_decision_recall | A group decision, plus who dissented. |
| Q3 | multi_contact_aggregation | All members' preferences on one domain. |
| Q4 | contamination_probe | Correct attribution when two speakers are confusable. |
| Q5 | theory_of_mind_reference | What A revealed about B's preference. |
| Q6 | norm_vs_individual | Whether a group norm truly applies to everyone. |
| Q7 | relational_edge_query | Relationship history revealed in conversation. |
| Q8 | temporal_preference_evolution | How a preference changed across sessions. |
| Q9 | departed_member_recall | Last known preference of a member who left the group. |

Q9 is generated only for networks with modelled departure.

## Dataset statistics

- Networks: **43** (small/medium/large breakdown)
- Conversation turns: **7355**
- QA pairs: **1031**
- Group types: close_friends, family, recreational, interest_community, acquaintance_network
- Languages: English
- License: CC BY 4.0

## How the data was built

1. **Stage 1 — Networks.** Personas, relationship edges, and group norms
   are sampled with deliberate constraints (Big Five spread, communication
   profile diversity, planted dissenters, designed preference histories).
2. **Stage 2 — Conversations.** WhatsApp-style multi-session corpora are
   generated with planted memory challenges (implicit preferences,
   theory-of-mind references, consensus traps, temporal shifts, false-
   attribution seeds, relational disclosures).
3. **Stage 3 — QA.** Q1–Q9 pairs are generated against the planted
   challenges. Every pair carries evidence anchors and is passed through a
   three-phase blind-critic QC (grounding, inference depth, difficulty
   calibration).

The blind-critic QC mean across all QA pairs is **0.952**;
no QA pair is shipped without grounded evidence anchors.

## Intended use

- Evaluating *memory systems* (Mem0, LangMem, Graphiti, Cognee,
  custom retrieval architectures) on multi-party attribution.
- Comparing memory architectures against full-context oracle baselines.
- Studying the gap between context-window reasoning and memory-system
  retrieval on the same conversations.

Out of scope: claims about real-human conversational memory, claims about
cross-cultural attribution, or claims about deployed-product behaviour
without further validation.

## Limitations

- All data is synthetic. Generated names skew toward English-language
  conventions; cultural references reflect the generator model's training
  distribution.
- Group sizes top out at 30; very large communities are not represented.
- The benchmark is preference-and-attribution centric; it does not cover
  task-state tracking, code-grounded conversations, or visual modalities.

## Citation

```bibtex
@misc{socialmembench2026,
  title  = {SocialMemBench: Are AI Memory Systems Ready for Social Group Settings?},
  author = {Anonymous Submission},
  year   = {2026},
  note   = {NeurIPS 2026 Datasets and Benchmarks Track submission},
  url    = {https://huggingface.co/datasets/anon4data/socialmembench}
}
```

## License

CC BY 4.0. You are free to use, share, and adapt with attribution.

## Acknowledgements

Generated with Claude (Anthropic) via the SocialMemBench pipeline. The
generation skills, schemas, and evaluation code are released at the
anonymous repo linked from the paper.