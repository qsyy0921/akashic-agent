# 08. 算法指标 Baseline

记录日期：2026-06-15  
范围：只统计和算法能力相关的指标，包括长期记忆写入、检索、QA 准确率、tool_search、数据集适配。不包含部署稳定性、Telegram 推送成功率、浏览器登录、Docker、QQ 在线率等工程指标。

## 本次执行的测试

### 算法相关单测

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_tool_search.py `
  tests/test_agent_memory_bench_adapters.py `
  tests/test_embedder_local_hash.py `
  tests/test_loop_consolidation_tags.py `
  tests/test_benchmark_qa_scope.py `
  -q
```

结果：

```text
75 passed in 0.33s
```

覆盖范围：

- `tool_search` 检索和按需解锁逻辑。
- agent memory benchmark 数据集适配器。
- 本地 hash embedding fallback。
- consolidation tag / memory loop 相关逻辑。
- benchmark QA scope 与 memory ingest scope 的一致性。

结论：基础算法组件的单元测试通过，但单测只能证明局部逻辑正确，不能证明端到端长期记忆效果好。

## 数据集规模

| 数据集 | cases | 平均 sessions | 平均 turns | 问题类型 |
| --- | ---: | ---: | ---: | --- |
| EverMemBench-Dynamic local | 2400 | 6.65 | 30.12 | single-session-user: 2400 |
| SocialMemBench local | 1031 | 9.05 | 212.55 | preference: 313, user: 456, knowledge-update: 262 |
| GroupMemBench probe local | 3960 | 1.00 | 120.00 | single-session-user: 3960 |

个人助手主线优先看 EverMemBench 和 SocialMemBench。GroupMemBench 当前只是 probe，不作为个人助手核心指标。

## 端到端 QA Baseline

以下结果来自已有 Mimo 评测输出，没有重新跑大模型全量评估。

| 结果文件 | cases | F1 | EM | errors | recall 调用 | recall 非空 | fetch 调用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `agent_memory_evermem_full_mimo_shard000.json` | 10 | 0.4586 | 0.0000 | 0 | 13 | 0 | 3 |
| `agent_memory_socialmem_full_mimo_smoke.json` | 2 | 0.4605 | 0.0000 | 0 | 3 | 0 | 5 |
| `agent_memory_groupmem_probe_mimo_smoke.json` | 2 | 0.5035 | 0.0000 | 0 | 2 | 0 | 0 |
| `agent_memory_evermem_eval5_mimo.json` | 5 | 0.4619 | 0.0000 | 0 | 8 | 0 | 1 |
| `agent_memory_socialmem_eval5_mimo.json` | 5 | 0.2207 | 0.0000 | 0 | 7 | 0 | 4 |
| `agent_memory_groupmem_eval5_mimo.json` | 5 | 0.6983 | 0.2000 | 0 | 5 | 0 | 0 |

关键观察：

- 所有结果的 `errors=0`，说明评测链路能跑通。
- `recall_memory` 一共调用 38 次，非空返回 0 次，非空召回率 0%。
- QA 仍然能得到 0.22 到 0.70 的 F1，说明部分答案来自 raw history、`fetch_messages` 或模型上下文，而不是结构化 memory recall。
- SocialMem 的 5 条结果 F1 只有 0.2207，说明偏好/人物行为类长期记忆仍然弱。

当前算法结论：

> 主要瓶颈不是工具执行失败，而是长期记忆的“写入粒度 + 检索召回 + 证据使用”没有形成稳定闭环。尤其是 `recall_memory` 非空召回率为 0%，这是优先级最高的问题。

## 2026-06-15 直接优化记录

### 根因：benchmark QA scope 与 ingest scope 不一致

已定位到一个确定性问题：

- benchmark ingest 阶段的 memory scope 来自 session_key，例如 `lme:<question_id>`。
- QA 阶段此前构造的 InboundMessage 使用 `channel="benchmark"`、`chat_id=<question_id>`。
- `recall_memory` 的 `answer` intent 默认会要求 scope match。
- 因此 memory2 中已经写入的条目会被 `benchmark` vs `lme` 的 channel mismatch 过滤掉。

样例验证：

```text
old_scope_hits   = 0   # scope_channel=benchmark
fixed_scope_hits = 1   # scope_channel=lme
```

对应样例：

- question: `evermem_dynamic_F_SH_Top01_001`
- query: `SQL optimization data submission API stress test 300 concurrent users peak CPU`
- memory item: 已存在，scope 为 `lme / evermem_dynamic_F_SH_Top01_001`

### 已完成代码优化

已修改：

- `eval/longmemeval/qa_runner.py`
  - QA 消息 channel 从 `benchmark` 改为 `lme`。
  - chat_id 继续使用 `question_id`。
  - QA session_key 仍保持 `lme:<question_id>:qa`，保证 QA 对话和历史 ingest 会话隔离。

- `eval/personamem/qa_runner.py`
  - QA 消息 channel 改为 `pm`。
  - chat_id 改为 `question_id`，与 `pm:<question_id>` 的 ingest scope 对齐。
  - QA session_key 仍使用独立前缀，避免污染 haystack session。

