---
unit: agent-closed-loop-v1
status: proposed
depends_on: ERR-001, RUN-001, RUN-003, OUT-001, MEM-001, MEM-008, PLG-009, TST-001
---

# Agent Closed Loop v1

## 1. 目标与范围

本里程碑把被动与主动链路中的意图、规划、工具执行、恢复、记忆发布和评测连接成
可审计的单机闭环。业务工具继续通过插件/MCP 插入；核心 Runtime 统一维护状态、
权限、可靠投递、失败语义和验收证据。

## 2. 职责与非目标

本单元负责八个有界切片：TurnStatusFrame 验收、FailureClass/RecoveryPolicy、
ToolGraph v1、多路 IntentView 融合、Typed EventEnvelope/AsyncTaskState、长期记忆
发布协议、Trajectory Evaluation 和 Offline Evolution。

本里程碑不引入分布式队列、图数据库、通用多智能体编排、在线 RL/SFT、备用模型
或 Provider、静默降级、生产自动切换和无关重构。各切片只实现通过其验收所需的
最小生产路径。

## 3. 契约与依赖

- Channel、MessageBus 和 ChatLane 负责消息接入与同聊天顺序。
- Core Runtime 拥有 ContextFrame、IntentView、TaskPlan、ToolExecutor 和恢复策略。
- ToolRegistry 只发布当前 generation 已激活的核心、插件和 MCP 能力。
- Session/Outbox/Memory 各自保留现有权威状态 owner；临时路由与规划不得反向改写事实。
- 主动与被动链路共享事件、工具执行、审计和投递基础设施，但各自保留决策策略。
- 冻结 Holdout、安全集和 semantic oracle 不与候选实现一起降低门槛。

## 4. 设计不变量

1. LLM 意图结果只是候选证据，不能直接授权工具、决定重试或宣告成功。
2. 权限、schema、安全拒绝和确定性输入错误不得重试。
3. 多工具计划必须是有界 DAG；环、缺失能力和不满足的输入 fail-loud。
4. 独立只读工具可以并行；副作用、审批或依赖工具按确定顺序执行。
5. 记忆候选未通过来源、冲突、隐私和提交 Gate 前不得成为已验证长期事实。
6. 线上只追加轨迹证据；候选优化只在离线执行，不能自改 oracle 或发布门槛。
7. Telegram、CLI、Dashboard 现有协议和稳定 V3 保持可回滚。

## 5. 运行时流程

```text
┌────────────────────────────┐
│ Channel / Proactive source │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│ EventEnvelope + ChatLane   │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│ Context + TurnStatus       │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│ IntentView fusion          │  规则/词法/向量/上下文/LLM
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│ TaskPlan + ToolGraph       │
└─────────────┬──────────────┘
              ▼
┌────────────────────────────┐
│ ToolExecutor + Recovery    │
└──────┬───────────┬─────────┘
       ▼           ▼
┌────────────┐  ┌────────────┐
│ Memory Gate│  │ Outbox     │
└────────────┘  └────────────┘
       └───────────┬─────────┘
                   ▼
┌────────────────────────────┐
│ Trajectory / Offline Gate  │
└────────────────────────────┘
```

依赖顺序：M0 → M1 → M2 → M3；M4 在 M1 契约稳定后进入；M5 依赖 M1/M4；M6
消费 M1～M5 的统一状态；M7 只消费 M6 生成的离线证据。

## 6. 数据所有权与状态

- `TurnStatusFrame`、IntentView 和 TaskPlan 是 C 类请求内视图，不持久化为用户事实。
- EventEnvelope/AsyncTaskState 只在既有 turn/outbox 或专用核心状态 owner 中记录；具体
  schema 若需要变化，必须先完成单独的架构 Gate，不能在普通切片中顺带迁移。
- MEMORY/SELF/PENDING 和 memory2 继续按 MEM-001～MEM-009 管理。候选记录只追加；
  发布通过状态机逻辑推进；当前不得增加自动物理删除。
