# Core Turn Status Frame

- 状态：candidate implementation
- 日期：2026-08-19
- 适用分支：`qsyy0921`
- 默认开关：`agent.turn_status_enabled = false`

## 1. 决策

在被动工具循环的每次模型调用前，由核心 Runtime 生成一份短小、确定性的
`TurnStatusFrame`。状态帧只描述代码已经观察到的事实：迭代预算、可见工具、
累计调用次数、最近工具状态、已解锁工具和强制/禁用约束。

状态帧不由 LLM 总结，不接受插件改写，不保存到 session history，也不包含工具
参数或结果正文。它作为本次 provider 请求末尾的动态 context frame 注入；下一轮
重新生成，旧状态不会累积。

## 2. 当前问题

当前 `DefaultReasoner.run` 在工具调用之间主要依赖完整消息轨迹维持状态。随着工具
结果增多，模型需要自行从长轨迹重建“已经做了什么、还剩多少预算、最近一次是否
成功”。`BeforeStep` 插件提示还会直接追加到共享 `messages`，使逐步提示可能跨轮
累积。现有 Loop Guard 能在重复调用后截断，但不能在每次决策前提供紧凑的执行状态。

源码证据：

- `agent/core/passive_turn.py:DefaultReasoner.run`：拥有迭代、工具可见性、调用轨迹和
  provider 调用，是状态帧的唯一事实 owner。
- `agent/lifecycle/phases/before_step.py:_InjectHintsModule`：插件提示写入传入的消息列表。
- `agent/tool_hooks/types.py:ToolExecutionResult`：工具终态为 `success/denied/error`；
  Runtime 另有 `blocked` 状态。

## 3. 目标链路

```mermaid
sequenceDiagram
    participant Loop as DefaultReasoner
    participant Phase as BeforeStep
    participant Status as TurnStatusFrame
    participant LLM as Provider
    participant Tool as ToolExecutor

    Loop->>Phase: 传入 request-local messages 副本
    Phase-->>Loop: 插件 hints / early-stop
    Loop->>Status: 从迭代预算与已完成 tool_chain 构建
    Status-->>Loop: 单个 agent_status context frame
    Loop->>LLM: messages + 当前状态帧
    LLM-->>Loop: reply 或 tool_calls
    Loop->>Tool: 执行候选调用
    Tool-->>Loop: 真实 success/denied/error
    Loop->>Loop: 只把真实结果写入 canonical messages/tool_chain
```

## 4. 所有权和边界

| 项目 | Owner | 是否持久化 | 可否由插件改写 |
|---|---|---:|---:|
| session 对话与 tool result | Session/DefaultReasoner | 是 | 通过既有生命周期 |
| `TurnStatusFrame` | Core Runtime | 否 | 否 |
| `BeforeStep.extra_hints` | Lifecycle plugin | 否，本次请求有效 | 是 |
| 工具成功/拒绝/错误 | ToolExecutor | 随 tool chain 持久化 | 否 |

状态帧不是新的业务事实源。任何“完成”判断仍以真实工具结果和最终验收为准；状态帧
只是这些事实的有损、可重建投影。

## 5. 数据契约

`TurnStatusFrame` 包含：

- 当前迭代、最大迭代和剩余迭代；`max_iterations=0` 明确表示无限制。
- 当前可见工具名；全量模式使用 `None`，不复制整个注册表。
- 工具调用总数和按工具聚合的计数。
- 最近一次工具名与状态，不复制参数、结果、hook trace 或模型思考。
- 本轮已解锁工具、首轮强制工具和禁用工具数量。

渲染必须确定性排序；工具名列表最多展示 12 项，避免工具数量增长时提示无限膨胀。

## 6. 发布策略

1. 配置默认关闭，保持当前线上 prompt 行为。
2. 单元测试验证投影准确、长度有界、敏感参数/结果不泄露。
3. 集成测试验证每个 provider 请求恰好一个状态帧，且 canonical history 不含状态帧。
4. 在冻结的多工具与澄清数据集上比较 base/candidate：任务成功率、首错类型、
   Pass^k、工具循环率、输入 token 和 P95 延迟。
5. 只有成功率不下降、循环率改善且成本增量可接受时，才在生产配置中开启。

## 7. 非目标

- 本单元不修改意图路由、多目标依赖图或 ToolRegistry schema。
- 不把主动 Wake/Drift 状态塞进被动状态帧。
- 不引入图数据库、多 Agent、SFT/RL 或新的 fallback。
- 不自动修改生产配置，不重启当前服务。

## 8. 验收标准