新增回归测试：

- `tests/test_benchmark_qa_scope.py`

验证点：

- LongMemEval QA 使用 `channel="lme"`、`chat_id=question_id`。
- PersonaMem QA 使用 `channel="pm"`、`chat_id=question_id`。
- QA session 仍然独立，不和 ingest session 混写。

### 无模型召回验证

由于当前 Mimo/OpenAI 兼容配置在实际 QA 复跑时返回 401，未得到有效大模型端到端新分数。本次使用已有 tool_chain 的 `recall_memory.query` 直接在对应 `memory2.db` 上比较关键词召回，验证 scope 修复是否能恢复候选召回。

| 结果文件 | recall query 数 | 旧 scope 非空 | 修复 scope 非空 | 修复后可召回率 |
| --- | ---: | ---: | ---: | ---: |
| EverMem full smoke | 13 | 0 | 3 | 23.08% |
| SocialMem full smoke | 3 | 0 | 1 | 33.33% |
| GroupMem probe smoke | 2 | 0 | 1 | 50.00% |
| EverMem eval5 | 8 | 0 | 3 | 37.50% |
| SocialMem eval5 | 7 | 0 | 2 | 28.57% |
| GroupMem eval5 | 5 | 0 | 1 | 20.00% |

解释：

- scope 修复不是完整解决方案，但能把“必然空召回”的一部分 case 变成可召回。
- 修复后仍有大量 query 无法命中，说明还需要继续优化写入粒度、跨语言 query、关键词抽取、向量检索和 evidence 对齐。
- 目前这个验证是无模型的候选召回验证，不等同于最终 QA F1 提升。

### 端到端复跑状态

尝试运行：

```powershell
.\.venv\Scripts\python.exe -m eval.longmemeval.run `
  --config eval\agent_memory_bench_config.toml `
  --data eval\agent_memory_bench_evermem_full_local.json `
  --workspace eval\runs\agent_memory_evermem_full_mimo_shard000 `
  --output eval\longmemeval\results\agent_memory_evermem_scopefix_qa1.json `
  --limit 1 `
  --qa-only `
  --skip-judge `
  --timeout 240
```

结果：

```text
OpenAI-compatible provider returned 401 invalid_key for model mimo-v2.5-pro.
```

因此本次没有产出有效的新 QA F1/EM。修复后的真实端到端收益需要在 API key 有效后重跑。

## memory2 存储指标

从评测 workspace 中的 `memory/memory2.db` 抽取：

| run | DB 数 | memory items | source_ref 覆盖率 | embedding 覆盖率 | replacements | memory 类型 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| EverMem full smoke | 10 | 6 | 100% | 100% | 0 | event: 6 |
| SocialMem full smoke | 2 | 3 | 100% | 100% | 0 | event: 3 |
| GroupMem probe smoke | 2 | 2 | 100% | 100% | 0 | event: 2 |
| EverMem eval5 | 5 | 2 | 100% | 100% | 0 | event: 2 |
| SocialMem eval5 | 5 | 17 | 100% | 100% | 0 | event: 17 |
| GroupMem eval5 | 5 | 6 | 100% | 100% | 0 | event: 6 |

好的信号：

- 写入的 memory item 都有 `source_ref`。
- 写入的 memory item 都有 embedding。
- consolidation event 能落库。

问题信号：

- 写入非常稀疏，例如 EverMem 10 个 case 只写了 6 条 memory item。
- memory 类型全部是 `event`，没有覆盖 `profile`、`preference`、`procedure`。
- `memory_replacements=0`，没有测到 supersede/update 能力。
- 结构化 memory 已经写入，但 QA 阶段 `recall_memory` 非空召回率为 0%，说明检索策略或 scope/time/filter 可能过严，也可能是写入摘要无法命中问题。

## 当前算法问题清单

### P0. `recall_memory` 非空召回率为 0%

现象：38 次 recall 调用全部返回空。  
影响：长期记忆系统没有真正参与回答，项目的核心卖点会被削弱。  
可能原因：

- 检索阈值过高。
- scope/channel/chat_id 过滤不匹配。
- consolidation 写入的摘要太少或太抽象。
- QA query 与 memory summary 语义不匹配。
- event/profile/preference 检索策略没有按任务区分。

优先改法：

- 在 eval runner 中记录每次 recall 的 query、scope、time_filter、threshold、候选数。
- 增加 `recall_memory_debug` 或 trace 字段，输出 vector lane、keyword lane、filtered count。
- 做 threshold ablation：0.45、0.35、0.25、0.15。
- 做 scope ablation：require_scope_match true/false。
- 强制 keyword fallback：当 vector 为空时，用原始问题和实体词直接查 `source_ref/summary`。

目标：

- memory QA 中 `recall_memory` 非空召回率从 0% 提升到 >= 70%。
- evidence recall@5 达到 >= 80%。

### P1. 长期记忆写入过稀疏

现象：多个 benchmark workspace 中 memory item 数远小于 case 数。  
影响：后续检索没有足够信息。  
可能原因：

- consolidation 写入门槛过高。
- benchmark ingest 没有触发完整 post-response memory worker。
- 只保存 history event，没有做 profile/preference/procedure 提取。

