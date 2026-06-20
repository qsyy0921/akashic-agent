# 05. 评估与 Benchmark

## 为什么需要评估

个人助手的关键能力是长期记忆，但 memory 很容易出现三类问题：

- 漏记：用户明确表达了长期偏好，但系统没有保存。
- 误记：把临时信息、提问、助手建议错误写成用户偏好。
- 召回失败：记忆已经存在，但回答时没有检索出来或没有正确使用。

所以不能只靠 demo，需要用 benchmark 量化：

- 是否能跨 session 回答用户偏好问题。
- 是否能追溯过去事件。
- 是否能处理偏好更新。
- 是否能基于证据回答，而不是靠模型猜测。

## 当前评估框架

项目中已有 LongMemEval 风格 runner：

- 导入数据集。
- 为每个 case 创建隔离 workspace。
- 写入历史会话。
- 触发 memory consolidation。
- 对问题运行 Agent QA。
- 记录 predicted_answer、gold_answer、tool_chain、F1、EM、错误数。

相关代码：

- `eval/longmemeval/run.py`
- `eval/longmemeval/ingest.py`
- `eval/longmemeval/qa_runner.py`
- `eval/agent_memory_bench/export_local.py`
- `eval/agent_memory_bench/dataset.py`

## 已接入数据集

当前本地 `eval/datasets` 下有三个方向：

1. EverMemBench-Dynamic  
   多日期、多项目、多角色的长期协作对话，适合测长期事件回忆、跨时间追踪和多跳问题。

2. SocialMemBench  
   多人、多 session 社交对话，适合测个人偏好、人物行为模式、跨会话关系理解。

3. GroupMemBench probe  
   当前公开数据更适合作为 probe，而不是完整官方 QA。对个人助手主线来说，它不是重点，只作为检索和结构化上下文压力测试。

个人助手面试时优先讲 SocialMemBench 和 EverMemBench。GroupMemBench 可以说是后续群聊 memory 扩展方向。

## 当前本地结果

当前仓库里已有 smoke 评测结果：

| 数据集 | case 数 | F1 | EM | errors | 说明 |
| --- | ---: | ---: | ---: | ---: | --- |
| EverMemBench-Dynamic smoke | 10 | 0.4586 | 0.0 | 0 | 跑通长期记忆 QA 链路 |
| SocialMemBench smoke | 2 | 0.4605 | 0.0 | 0 | 跑通偏好/人物行为问题 |
| GroupMemBench probe smoke | 2 | 0.5035 | 0.0 | 0 | 仅 probe，不代表官方分数 |

结果文件：

- `eval/longmemeval/results/agent_memory_evermem_full_mimo_shard000.json`
- `eval/longmemeval/results/agent_memory_socialmem_full_mimo_smoke.json`
- `eval/longmemeval/results/agent_memory_groupmem_probe_mimo_smoke.json`

## 如何解释当前分数

不要把当前分数包装成很高。更好的面试表达是：

> 我已经建立了可重复评估链路，并用 Mimo 模型跑了 smoke test。当前 F1 在 0.45 到 0.50 左右，说明链路能跑通，但还有明显优化空间。这个结果也帮我定位问题：不是简单模型回答错误，而是 memory 写入、召回和证据使用都需要进一步加强。

## 可以拆解的问题来源

1. 模型问题  
   小模型或兼容接口模型可能在工具调用、引用格式、复杂推理上不稳定。

2. 写入问题  
   consolidation 没有把关键信息写入 memory2，导致后续检索不到。

3. 检索问题  
   memory 存在，但 query rewrite、embedding、关键词或阈值导致没召回。

4. 使用问题  
   召回到了证据，但模型没有正确整合，或者忽略 tool result。

5. 评估框架问题  
   当前 runner 是一问一 workspace，成本高；更适合 smoke，不适合快速全量迭代。

## 下一步优化实验

1. Memory write gate ablation  
   比较“直接写入”和“候选生成 + 二次判定”对误写/漏写的影响。

2. Retriever ablation  
   分别关闭向量、关键词、RRF、HyDE，观察 F1 和 recall 变化。

3. Source evidence mode  
   强制 recall 后再 fetch_messages，比较有无原文证据时的准确率。

4. Preference update test  
   构造用户偏好改变的数据，测试 supersede 是否生效。

5. Grouped runner  
   把同一用户/同一网络的多个 QA 放到共享 memory workspace 中，降低评估成本，更贴近真实个人助手。

## 面试可说的评估闭环

> 我不是只做功能 demo，而是把个人助手的长期记忆能力接入了 benchmark。数据集导出为 LongMemEval 格式后，可以自动 ingest 历史会话、运行 Agent QA、记录工具链和 F1/EM。通过结果我能区分是模型能力问题、记忆写入问题、检索问题还是回答整合问题，这样后续优化有明确方向。