1. 开关关闭时 provider 输入与当前行为一致。
2. 开关开启时每次主工具循环调用只有一个 `agent_status` frame。
3. 第二轮状态准确反映第一轮真实工具状态和调用次数。
4. 初始 `messages`/session history 不持久化 `agent_status` 或逐轮插件 hint。
5. 状态渲染不含工具参数、结果正文、密钥或模型思考，且长度固定有界。
6. 现有被动链路测试和新增测试全部通过。

## 9. 本单元 write set

- `docs/design/turn-status-frame.md`
- `agent/core/turn_status.py`
- `agent/core/passive_turn.py`
- `agent/looping/ports.py`
- `agent/config.py`
- `agent/config_models.py`
- `bootstrap/tools.py`
- `config.example.toml`
- `tests/test_turn_status.py`
- `tests/test_agent_core_p2_reasoner.py`
- `tests/test_runtime_smoke.py`

## 10. 全书结论到 Akashic 的后续路线

以下排序基于《深入理解 AI Agent：设计原理与工程实践》各章原则与当前源码的
交叉检查，不表示书中所有机制都应照搬。

| 主题 | 当前实现 | 主要缺口 | 决策 |
|---|---|---|---|
| 上下文与显式状态 | 已有 PromptAssembler、context frame、历史裁剪和 Loop Guard | 工具循环缺少代码维护的短状态投影 | P0，本单元实现 |
| 记忆生命周期 | 已有 `PENDING.md`、快照回滚、`source_ref` 幂等、memory2 supersede/evidence | 候选记忆的来源/冲突/隐私 Gate 尚未统一成一个可审计发布协议 | P1，保持核心 ownership |
| 工具发现 | 已有 `ToolMeta`、关键词/向量路由、`tool_search` 动态解锁 | 多目标任务缺少显式 consumes/produces/依赖边，执行中能力缺口难以规划 | P1，先做内存 ToolGraph，不上图数据库 |
| 事件与主动链路 | 已有 Channel、MessageBus、ChatLane、Wake/Drift、outbox 与主动状态库 | `InboundMessage.metadata` 仍承载开放信息；缺少统一事件种类、优先级、安全取消点和异步任务终态 | P1，增加 typed EventEnvelope/TaskState |
| 故障恢复 | 已有 HTTP retry budget、工具错误回传、重复调用 Guard、最大迭代 | API/工具/上下文/控制流错误尚未统一分类；长流空闲 watchdog 和分路径熔断不完整 | P1，建立 FailureClass/RecoveryPolicy |
| 评测 | 意图路由已有冻结 Dev/Holdout/Stress、NO-GO Gate 和大量回归 | 通用工具链仍缺 first-error、trajectory-prefix、Pass^k 与状态准确率指标 | P1，扩展统一 trajectory evaluator |
| 持续演进 | 已有记忆 optimizer、版本化设计文档和人工 Gate | 线上轨迹到候选 Harness 的离线提案/边界集/保留集/安全集尚未产品化 | P2，只允许离线 candidate |
| 多模态/Computer Use | Telegram 媒体、视觉工具和 Chrome-CDP/MCP 可提供能力 | 浏览器身份、会话复用、可审计动作与隔离执行仍依赖外部服务配置 | P2，先强化 adapter 和 policy |
| 多 Agent | 已有 spawn/background 子任务 | 尚无证据表明扩大 Agent 数量会提升当前意图/工具成功率 | 暂缓，仅在能引入新信息时使用 |
| SFT/RL | 当前问题已有 Harness 和数据层修复空间 | 高保真、可重置训练环境与可靠过程奖励尚不具备 | 暂缓，先完成外部符号化与评测闭环 |

建议按以下有界单元继续，每个单元独立 feature flag、独立 Gate、可单独回滚：

1. `FailureClass + RecoveryPolicy`：只统一错误分类和恢复上限，不新增备用模型 fallback。
2. `ToolGraph v1`：给 ToolMeta 增加 `consumes/produces/requires_operations`，用内存 DAG
   规划多工具顺序并检测缺失依赖。
3. `EventEnvelope + AsyncTaskState`：区分 user/tool/timer/webhook/supervisor 事件，状态
   使用 accepted/running/succeeded/failed/cancelled，不把已接收误报为已完成。
4. `Trajectory Evaluation v1`：保存首个不可接受动作、轨迹前缀和连续成功指标；保持
   当前 NO-GO veto，不用平均分掩盖安全或正确性回退。
5. `Offline Evolution v1`：线上只追加证据，离线生成候选补丁；边界集、保留集、安全集
   全部通过后才允许灰度，验证器和发布门槛属于不可自修改的可信根。

明确不做：把 Runtime/Session/Memory/ProactivePolicy 变成普通插件；把业务真相写进
向量库；让 LLM 直接决定权限、成功状态或重试次数；在没有可靠环境前进行大规模 RL；
为当前单机规模引入分布式队列或图数据库。
