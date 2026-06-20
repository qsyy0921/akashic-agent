# 09. Memory 文献驱动改进计划

本文只讨论 Akashic 作为个人助手时的 memory 改进。当前系统已经具备 `source_ref`、SQLite 结构化存储、embedding、关键词召回、RRF 融合、`active/superseded`、`memory_replacements`、post-response 写入和 dashboard 能力。接下来的重点不是重写系统，而是在现有架构上补齐写入门控、状态更新、证据反馈和可量化评估。

## 相关论文结论

### A-MAC: Adaptive Memory Admission Control for LLM Agents, 2026

论文链接：https://arxiv.org/abs/2603.04549

核心观点：长期记忆首先要控制“什么值得写入”。A-MAC 把写入决策拆成 future utility、factual confidence、semantic novelty、temporal recency、content type prior 五个可解释因子，而不是完全交给 LLM 随机判断。

对 Akashic 的启发：

- 当前写入已经有较高门槛 prompt，但缺少可解释分数和可调阈值。
- 应该把“候选记忆生成”和“是否进入长期记忆”拆成两步。
- 每条候选记忆都记录 `utility/confidence/novelty/recency/type_prior/admission_score/reject_reason`。
- dashboard 可以展示被拒绝的候选，方便人工审计。

优先级：最高。它直接对应当前评测里的 `memory_write_failure` 和 `personal_preference_attribution_error`。

### Memora / FAMA: From Recall to Forgetting, 2026

论文链接：https://arxiv.org/abs/2604.20006

核心观点：个人助手不能只考“是否记得旧事实”，还要考“是否忘掉或覆盖已经失效的旧记忆”。论文提出 FAMA，用来惩罚模型引用 obsolete / invalid memory。

对 Akashic 的启发：

- 当前已经有 `active/superseded` 和 `memory_replacements`，但评测还主要看回答正确率。
- 应新增 forgetting-aware 指标：回答是否用了已 supersede 的旧记忆。
- 对 `knowledge-update` 和 preference 变化问题，必须检查回答是否引用最新 active 版本。
- `error_cases.json` 应额外标注 `stale_memory_used`、`missing_supersede`、`obsolete_evidence_used`。

优先级：最高。它能把“记忆更新”从口头设计变成可测指标。

### MemConflict: Long-Term Memory Systems Under Conflict, 2026

论文链接：https://arxiv.org/pdf/2605.20926

核心观点：memory conflict 不只是时间更新，还包括偏好漂移、否定、来源冲突、上下文记忆与模型参数知识冲突等。只用“新替旧”会漏掉很多真实场景。

对 Akashic 的启发：

- `supersede` 决策需要记录 conflict 类型，而不是只标记旧条目失效。
- 建议增加 `conflict_type`：`temporal_update`、`explicit_negation`、`preference_drift`、`source_conflict`、`scope_conflict`。
- 检索时如果命中冲突链，优先返回最新 active item，同时把旧 item 作为审计证据，不注入给回答模型。

优先级：高。它能提升长期个人偏好变化场景的可靠性。

### RMM: Reflective Memory Management, ACL 2025

论文链接：https://aclanthology.org/2025.acl-long.413/

核心观点：长对话 memory 需要前向反思和后向反思。前向反思把对话按 utterance、turn、session 多粒度总结；后向反思根据模型实际引用的证据来优化检索。

对 Akashic 的启发：

- 当前 `source_ref` 和 citation 插件已经能记录回答依赖的 memory。
- 可以在回答结束后记录 `retrieved_ids`、`cited_ids`、`useful_ids`、`ignored_ids`。
- 对被引用且 judge 正确的 memory 增强 reinforcement；对频繁召回但未引用的 memory 降权。
- 这比静态 RRF 更适合个人助手，因为用户偏好和项目状态会反复出现。

优先级：高。它直接改善“检索到但没有使用证据”的错误。

### A-MEM: Agentic Memory for LLM Agents, 2025

论文链接：https://arxiv.org/abs/2502.12110

核心观点：记忆不应该只是扁平摘要列表，而应像 Zettelkasten 一样包含上下文、关键词、标签和互相关联的 note。新记忆写入时可以反过来更新旧记忆的属性和链接。

对 Akashic 的启发：

- 不必一开始引入完整图数据库，可以先在 `extra_json` 里保存 `keywords/tags/topic/source_entities/linked_item_ids`。
- 新增 `memory_edges` 表，记录 `related_to/supports/contradicts/supersedes/same_topic`。
- 检索时先召回 seed memory，再扩展 1-hop related memories，最后 rerank。

优先级：中高。它适合解决跨 session、多证据拼接问题，但实现复杂度高于 write gate。

### Zep / Graphiti: Temporal Knowledge Graph for Agent Memory, 2025

论文链接：https://arxiv.org/abs/2501.13956

