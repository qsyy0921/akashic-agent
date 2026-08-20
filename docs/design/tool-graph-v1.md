---
unit: core-tool-graph-v1
status: verified
depends_on: PLG-003, PLG-008, core-failure-recovery-policy, intent-route-advice-v3
---

# Tool Graph v1

## 1. 目标与范围

本单元把当前 generation 的工具元数据构造成请求内 ToolGraph，并把 resolved
RouteAdvice 的多目标依赖转换为最多五步的 TaskPlan。Reasoner 使用计划提示和依赖
检查引导现有多轮工具循环，只有前置 operation 已真实成功时才执行依赖工具。

## 2. 职责与非目标

本单元负责 `consumes`、`produces`、`requires_operations` 元数据、内存 DAG、确定性
拓扑排序、能力缺口/歧义/环检测、计划渲染和执行前依赖检查。

本单元不选择意图、不替代 ToolGovernor、不自动补参数、不持久化图、不引入图数据库、
不并发执行副作用工具、不为缺失 capability 选择任意 provider，也不把 blocked 误记为
工具成功。

## 3. 契约与依赖

- IntentView/RouteAdvice 继续拥有目标、`depends_on` 和候选排序。
- ToolRegistry/当前插件 generation 拥有工具、operation、风险和依赖元数据。
- ToolGraph 只消费同一个 runtime snapshot 的 Registry；计划携带 graph digest。
- ToolExecutor/ToolGovernor 继续拥有最终参数、授权、审批、执行和可靠性账本。
- DefaultReasoner 继续拥有实际多轮循环，TaskPlan 只约束计划内工具的前置 operation。

## 4. 设计不变量

1. TaskPlan 最多包含 5 个唯一工具和 4 个路由目标。
2. 每个计划工具必须存在于当前 Registry，且不得位于本回合 disabled 集合。
3. 一个缺失 operation 有且只有一个可用 provider 时才可自动纳入计划；零个或多个
   provider 均 fail-closed 并返回结构化缺口。
4. 所有依赖边必须形成 DAG；自依赖和环在 provider 调用前拒绝。
5. 计划顺序确定性稳定，不能依赖 set/dict 的偶然遍历结果。
6. 只有 `ToolExecutionResult.status == success` 才满足该工具提供的 operation。
7. 非计划工具仍受可见性、turn search、ToolGovernor 和自身显式依赖约束。

## 5. 运行时流程

```text
┌─────────────────────┐
│ resolved RouteAdvice│
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ top candidate / goal│
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ ToolGraph expansion │  requires_operations 唯一解析
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ cycle / gap / limit │  非 ready 时不生成可执行计划
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ deterministic plan  │  注入请求内提示并预加载 schema
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ reasoner tool loop  │  前置 operation 成功后执行依赖工具
└─────────────────────┘
```

## 6. 数据所有权与状态

ToolGraph、TaskPlan、PlanStep 和完成 operation 集是 C 类请求内视图。它们从当前
Registry 和本回合真实 tool result 重建，不写 SessionDB、plugin-data 或 memory。
`consumes/produces/requires_operations` 属于插件/核心工具声明，随 generation 原子发布；
旧 generation 的在途 turn 继续使用其绑定 Registry。

## 7. 失败处理

计划状态只允许 `ready/no_plan/missing_capability/ambiguous_capability/cycle/too_large`。
非 ready 计划保存 reason code，不静默退回任意工具。执行时依赖未满足返回明确 blocked
结果给模型，并保留后续调用正确前置工具的机会；同一 blocked 循环仍由现有 Loop Guard
有界终止。内部 Registry/plan 不一致 fail-loud。

## 8. 安全

TaskPlan 不能新增当前 Registry 之外的工具，不能绕过 disabled、turn search、插件
generation、ToolGovernor 或审批。计划提示不包含参数、工具结果、凭据或模型思考。
外部副作用工具保持顺序执行，独立只读工具的并发优化留给后续独立切片。

## 9. 可观测性

`context_retry.task_plan` 记录 status、graph digest、步骤、依赖和 reason code；tool_chain
继续记录真实 status。计划命中率、dependency_blocked、能力缺口、环和实际完成顺序供
M6 Trajectory Evaluation 消费。

## 10. 验收标准

1. Registry 对新增元数据做边界校验，默认值保持所有既有工具行为。
2. 独立两工具计划顺序稳定，依赖计划按拓扑顺序输出。
3. 唯一 provider 的隐式前置 operation 自动加入，零/多 provider fail-closed。
4. 自依赖、环、disabled 工具和超过五步均产生非 ready 计划。
5. Reasoner 只在前置工具 success 后执行依赖工具；error/denied/blocked 不满足依赖。
6. 计划只存在于请求内，canonical messages/session history 不包含计划对象。
7. 原有单工具、tool_search、多工具、治理和路由回归不下降。
8. 定向测试、Ruff、Pyright、严格 SDD 校验通过。

## 11. 源码依据

- `agent/tools/registry.py:ToolMeta,ToolRegistry`
- `agent/routing/contracts.py:IntentGoal,GoalSpec,RouteAdvice`
- `agent/routing/advisor.py:validate_route_advice`
- `agent/core/passive_turn.py:DefaultReasoner.run_turn,DefaultReasoner.run`
- `agent/tool_hooks/executor.py:ToolExecutor`
- `agent/plugins/snapshot.py`
- `tests/test_intent_routing_v3_integration.py`
- `tests/test_agent_core_p2_reasoner.py`

实现证据：

- `agent/planning/tool_graph.py` 从当前 ToolRegistry generation 派生带 digest 的不可变图，
  对显式目标依赖和 `requires_operations` 做确定性拓扑规划。
- `agent/core/passive_turn.py` 仅把 ready 计划注入 provider 的请求内消息；canonical
  messages 不保存计划。缺能力、歧义、环、禁用和超限在 provider 调用前 fail-closed。
- `agent/plugins/decorators.py` 与 `agent/plugins/manager.py` 把规划元数据带入插件 generation；
  Registry 统一校验核心与插件声明。
- 291 项 ToolGraph、reasoner、插件 generation/hot reload、路由、搜索和治理回归通过；
  依赖工具的 error、denied 与 blocked 均不能解锁后继步骤。
- M2 作用域 Ruff 通过；新 ToolGraph 模块 Pyright 为 0 errors/0 warnings；严格 SDD
  校验通过。`tests/test_plugin_manager.py` 全文件 Ruff 仍有本切片前已存在的 15 项基线
  告警，本次变更行未新增告警。

## 12. 未决问题

独立只读步骤的并发执行需要单独确定事件顺序、取消、结果回填和部分失败合同。本单元
保持现有确定性顺序执行，避免在依赖图切片中同时改变并发语义。
