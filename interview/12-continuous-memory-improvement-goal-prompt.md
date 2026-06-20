# 12. Akashic Memory 持续改进 Prompt

这个文档是 Akashic 个人助手 memory 能力持续改进时使用的固定 prompt 文档。

它采用“双层机制”：

- **固定短版 Codex Goal Prompt**：新开 Codex goal/thread 时直接复制，只作为入口指针和执行约束，不承载动态目标。独立文件见 `interview/codex-goal-short-prompt.md`。
- **维护配置**：真正会随实验进展变化的目标、指标、方法、约束、论文依据、评测协议和下一步动作。

后续如果评测指标、最佳方法、论文依据、失败分析或下一步方向变化，只更新“维护配置”。短版 prompt 默认不改；只有文档路径、工作目录或入口规则本身变化时才允许修改短版。

## 固定短版 Codex Goal Prompt 入口

固定短版的唯一来源是 `interview/codex-goal-short-prompt.md`。本文不再同步复制短版正文，避免形成两个需要维护的入口版本。

短版是稳定启动器，不是状态文档。后续 method 编号、最新指标、论文依据、失败分析、下一步实验、评测协议变化，都只维护在本文“维护配置”和对应方法目录中。

短版使用规则：
- 这段短版是丢给 Codex Goal 的固定入口，后续不要把 method 编号、最新指标、临时结论或具体实验计划写进短版。
- 短版默认不可变；除非工作目录、文档路径或入口规则本身变化，否则不要修改短版。
- 需要维护、演进、修正的内容全部写入下方“维护配置”。
- 每次新开 goal 时，Codex 必须先读本文，再按维护配置里的最新状态继续，而不是依赖短版自身携带最新信息。
- 如果只是新增方法、更新指标、修正失败分析、调整下一步实验或补充论文依据，只能改“维护配置”和对应方法文档，不能改短版。
- 如果发现短版和本文状态不一致，以本文“维护配置”为准；短版只负责定位本文。

## 维护配置

### 目标

目标：
持续改进 Akashic 个人助手的长期记忆能力，要求每次改进都可复现、可量化、可解释。当前 SocialMemBench 50 条样本 baseline judge_acc=76%，当前最佳方法为 method_17_structured_answer_contract，clean adjusted judge_acc=88%。本轮“突破 82%”目标已完成；下一轮目标是在不牺牲 88% overall 的前提下，优先提升 single-session-preference 并减少隐含偏好归因错误。

### 工作目录

工作目录：
E:\agent\akashic

### 硬性约束

硬性约束：
1. 不要重启 Chrome、QQ、Telegram、MCP 服务或浏览器会话。
2. 不要修改生产 QQ/Telegram/channel 接入逻辑，除非发现影响评测的通用 bug，并且必须做最小验证。
3. 不要泄露任何 API key。运行 Mimo 评测时使用：
   $env:MIMO_API_KEY=[Environment]::GetEnvironmentVariable("MIMO_API_KEY","User")
   $env:PYTHONIOENCODING="utf-8"
   $env:PYTHONUTF8="1"
4. Windows PowerShell 下注意 UTF-8，避免 GBK 编码导致模型回答或日志崩溃。
5. 每一种方法都必须独立编号并保留，不要覆盖旧方法代码、指标和结果文件。
6. 如果 Mimo 429 导致回答变成“处理消息时出错，请稍后再试。”，只删除该 case 的 result.json 和 trace.log，然后用 --resume-auto 补跑。
7. 新方法必须先基于论文笔记和已有错误分析，不要凭直觉写宽泛规则。

### 开始前必须阅读

开始前必须阅读：
1. interview/11-2026-top-conference-memory-reading-notes.md
2. interview/memory_evaluation_report.md
3. experiments/memory_methods/README.md
4. experiments/memory_methods/<最新方法>/error_cases.json

### 研究依据

研究依据：
重点参考 2026 顶会/高质量论文结论：
- APEX-MEM：append-only 的时间/实体/事件记忆。
- Memora/FAMA：遗忘感知、旧记忆失效评测。
- MemSearcher：面向问题构建紧凑 working memory。
- RecMem：基于重复出现触发记忆巩固。
- OCR-Memory：稳定 source anchor 和忠实证据恢复。
- MemGuide：意图对齐检索 + missing-slot filtering。
- MemoryAgentBench / MemoryArena / AMA-Bench：增量交互和行动型 memory 评测。