核心观点：企业和个人助手需要动态知识整合，而不是静态 RAG。Temporal knowledge graph 可以保存实体、关系、事件和时间有效性，提升跨 session 综合和时间推理效率。

对 Akashic 的启发：

- 当前 memory item 有 `happened_at`，但缺少实体关系层。
- 可以先做轻量版本：`memory_entities`、`memory_edges`、`valid_from/valid_to`。
- 个人助手最有价值的实体包括：用户、项目、工具、账号、文件、论文、数据集、任务。
- 不建议立刻引入外部图数据库；SQLite 表足够做第一版。

优先级：中高。适合作为第二阶段核心亮点。

### MIRIX: Multi-Agent Memory System, 2025

论文链接：https://arxiv.org/abs/2507.07957

核心观点：memory 应分成 Core、Episodic、Semantic、Procedural、Resource、Knowledge Vault 等类型，并由不同 agent / manager 协调更新和检索。

对 Akashic 的启发：

- 当前已有 `profile/preference/procedure/event`，可以映射为个人助手版 memory taxonomy。
- 建议补 `resource` 和 `project_state` 两类：
  - `resource`：账号、路径、URL、论文库、数据集、MCP 服务。
  - `project_state`：当前项目目标、已完成实验、指标、阻塞点。
- 不需要马上做多 agent，先把 memory type 和工具路由做清楚。

优先级：中。它更适合架构表达和面试讲解。

### PAMU: Preference-Aware Memory Update, 2025

论文链接：https://arxiv.org/abs/2510.09720

核心观点：用户偏好不是简单覆盖，有短期波动和长期倾向。PAMU 用 sliding window 和 EMA 表示短期与长期偏好。

对 Akashic 的启发：

- 对 preference 不要只保存一句话摘要。
- 增加 `preference_key`、`preference_value`、`short_term_score`、`long_term_score`、`last_observed_at`。
- 用户临时要求不应立刻覆盖长期偏好，除非重复出现或明确声明“以后都这样”。

优先级：中。它能减少偏好污染。

### MemoryAgentBench / Memory-R1 / Evo-Memory

论文链接：

- MemoryAgentBench：https://arxiv.org/abs/2507.05257
- Memory-R1：https://arxiv.org/abs/2508.19828
- Evo-Memory：https://arxiv.org/abs/2511.20857

核心观点：memory agent 的能力不只是检索，还包括 test-time learning、long-range understanding、selective forgetting，以及 ADD/UPDATE/DELETE/NOOP 等主动记忆操作。

对 Akashic 的启发：

- 当前可以先把 ADD/UPDATE/DELETE/NOOP 做成显式可观测操作，不必立刻 RL。
- 每次写入都记录 action、候选、旧条目、决策原因和结果。
- 后续如果有足够 eval 数据，再考虑训练或优化一个 memory policy。

优先级：中低。RL 不是当前最划算的第一步。

### LoCoMo / LoCoMo-Plus

论文链接：

- LoCoMo：https://aclanthology.org/2024.acl-long.747/
- LoCoMo-Plus：https://arxiv.org/html/2602.10715v1

核心观点：长期对话 memory 不只是事实 QA，还包括事件总结、多跳、时间推理和隐式约束使用。LoCoMo-Plus 特别强调用户状态、目标、价值观这类 latent constraints。

对 Akashic 的启发：

- 当前 SocialMemBench 主要适合个人偏好和用户事实，但还不够覆盖隐式约束。
- 应补充 `constraint_consistency` 指标：回答是否遵守长期偏好、项目目标、用户明确禁忌。
- 面试中可以说：第一阶段用 SocialMemBench 做 QA，第二阶段用 LoCoMo / Memora 做长期状态和遗忘评估。

优先级：中。更偏评测扩展。

## 对当前 Akashic 的判断

当前 50 条 SocialMemBench / Mimo 评测中：

- baseline judge_acc：0.76
- intent-aware retrieval：0.82
- memory update/versioning：0.82
- structured schema 单独使用下降到 0.66
- 主要错误类型仍是写入失败、旧记忆未更新、个人偏好归因错误、检索到但回答未使用证据

这说明：

1. 检索路由有收益，但不是根因。
2. 只给 retrieval result 加结构字段，没有改变写入和更新策略，收益有限甚至会增加噪声。
3. 下一步最应该优化的是写入门控、状态更新和证据反馈，而不是继续调 top_k。

## 推荐改进路线

### Method 05: adaptive_write_gate

目标：减少误写和漏写，让长期记忆进入数据库前有可解释决策。

实现：

- 新增候选记忆阶段：extract candidates，不直接写入。
- 对每个 candidate 计算：
  - `utility`
  - `confidence`
  - `novelty`
  - `recency`
  - `type_prior`
  - `admission_score`
- 低于阈值的候选写入 `memory_candidates`，但不进入 `memory_items`。
- dashboard 展示 rejected/pending/accepted。