- Trajectory 是诊断/评测证据，不自动进入长期记忆，也不保存模型隐藏思考。
- 正式 workspace、凭据、插件数据和服务进程不在本地代码 Goal 的允许 write set 内。

## 7. 失败处理

M1 建立统一 `FailureClass`，把 provider、工具、上下文和控制流故障映射到唯一恢复动作：
`retry_same_path`、`clarify`、`abort` 或明确的既有补偿。重试必须共享总预算并记录最后
失败；相同错误指纹达到上限后终止。禁止备用 Provider、空结果成功、catch-and-continue
和测试桩进入生产配置。

## 8. 安全

ToolGraph 和 LLM 都不能扩大 ToolGovernor、审批、插件 generation 或 MCP readiness
授予的能力。副作用工具保留审批、幂等键、审计和 Outbox 提交边界。路由和轨迹只保存
完成验收所需的结构化证据，不复制密钥、完整工具结果或模型隐藏思考。

## 9. 可观测性

统一关联字段至少包括 session、turn、event、plan、tool call 和 delivery identity。
评测记录首个不可接受动作、轨迹前缀、最终状态、澄清决策、工具依赖、重试次数、
输入 token 和延迟。平均分不得掩盖安全集、冻结 Holdout 或连续成功率回退。

## 10. 验收标准

| 切片 | 可观察结果 | 停止条件 |
|---|---|---|
| M0（verified） | 状态帧请求内隔离且默认关闭 | 单元/集成/静态/SDD Gate 通过；公开 Docker Gate 留到根级重跑 |
| M1（verified） | 每类故障得到唯一、有界恢复动作 | 不可重试错误零重试；瞬态错误不超过预算 |
| M2（verified） | 1～5 个工具按 DAG 执行 | 依赖排序、顺序执行、环和缺失能力测试通过；并发留待独立切片 |
| M3（verified） | 上下文和 LLM 信号进入受约束 IntentView | 当前冻结 V3 Gate 通过并绑定源码；扩大 Gold/Holdout/安全集留给 M6 |
| M4（verified） | 事件和异步任务具有明确终态 | accepted/running/terminal、取消、shutdown、快照租约与静态 Gate 通过 |
| M5（verified） | 候选记忆经发布 Gate 成为长期事实 | 冲突、隐私、失败回滚、hash 确认与重启恢复 Gate 通过 |
| M6（verified） | 可定位轨迹首错并计算连续成功 | first-error、prefix、依赖、重试与 Pass^k CLI Gate 通过 |
| M7（verified） | 线上证据只产生离线候选 | digest、边界、保留、样本、安全与 Holdout Gate 通过；仅供人工审查 |

根级完成还要求被动、主动、多工具、故障、记忆和投递的隔离端到端 smoke 通过，
相关 SDD 与最终源码对账，完整 diff 无未声明 fallback、权限或持久副作用。

## 11. 源码依据

- `agent/core/passive_turn.py:DefaultReasoner.run`
- `agent/core/turn_status.py:TurnStatusFrame`
- `agent/tools/registry.py:ToolMeta`
- `agent/planning/tool_graph.py:ToolGraph,TaskPlan`
- `agent/routing/advisor_v3.py:IntentV3TurnRouteAdvisor`
- `bus/events.py:InboundMessage`
- `bus/queue.py:ChatLane`
- `agent/intent_routing/`
- `agent/tool_hooks/`
- `agent/delivery/`
- `core/memory/`、`memory2/`、`plugins/default_memory/`
- `proactive_v2/`
- `eval/intent_routing/`
- `docs/design/turn-status-frame.md`
- `docs/design/vnext-local-integration.md`

## 12. 未决问题

1. M4 若必须改变持久 schema 或公共事件协议，先提交架构级决策包，不在原切片扩大范围。
2. M5 只实现现有 MEM 条款能够支持的发布协议；持久化状态地图中尚未确认的 retention、
   secret backup 和诊断保留问题不由本 Goal 决定。
3. M3 的具体晋级阈值由当前冻结数据、稳定 V3 和人工审核 Gold 基线在实现前固定；
   候选实现不得同时修改 oracle 来获得通过。