### 当前经验事实

当前经验事实：
- baseline_current：76%。
- method_01_intent_aware_retrieval：82%，single-session-user 最强，但延迟高。
- method_04_memory_update_versioning：82%，延迟低于 method_01。
- method_05_hybrid_intent_temporal_rerank：76%，负结果，伤害归因题。
- method_06_adaptive_intent_versioned：82%，knowledge-update 达到 90.9%，但偏好和说话人归因仍弱。
- method_07_source_grounded_slot_resolver：70%（adjusted），负结果。把 source-grounded evidence rows 和回答规则塞进 `recall_memory` 的 summary/signals 会污染模型的选项推理；它证明“summary 层补提示”不是可靠路径。
- method_08_raw_message_first_resolver：已实现并 smoke 通过，但 targeted speaker-attribution case `socialmem_Q4_d4e5f6a7` 仍失败。raw evidence table 和 `candidate_answer_hints` 已识别 Jordan，Mimo 仍选择 Priya，说明下一瓶颈是“归因决策本身必须结构化/确定化”，不是继续加提示字段。
- method_09_deterministic_attribution_resolver：66%（adjusted），负结果。它用 deterministic selected_candidate 和状态化 search/fetch wrapper 修复过 `socialmem_Q4_d4e5f6a7` 的 isolated targeted v3，但 full 50 退化到 66%，knowledge-update 只有 36.4%。这证明 benchmark wrapper 级强约束会放大工具链脆弱性，不能替代生产级 event/entity/source schema。
- method_10_production_structured_memory_schema：70%（adjusted），基础设施结果但未超过 baseline。它把 raw_event、entity、event_fact、memory_assertion、relation_fact、validity/version/source_ref 写入生产 `memory2`，并从 SessionStore 回填 speaker/content/message_index，再通过 `signals.structured_evidence` 暴露；但最终模型仍会忽略或误用结构化证据，speaker/order/implicit preference 仍弱。
- method_11_structured_candidate_resolver：70%（adjusted），负结果。它已经直接读取 `memory_raw_events` 并构造 `selected_evidence_table`，但证据集合仍然过宽过噪，`avg_tool_result_chars` 约 58.7k，15 个错误中 8 个仍是 retrieval_failure。它证明“直接查 raw_event 表”本身还不够，下一步必须让 resolver 使用原始问题/options 做 session/entity 范围收缩，而不是只依赖模型生成的 recall query。
- method_12_question_aware_structured_router：76%（adjusted），恢复到 baseline 但未超过 82%。它把原始 question/options 通过 benchmark-only context 传给 recall tool，先用 `session_key=lme:<question_id>` 收缩 `memory_raw_events`，去掉泛词和 option-name-only 打分，并输出 `slot_decision`。它把 retrieval_failure 从 8 降到 4，single-session-preference 达到 88.24%，但 exception/negation、who-first 和 update trajectory 仍会在最终回答阶段被误用。
- method_13_slot_decision_answer_planner：74%，负结果。它把 `slot_decision` 转成 `answer_plan`，并在 targeted `socialmem_Q2_e1f2a3b4` 上修复了 Sam 例外判断；但 full 50 从 method_12 的 76% 退化到 74%，single-session-preference 从 88.24% 降到 76.47%，`avg_tool_result_chars` 增至 82.6k，说明继续加厚 answer-time payload 会引入噪声和成本，不能解决 memory_write/update 质量问题。
- method_14_consolidated_memory_write_quality：82%（adjusted），正向结果但未突破。它在 frozen workspace 中从 `memory_raw_events` 回填 compact `preference_fact`、`exception_fact`、`relationship_fact`、`decision_fact`、`update_trajectory_fact`，保留 source_refs/speaker/date/message_index/quote，并让检索优先消费 `consolidated_fact_table`。它把 method_13 的 74% 拉回到 82%，平均延迟 18.4036s，是目前达到 82% 的最快方法；但 single-session-preference 仍为 76.47%，剩余 9 个错误集中在 `retrieved_but_answer_wrong`、`personal_preference_attribution_error`、`retrieved_but_evidence_unused`，说明瓶颈转为 compact facts 上的候选选择和最终答案精度。
- method_15_consolidated_fact_precision_rerank：74%（adjusted），负结果。它用 compact facts + disputed raw rows 做 candidate-group precision rerank，修复了 `socialmem_Q4_d4e5f6a7`、`socialmem_Q8_v4s4c1`、`socialmem_Q6_a5s3c2`，但新增 7 个 method_14 已正确的回归。结论：不能把 precision rerank 作为宽泛 answer-time payload 暴露给最终模型。
- method_16_conservative_precision_gate：74%（adjusted），负结果。它只在 option-style speaker / who-first / explicit norm-exception 问题上启用 precision gate，并修复了 who-first 的 global `source_ref` 顺序 tie-break；但 full 50 仍为 74%，single-session-user 达到 86.36%，knowledge-update 掉到 63.64%。结论：保守门控还不够，下一步瓶颈是最终答案控制和结构化验证。
- method_17_structured_answer_contract：88%（clean adjusted），正向结果，首次突破 82% 上限。它基于 method_14 compact facts，只在高置信 option slot 上生成极小 `final_answer_contract`，并由 verifier 确认 source_ref 支持后才覆盖最终答案。clean full run 中只有 `socialmem_Q4_d4e5f6a7` 和 `socialmem_Q6_a5s3c2` 两个 contract 生效，均正确且只引用单条关键 source_ref。knowledge-update 为 90.91%，single-session-user 为 95.45%，single-session-preference 仍为 76.47%。剩余 6 个错误集中在隐含偏好推理、证据已取回但答案没有抓住 latent rationale，以及少量 update trajectory 解释偏差。