指标：

- `memory_write_precision`
- `memory_write_recall`
- `personal_preference_attribution_error`
- `memory_write_failure`
- `avg_write_latency`

预期：优先降低 preference 误写和 profile 反推。

### Method 06: forgetting_aware_versioning

目标：让旧偏好和旧事实不会污染新回答。

实现：

- 给 `memory_replacements` 增加 `conflict_type`、`decision_reason`。
- 对每条 active memory 增加 `valid_from/valid_to`。
- retrieval 默认排除 superseded，但如果问题涉及历史版本，可以显式查历史链。
- eval 增加 FAMA-like 评分：引用 obsolete memory 直接扣分。

指标：

- `fama_acc`
- `stale_memory_used_rate`
- `missing_supersede_count`
- `obsolete_evidence_used_count`

预期：提升 knowledge-update 和 preference drift。

### Method 07: evidence_feedback_rerank

目标：把“模型实际用了哪些记忆”反馈给 retriever。

实现：

- 每轮回答记录 `retrieved_ids`、`cited_ids`、`fetch_message_ids`。
- 如果 judge 正确且引用了某 memory，增加 reinforcement。
- 如果某 memory 高频召回但长期未被引用，降低 hotness 或加入 negative signal。
- 对需要精确事实的问题，强制 fetch_messages 后才能回答。

指标：

- `citation_coverage`
- `retrieved_but_unused_rate`
- `evidence_fetch_rate`
- `answer_grounded_rate`

预期：降低 retrieved-but-answer-wrong。

### Method 08: lightweight_graph_memory

目标：支持跨 session、多跳、实体关系和项目状态推理。

实现：

- 增加 SQLite 表：
  - `memory_entities(id, name, type, aliases, scope, created_at, updated_at)`
  - `memory_edges(src_id, dst_id, relation, memory_item_id, valid_from, valid_to, confidence)`
- 写入 memory item 时抽取 entity 和 relation。
- 检索时先 seed recall，再 1-hop graph expansion，再 rerank。

指标：

- `multi_hop_acc`
- `entity_link_recall`
- `graph_expansion_hit_rate`
- `latency_delta`

预期：提升项目状态、论文库、工具链、账号路径等复杂个人助手任务。

### Method 09: preference_state_model

目标：把 preference 从“文本摘要”升级成可更新状态。

实现：

- 增加 `preference_key`，例如 `answer_style`、`tool_usage`、`paper_interest`。
- 保存 `short_term_score` 和 `long_term_score`。
- 明确“以后都这样”时快速更新；普通临时要求只影响 short-term。

指标：

- `preference_update_acc`
- `preference_pollution_rate`
- `short_term_override_acc`

预期：减少个人助手长期偏好污染。

## 下一步最划算的开发顺序

1. 先做 `method_05_adaptive_write_gate`。
   这是最直接的收益点，也最容易解释为工程质量提升。

2. 同时补 FAMA-like eval。
   没有 forgetting-aware 指标，versioning 的收益很难讲清楚。

3. 再做 `method_07_evidence_feedback_rerank`。
   当前已经有 `source_ref` 和 citation 插件，改造成本低。

4. 最后做轻量 graph memory。
   这会成为架构亮点，但不应该第一步做，避免引入过多不确定性。

## 面试表达

可以这样讲：

> 我阅读了 2025/2026 年 agent memory 相关工作后，发现长期记忆系统的关键不只是向量召回，而是 memory lifecycle：写入、更新、检索、证据使用、遗忘和评估。Akashic 当前已经有 SQLite 结构化存储、source_ref、embedding、RRF 和 supersede 机制。下一阶段我会优先引入 A-MAC 风格的可解释写入门控，以及 Memora/FAMA 风格的遗忘感知评测，把 memory 从“能记住”推进到“能可靠更新、能追溯、能量化优化”。

## 参考链接

- A-MAC: https://arxiv.org/abs/2603.04549
- Memora / FAMA: https://arxiv.org/abs/2604.20006
- MemConflict: https://arxiv.org/pdf/2605.20926
- RMM, ACL 2025: https://aclanthology.org/2025.acl-long.413/
- A-MEM: https://arxiv.org/abs/2502.12110
- Zep / Graphiti: https://arxiv.org/abs/2501.13956
- MIRIX: https://arxiv.org/abs/2507.07957
- PAMU: https://arxiv.org/abs/2510.09720
- MemoryAgentBench: https://arxiv.org/abs/2507.05257
- Memory-R1: https://arxiv.org/abs/2508.19828
- Evo-Memory: https://arxiv.org/abs/2511.20857
- LoCoMo: https://aclanthology.org/2024.acl-long.747/
- LoCoMo-Plus: https://arxiv.org/html/2602.10715v1
- Graph-based Agent Memory Survey: https://arxiv.org/abs/2602.05665