优先改法：

- 对 benchmark ingest 增加 memory write audit：每个 case 统计 messages、consolidation windows、写入 item 数。
- 把 gold evidence/reference 对应的关键信息作为 event 候选写入。
- 对 SocialMem 的 preference 问题单独启用 preference extraction。

目标：

- 每个需要长期记忆的 case 至少写入 1 条可检索候选。
- SocialMem preference cases 的 preference extraction recall >= 70%。

### P1. 记忆类型单一

现象：当前评测 memory2 中全部是 `event`。  
影响：个人助手最关键的 `profile/preference/procedure` 没有被评估到。  
优先改法：

- 建一个个人助手小型标注集，覆盖：
  - 用户长期偏好。
  - 用户身份/项目背景。
  - 用户要求助手长期遵守的流程。
  - 用户偏好变化。
- 给每类记忆单独统计 precision/recall。

目标：

| 记忆类型 | 指标目标 |
| --- | ---: |
| profile precision | >= 90% |
| preference precision | >= 90% |
| procedure precision | >= 95% |
| profile/preference/procedure recall | >= 70% |

### P1. update/supersede 没有被实际测到

现象：`memory_replacements=0`。  
影响：无法证明用户偏好变化时系统能更新旧记忆。  
优先改法：

- 构造 synthetic update benchmark：
  - 第 1 轮：用户说“以后回答详细一点”。
  - 第 2 轮：用户说“以后面试回答要简洁”。
  - 第 3 轮：问“我希望你怎么回答面试题？”
- 检查旧 preference 是否 superseded，新 preference 是否 active。

目标：

- supersede 成功率 >= 90%。
- 冲突偏好同时 active 的比例 < 5%。

### P2. SocialMem 表现偏弱

现象：SocialMem eval5 F1=0.2207。  
影响：个人偏好和跨会话行为模式理解能力不足。  
注意：SocialMem 是多人社交数据，不完全等价于个人助手，但可以作为 preference/profile 能力压力测试。  
优先改法：

- 先只抽取与目标用户相关的偏好和行为模式。
- 检索时区分“问用户本人”与“问他人行为”。
- 对 preference 问题提高 preference/profile memory 权重。

目标：

- SocialMem 100 cases F1 >= 0.50。
- preference 子集 F1 >= 0.55。

## 建议的算法指标体系

| 指标 | 当前值 | 近期目标 | 说明 |
| --- | ---: | ---: | --- |
| 算法单测通过率 | 75/75 | 持续 100% | tool_search、adapter、embedding、consolidation、benchmark scope |
| EverMem smoke F1 | 0.4586 | >= 0.60 | 先跑 100 cases baseline |
| SocialMem smoke F1 | 0.4605 | >= 0.55 | 重点看 preference/profile |
| SocialMem eval5 F1 | 0.2207 | >= 0.50 | 当前偏弱 |
| `recall_memory` 非空召回率 | 0% 端到端旧结果；无模型候选验证最高 50% | >= 70% | scope mismatch 已修，仍需继续优化 |
| evidence recall@5 | 未测 | >= 80% | 需要增加 gold evidence 对齐 |
| source_ref 覆盖率 | 100% | >= 95% | 当前达标 |
| embedding 覆盖率 | 100% | >= 95% | 当前达标 |
| profile/preference/procedure 写入 recall | 未测 | >= 70% | 需要小型人工标注集 |
| memory 写入 precision | 未测 | >= 90% | 防止误记 |
| supersede 成功率 | 未测 | >= 90% | 需要 synthetic update benchmark |
| tool_search precision@5 | 单测通过 | >= 90% | 需要查询集评估 |

## 下一步最小可执行计划

1. 先修 `recall_memory` 空召回问题  
   对已有 eval workspace 做 debug，不需要重新跑大模型。先看为什么 memory2 有 item 但 recall 返回空。

2. 建 50 条个人助手 memory 标注集  
   覆盖 profile、preference、procedure、event、update 五类，每类 10 条。

3. 增加 `memory_eval.py`  
   指标包括 write precision/recall、recall@k、source_ref coverage、supersede success。

4. 跑 EverMem/SocialMem 各 100 条  
   形成正式 baseline，不再只依赖 2 条或 5 条 smoke。

5. 做 ablation  
   至少比较：
   - vector only
   - keyword only
   - vector + keyword
   - vector + keyword + RRF
   - with/without source evidence fetch

## 面试中可以这样讲

> 我已经把项目从功能 demo 推进到可评估阶段。当前算法单测 73 项通过，EverMem 和 SocialMem benchmark 已接入并跑通。现阶段最关键的问题是结构化长期记忆的召回还没有真正发挥作用：已有结果中 `recall_memory` 非空召回率为 0%，虽然 source_ref 和 embedding 覆盖率都是 100%。这说明系统的存储链路是通的，但写入粒度、检索阈值和 scope 过滤需要优化。下一步我会优先做 memory retrieval debug、gold evidence recall@k 和 preference/profile 标注集，把长期记忆从“能写入”提升到“能稳定召回并提升 QA 准确率”。