### 当前主要失败类型

当前主要失败类型：
1. 从回避、含蓄表达中推断隐含偏好，例如把“just prefer chicken”误判成鸡肉偏好而不是蘑菇回避。
2. 偏好归因需要识别 latent rationale，而不只是复述字面表态。
3. retrieved evidence 已取回，但最终答案没有抓住 gold answer 需要的对比点。
4. 多 session update trajectory 仍会被过度概括，例如把持续升级误写成后期恢复。
5. final-answer contract 目前只覆盖窄 option slot；剩余 preference/update 问题不能靠扩大 payload 粗暴覆盖。

### 下一步优先实现

下一步优先实现：
本轮 method_17_structured_answer_contract 已完成并达到成功标准。下一轮如继续开 Goal，优先探索 method_18_implicit_preference_evidence_contract：不要扩大宽泛 recall payload，而是在 method_17 的 verifier 框架内，只对隐含偏好/回避类问题生成可验证的 preference evidence contract；如果不能从 source refs 证明 latent rationale，就回退 method_17。

### method_17 要求

method_17 要求与结果：
1. 固定短版 Codex Goal Prompt 不改；所有 method_17 目标、实验记录和指标都只维护在本文与方法文档中。
2. 以 method_14 为主要正向基线，同时保留 method_15/16 负结果和产物，不删除旧方法代码与结果。
3. 不要继续加厚 `recall_memory` 的 answer-time payload；只允许输出一个极小的 `final_answer_contract`，字段包括 `answer`, `answer_type`, `supporting_source_refs`, `must_cite`, `allowed_scope`, `confidence`, `verifier_status`。
4. 针对 method_14/15/16 的剩余错误优先优化：
   - `retrieved_but_answer_wrong`
   - `personal_preference_attribution_error`
   - `retrieved_but_evidence_unused`
   - `memory_update_not_versioned`
5. final-answer contract 必须只来自 source-backed compact facts 或 selected raw evidence；如果 verifier 不能确认 source refs 支持答案，就必须回退到 method_14 payload，不强行覆盖。
6. 对 preference attribution，contract 的 `answer` 必须同时满足 speaker/source_ref 支持和 preference predicate 支持；不能仅凭同场景共现或弱相关事实升级为偏好。
7. 对 update/change，contract 只能输出 current fact，并把 invalidated/previous fact 放在 `superseded_source_refs`；最终答案不得把旧计划和新计划并列成同等结论。
8. 对 exception/negation，contract 必须区分 `norm_setter` 和 `exception_actor`；不能把规则制定者当成不遵守规则的人。
9. raw events 只能作为 verifier 的 source window，不要作为宽泛补充表返回。
10. method_17 已超过 82%，最终 clean adjusted judge_acc=88%，raw provider pollution 和 clean rerun 均已保留。剩余瓶颈不是 contract 注入，而是 contract 覆盖范围之外的隐含偏好推理和 update trajectory 解释。

