---
configs:
  - config_name: dialogues
    data_files:
      - split: train
        path: "0[1-5]/dialogue.json"
  - config_name: qars
    data_files:
      - split: train
        path: "0[1-5]/qa_*.json"
  - config_name: profiles
    data_files:
      - split: train
        path: "profiles.json"
---

# EverMemBench-Dynamic

A benchmark dataset for evaluating long-term memory capabilities in conversational AI systems.

## Configurations

This dataset has three configurations (subsets):

### `dialogues`
Multi-turn group dialogues spanning ~250 days per topic, organized by date and chat group.

```python
from datasets import load_dataset
ds = load_dataset("EverMind-AI/EverMemBench-Dynamic", "dialogues")
```

| Column | Type | Description |
|--------|------|-------------|
| `topic_id` | string | Topic identifier (01-05) |
| `date` | string | Date of the dialogues (YYYY-MM-DD) |
| `dialogues` | dict | Contains `Group 1`, `Group 2`, `Group 3` keys, each mapping to a list of messages or null |

Each message has: `speaker`, `time`, `dialogue`, `message_index`.

### `qars`
Question-Answer-Reference triples for evaluating memory retrieval.

```python
from datasets import load_dataset
ds = load_dataset("EverMind-AI/EverMemBench-Dynamic", "qars")
```

| Column | Type | Description |
|--------|------|-------------|
| `topic_id` | string | Topic identifier (01-05) |
| `id` | string | Unique question ID |
| `Q` | string | Question |
| `A` | string | Ground truth answer |
| `R` | list | Reference evidence entries (see below) |
| `options` | dict or null | Multiple choice options (A/B/C/D) if applicable |

#### Locating reference evidence

Each entry in the `R` (Reference) list contains three fields that together pinpoint the supporting evidence within the `dialogues` config:

| Field | Example | Description |
|-------|---------|-------------|
| `date` | `"2025-10-22"` | Matches the `date` field in `dialogues` |
| `group` | `"Group 3"` | Matches a group key inside the `dialogues` dict |
| `message_index` | `"1, 4-6, 8, 10-11"` | Refers to `message_index` values of individual messages within that group |

The `message_index` field is a **string** that may contain:
- A single index: `"4"`
- A comma-separated list: `"1, 4-7"`
- Ranges: `"2-3, 6-7"` (meaning messages 2, 3, 6, 7)
- Mixed: `"1, 4-6, 8, 10-11"`

To extract the referenced messages, filter the dialogue messages where `topic_id`, `date`, and group match, then select messages whose `message_index` falls within the specified indices/ranges.

**Why does a single question reference multiple evidence entries?** Each question is designed around a localized conversational context. While the ground-truth answer may reside in a specific message, correctly retrieving and answering the question requires understanding the surrounding context — the relevant slice of the conversation that leads up to or follows the key message. Therefore, `R` captures the full contextual snippet (potentially spanning multiple dates and groups) needed to reason about the answer, not just the single message containing it.

### `profiles`
Character profiles of all 170 unique members across the 5 topics.

```python
from datasets import load_dataset
ds = load_dataset("EverMind-AI/EverMemBench-Dynamic", "profiles")
```

| Column | Type | Description |
|--------|------|-------------|
| `Name` | string | Member name |
| `ID` | string | Unique member ID |
| `Gender` | string | Gender |
| `Age` | string | Age |
| `Education` | string | Education level |
| `Major` | string | Major / field of study |
| `Dept` | string | Department |
| `Title` | string | Job title |
| `Rank` | string | Rank level |
| `Tenure` | string | Years of tenure |
| `Skills_List` | list | List of skills |
| `Communication_Profile` | dict | Communication style attributes |
| `Big_Five_Profile` | dict | Big Five personality traits |
| `Interests` | list | Personal interests |
| `Marital_Status` | string | Marital status |
