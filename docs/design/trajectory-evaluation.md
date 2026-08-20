---
unit: eval-trajectory-v1
status: verified
depends_on: core-turn-status-frame, core-tool-graph-v1, core-event-envelope-async-task
---

# Trajectory Evaluation v1

## 1. 目标与范围

本单元建立离线、确定性的 Agent 轨迹判分器。它把 routing、clarification、tool call/result、
reply、memory publication 和 delivery 等来源适配为统一步骤，定位首个不可接受动作，并计算
正确前缀、终态、重试、token、延迟与 Pass^k。

## 2. 职责与非目标

本单元负责规范化输入 schema、oracle、单次判分、跨 attempt 聚合、严格 JSONL runner 和报告
source digest。不负责在线 trace 采集、模型裁判、数据标注 UI、分布式 telemetry、自动修改
prompt/权重或替代现有 intent/memory benchmark。

## 3. 契约与依赖

- 每个 run 有 case id、正整数 attempt、terminal status、steps 和 oracle。
- step 只有 id、kind、subject、outcome、attempt、latency_ms、input_tokens；不保存参数或结果正文。
- oracle 定义有序 matcher、是否允许尾随步骤、终态、澄清策略、工具依赖和重试预算。
- matcher 可接受一个 kind 下多个等价 subject/outcome，避免把唯一实现路径当成正确答案。
- runner 逐行严格解析 JSONL，任一损坏输入 fail-loud，不跳过坏行。

## 4. 设计不变量

1. first error 永远是按执行顺序遇到的第一个违规；终局失败不能覆盖更早原因。
2. 只有 succeeded tool result 才满足依赖；tool call 本身不算完成。
3. Pass^k 表示同一 case 前 k 次 attempt 全部成功，不等同于 pass@k。
4. 少于 k 次 attempt 的 case 不进入 Pass^k 分母，并显式报告 eligible_cases。
5. report 绑定输入 bytes 的 SHA-256，不能修改数据后沿用旧指标。
6. 评测记录和报告不含 tool arguments、model thinking、message 正文或凭据。

## 5. 运行时流程

```text
runtime/eval trace adapters
          |
          v
strict normalized JSONL --sha256--> TrajectoryRun
          |                              |
          v                              v
 ordered oracle match         dependency/retry/clarification policy
          |                              |
          +--------------+---------------+
                         v
             first error + correct prefix
                         |
                         v
      attempt success / p95 / error histogram / Pass^k
```

## 6. 数据所有权与状态

冻结 JSONL 是评测输入 owner，生成 JSON report 是可再生 D 类产物。报告只保存 case/attempt、
步骤类别和 subject 标识、计数、耗时、token 与错误 reason；不复制生产 trace payload。长期
报告继续写入项目的机械盘 Junction，不在源码目录堆积大文件。

## 7. 失败处理

schema、枚举、负数 token/latency、重复 case-attempt、非法 matcher 或 dependency 均拒绝整次
运行。缺步骤、意外步骤、依赖违规、重试超限、澄清违规和终态不符产生稳定 first-error
reason。聚合不以空分母伪造 0 或 100%，而返回 null rate 与明确 eligible count。

## 8. 安全

schema 不提供 argument/result/content/thinking 字段；未知字段拒绝。runner 不加载代码、不调用
模型或工具、不访问生产数据库。subject 是受限标识，不得含换行或超过长度边界。

## 9. 可观测性

单次结果提供 first_error_index/reason、expected/actual、correct_prefix_length/rate、terminal、
clarification、retry、input_tokens 和 latency。聚合提供 attempt success、case all-pass、Pass^k、
首错直方图、token 总量和 nearest-rank P95 latency。

## 10. 验收标准

1. 成功、意外步骤、缺步骤、终态错误都能稳定定位首错和正确前缀。
2. 工具依赖必须由更早的 succeeded result 满足，失败 result 不得解锁下游。
3. required/forbidden clarification 与 retry budget 有独立 reason。
4. Pass^1/2/3、eligible count、全 attempt 成功率和首错直方图可复现。
5. JSONL schema 未知字段、重复 attempt 和坏类型 fail-loud。
6. 单元测试、CLI smoke、Ruff、Pyright 和严格 SDD 通过。

## 11. 源码依据

- `eval/intent_routing/run_v3_eval.py`
- `eval/longmemeval/qa_runner.py`
- `eval/personamem/qa_runner.py`
- `core/common/strategy_trace.py`
- `agent/planning/tool_graph.py`
- `agent/background/state.py`
- `agent/core/turn_status.py`

## 12. 未决问题

生产 trace 到 normalized JSONL 的每类 adapter 需随对应 owner 单独落地；v1 先固定评分语义，
避免采集实现反向定义 oracle。开放式文本质量仍由现有 benchmark 或人工 rubric 评估。