### 每个新方法必须保留

每个新方法必须保留：
- experiments/memory_methods/<method_id>/config.json
- experiments/memory_methods/<method_id>/README.md
- experiments/memory_methods/<method_id>/source_snapshot/
- eval/results/memory_methods/<method_id>.json
- experiments/memory_methods/<method_id>/metrics.json
- experiments/memory_methods/<method_id>/error_cases.json

### 标准评测协议

标准评测协议：
使用冻结 baseline memory workspace，避免 ingest 方差。

运行评测：

python -m eval.longmemeval.run `
  --config eval/agent_memory_bench_config.toml `
  --data eval/agent_memory_bench_socialmem_full_local.json `
  --workspace runtime/eval/memory_methods/<method_id> `
  --output eval/results/memory_methods/<method_id>.json `
  --limit 50 `
  --qa-only `
  --resume-auto `
  --timeout 240 `
  --method-config experiments/memory_methods/<method_id>/config.json

### 评测后汇总

评测后汇总：

python -m eval.longmemeval.summarize_method_results `
  --result eval/results/memory_methods/<method_id>.json `
  --method-dir experiments/memory_methods/<method_id> `
  --method-id <method_id> `
  --run-log eval/runs/memory_methods/<method_id>.log

### 验证要求

验证要求：
1. 对所有改动的 Python 文件运行 py_compile。
2. 至少运行：
   pytest -q -c NUL -p no:cacheprovider tests/test_longmemeval_methods.py tests/test_longmemeval_judge.py
3. 如果改了工具层，补充最小直接验证。
4. 当前环境可能没有 pytest_asyncio；必要时可用 asyncio.run 直接验证 async 工具行为。
5. 最终回复前确认没有评测进程还在运行。

### 文档更新要求

文档更新要求：
每次完整方法评测后，更新：
1. experiments/memory_methods/README.md
2. interview/memory_evaluation_report.md
3. experiments/memory_methods/<method_id>/README.md
4. 如果指标、最佳方法、失败类型、下一步方向或评测协议变化，更新本文“维护配置”。
5. 不要因为实验进展修改 `interview/codex-goal-short-prompt.md` 或上方“固定短版 Codex Goal Prompt”；短版只在文档路径、工作目录或入口规则变化时更新。

### 报告要求

报告要求：
不要只报告 overall accuracy。必须报告：
- judge_acc
- 相对 baseline 的 delta
- F1
- avg_elapsed_s
- avg_token_usage 估计值
- 分类型准确率：
  - knowledge-update
  - single-session-preference
  - single-session-user
- error type counts
- 具体剩余失败类型

### 成功标准

成功标准：
本轮首要目标：在 50 条 SocialMemBench 上 judge_acc 超过 82%。method_17 clean adjusted result 已达到 88%。

如果没有超过：
1. 保留该方法作为可复现负结果。
2. 说明哪些类型提升、哪些类型下降。
3. 明确下一个架构瓶颈。
4. 不要隐藏失败方法。

### 持续迭代流程

持续迭代流程：
1. 读最新论文笔记和评测报告。
2. 查看最新 error_cases.json。
3. 提出一个最小、论文驱动的方法。
4. 实现为 method_XX，不动生产服务。
5. 跑完整 50 条评测。
6. 清理并补跑 provider 污染样本。
7. 汇总指标和错误类型。
8. 更新文档。
9. 判断下一步是继续 answer-time resolver，还是需要进一步改生产 memory 写入/检索链路。

## 维护说明

- 这个文档替代早期临时口头 prompt。
- 新开 Codex goal 时复制 `interview/codex-goal-short-prompt.md` 中的短版，短版不随指标、方法编号、最佳结果或下一步方向变化而改。
- 指标、最佳方法、论文依据、下一步 method、失败分析和评测协议都维护在“维护配置”里。
- 如果后续想改短版，先判断是不是入口规则真的变化；如果只是任务状态变化，只能改维护配置。
- method_10 已完成生产 memory schema 的第一版落地：
  - 显式 event/entity/source/assertion/relation 表
  - validity state 和 version_of
  - speaker_id/message_index/source_ref 全链路保留
  - SessionStore raw message 回填
- method_11 已完成直接读取结构化表的第一版 full-run：
  - `memory_raw_events` 直接检索
  - selected evidence table
  - candidate resolution
  - full 50 adjusted judge_acc 仍为 70%，低于 baseline
- method_12 已完成 question-aware structured router：
  - 使用原始 question/options 做 question-aware slot extraction
  - 先做 session/entity scope 收缩
  - 去掉泛词和名字单独加分
  - 将 selected evidence table 压缩到 4-8 行
  - 显式输出 supporting/contradicting source refs 和 evidence_gap
  - full 50 adjusted judge_acc 为 76%，恢复 baseline，但未超过 82%
- method_13 已完成 slot decision answer planner：
  - targeted `socialmem_Q2_e1f2a3b4` 已修复
  - full 50 judge_acc 为 74%，低于 baseline 和 method_12
  - 结论：不要继续加厚 answer-time payload，下一步转向 memory 写入/consolidation 质量
- method_14 已完成 consolidated memory write quality：
  - full 50 adjusted judge_acc 为 82%，追平 method_01/method_04/method_06，但未突破
  - 平均延迟 18.4036s，是目前达到 82% 的最快方法
  - knowledge-update 为 81.82%，single-session-user 为 86.36%，single-session-preference 仍只有 76.47%
  - 结论：compact facts 有效，但下一步必须提升 candidate precision、preference attribution 和版本/冲突选择
- method_15 已完成 consolidated fact precision rerank：
  - full 50 adjusted judge_acc 为 74%，低于 baseline 和 method_14
  - 修复了 3 个 method_14 错误，但新增 7 个 method_14 正确 case 的回归
  - 结论：不能把 precision candidate decision 作为宽泛 answer-time payload 暴露给最终模型
- method_16 已完成 conservative precision gate：
  - full 50 adjusted judge_acc 为 74%，低于 baseline 和 method_14
  - targeted smoke 修复了 who-first global source_ref 排序问题，single-session-user 为 86.36%
  - knowledge-update 降到 63.64%，说明保守门控仍不能稳定控制最终答案
  - 结论：下一步转向结构化 final-answer contract + source-backed verifier
- method_17 已完成 structured answer contract：
  - clean adjusted full 50 judge_acc 为 88%，首次突破 82% 上限
  - 基于 method_14 compact facts，不扩大 recall payload
  - 只在高置信 slot 上生成极小 `final_answer_contract`
  - verifier 必须确认 answer 被 source_refs 支持，否则回退 method_14
  - full run 中只有两个 contract 生效，均正确且 source-backed
  - 结论：小而严的 final-answer contract 有效；下一轮不要扩大候选表，而应针对剩余隐含偏好错误设计可验证的 preference evidence contract
- method_07 已低于 baseline，后续不要再采用“改写 recall_memory summary 来塞证据规则”的方案。
- method_09 已低于 baseline，后续不要再采用“在 benchmark wrapper 中强行约束 search/fetch”的方案。
- method_10 已低于 baseline，后续不要停留在“把结构化证据暴露给模型看”；必须让检索、候选选择和版本判断主动消费结构化 schema。
- method_11 已低于 baseline，后续不要再返回宽而全的 raw_event 证据表；必须先收缩问题范围和候选实体，再给最终模型小证据集。
- method_12 已恢复 baseline 但未突破，后续不要继续加大 raw_event 检索。
- method_13 已证明 answer plan 方向 full-run 退化，后续不要再通过更大的 answer-time plan 修补；应把结构化事实前移到写入/巩固阶段。
- method_14 已证明写入/巩固方向能追平最佳且降低延迟，但仍卡在 82%；后续不要再把目标写成“先做 consolidation”，应转向 compact fact 上的候选精度和最终选择。
- method_15 已证明宽泛 candidate precision rerank 会回归，后续不要再把候选表和规则直接暴露给最终模型。
- method_16 已证明只做保守门控仍不够，后续必须把“最终答案是什么”结构化并验证，而不是期待模型自行遵守 evidence table。
- method_17 已证明结构化且可验证的 final-answer contract 能突破 82%；后续不要把 contract 泛化成大段 answer plan，应只在 verifier 可证明 source refs 支持 latent preference 或 update trajectory 时扩展。
