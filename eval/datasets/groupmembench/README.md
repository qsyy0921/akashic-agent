---
language:
- en
pretty_name: GroupMemBench
size_categories:
- 100K<n<1M
tags:
- memory
- retrieval
- benchmark
- group-conversation
- rag
- multi-channel
task_categories:
- question-answering
- text-retrieval
---

# GroupMemBench — Conversation Data

GroupMemBench is a benchmark for evaluating group-conversation memory systems
on synthetic enterprise channel logs. This repository hosts the **conversation
data** for four domains; the typed evaluation question sets and the reference
RAG baselines live in the companion code repo at
[KimperYang/GroupMemBench](https://github.com/KimperYang/GroupMemBench).

## What's in here

```
data/final/
├── Finance/synthetic_domain_channels_rolevariants_Finance.json
├── Technology/synthetic_domain_channels_rolevariants_Technology.json
├── Healthcare/synthetic_domain_channels_rolevariants_Healthcare.json
└── Manufacturing/synthetic_domain_channels_rolevariants_Manufacturing.json
```

Each file is a JSON object keyed by **channel name**; the value is a
chronologically ordered list of messages. Every message carries:

| field            | description                                                  |
|------------------|--------------------------------------------------------------|
| `msg_node`       | unique message id (`Msg_<n>`)                                |
| `content`        | natural-language message body                                |
| `author`         | anonymised user id (`User_<n>`)                              |
| `role`           | role label (e.g. *Compliance Officer*, *Plant Manager*)      |
| `timestamp`      | ISO 8601                                                     |
| `reply_to`       | parent `msg_node` or `null`                                  |
| `phase_name`     | the decision/work phase the message belongs to               |
| `topic`          | thread topic                                                 |
| `is_noise`       | `true` for distractor messages                               |
| `is_decision_point` | `true` when the message records a decision change         |
| `tone` / `style` / `expertise` | role-conditioned style tags                    |

Counts:

| domain        | channels | messages |
|---------------|---------:|---------:|
| Finance       | 6        | 30,000   |
| Technology    | 7        | 30,000   |
| Healthcare    | 10       | ~22,000  |
| Manufacturing | 10       | ~22,000  |

## Loading

```python
import json
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    repo_id="kimperyang/GroupMemBench",
    repo_type="dataset",
    filename="data/final/Finance/synthetic_domain_channels_rolevariants_Finance.json",
)
data = json.load(open(path))
for channel, messages in data.items():
    print(channel, len(messages))
```

## Intended use

The data is designed for stress-testing memory / retrieval systems on
multi-author, multi-channel, multi-phase enterprise-style conversations. The
companion question sets cover six question types — `multi_hop`,
`knowledge_update`, `temporal`, `user_implicit`, `term_ambiguity`,
`abstention` — that target orthogonal failure modes of naïve retrieval.

## Provenance

All conversations are **synthetic**. There is no real user data; authors are
generic `User_<n>` ids and content is generated to plausibly mimic enterprise
channel discussions, including topic-aware noise and decision-point updates.

## Citation

TODO — citation will be added when the accompanying paper is released.
