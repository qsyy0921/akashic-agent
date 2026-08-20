---
unit: core-turn-status-frame
status: verified
depends_on: CTX-001, CTX-003, CTX-004, ERR-001
---

# Core Turn Status Frame

## 1. 目标与范围

本单元在被动工具循环的每次模型调用前，由核心 Runtime 生成短小、确定性的
`TurnStatusFrame`。状态帧只投影代码已经观察到的执行事实，默认通过
`agent.turn_status_enabled = false` 关闭，正式启用需要独立的 base/candidate 语义 Gate。

## 2. 职责与非目标

本单元负责迭代预算、可见工具、累计调用次数、最近工具状态、已解锁工具和
强制/禁用约束的请求内投影。它不修改意图路由、ToolRegistry schema、主动链路、
工具授权、任务完成判断、生产配置或正式服务进程，也不引入备用 Provider。

## 3. 契约与依赖

- `DefaultReasoner.run` 是迭代、工具可见性、调用轨迹和 provider 请求的事实 owner。
- `BeforeStep` 可以生成本次请求的插件 hint，但不能改写核心状态帧。
- `ToolExecutionResult` 和 Runtime 的 `blocked` 记录提供工具终态。
- 本单元遵守 CTX-001、CTX-003、CTX-004：请求内投影不得写回持久历史，也不得伪装成用户原话。

## 4. 设计不变量

1. 每次 provider 请求最多包含一个 `agent_status` frame。
2. 状态帧不进入 canonical messages、SessionDB 或下一次循环的基础消息。
3. 状态帧不包含工具参数、工具结果正文、密钥、模型思考或插件私有状态。
4. 工具名确定性排序并最多展示 12 项，渲染长度不会随注册表无限增长。
5. 状态帧不能授权工具、改变工具终态或把未完成任务标记为成功。
6. 开关关闭时保持原有 provider 输入路径。

## 5. 运行时流程

```text
┌──────────────────────┐
│ DefaultReasoner      │
└──────────┬───────────┘
           │ 创建 request-local messages 副本
           ▼
┌──────────────────────┐
│ BeforeStep           │  插件 hint 只修改本次副本
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ TurnStatusFrame      │  从迭代与 tool_chain 重建
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Provider request     │  messages + 一个 agent_status
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Tool execution       │  只把真实结果写入 canonical 轨迹
└──────────────────────┘
```

## 6. 数据所有权与状态

`TurnStatusFrame` 是 C 类临时运行时视图，不拥有持久状态。Session/DefaultReasoner
继续拥有对话和工具轨迹；ToolExecutor 继续拥有工具执行结果。每轮状态帧都从当前
迭代和真实 `tool_chain` 重建，旧帧不会原位更新、逻辑失效或物理删除，因为它从未
被持久化。

## 7. 失败处理

状态帧构造只消费内存中的已验证类型，不执行 I/O、重试或外部调用。内部契约违反
时由调用边界 fail-loud；本单元不捕获异常后继续，也不生成空状态假装成功。关闭
feature flag 是回滚路径，不是运行时 fallback。

## 8. 安全

状态帧只输出工具名、计数和枚举状态。构造器不得接收工具参数、结果正文、凭据或
任意插件 payload。工具审批与授权仍由既有 ToolGovernor/执行边界决定，模型无法
通过状态帧扩大权限。

## 9. 可观测性

`react_stats.turn_status_enabled` 记录本回合是否启用状态帧。现有 LLM 调用日志继续
记录迭代、可见工具数量和估算 token；状态帧正文不进入独立持久 trace，避免复制
潜在会话信息。base/candidate Gate 负责比较成功率、循环率、输入 token 与 P95 延迟。

## 10. 验收标准

1. 开关关闭时 provider 输入不包含 `agent_status`。
2. 开关开启时每次主工具循环调用恰好包含一个状态帧。
3. 第二轮状态准确反映第一轮真实工具状态和调用次数。
4. 初始 messages 和持久 session history 不包含状态帧或逐轮插件 hint。
5. 状态渲染不包含敏感参数和结果正文，且长度固定有界。
6. 单元、reasoner 和 runtime 配置回归全部通过。

验证命令：

```text
python -m pytest tests/test_turn_status.py tests/test_agent_core_p2_reasoner.py tests/test_runtime_smoke.py -q
ruff check agent/core/turn_status.py agent/core/passive_turn.py agent/config.py agent/config_models.py agent/looping/ports.py tests/test_turn_status.py tests/test_agent_core_p2_reasoner.py tests/test_runtime_smoke.py
npx --yes pyright agent/core/turn_status.py
python validate_unit_sdd.py docs/design/turn-status-frame.md --strict
```

## 11. 源码依据

- `agent/core/turn_status.py:TurnStatusFrame`
- `agent/core/passive_turn.py:DefaultReasoner.run`
- `agent/lifecycle/phases/before_step.py:_InjectHintsModule`
- `agent/tool_hooks/types.py:ToolExecutionResult`
- `agent/config.py:load_config`
- `agent/config_models.py:Config`
- `tests/test_turn_status.py`
- `tests/test_agent_core_p2_reasoner.py`
- `tests/test_runtime_smoke.py`

M0 隔离验收结果：65 项目标测试通过；作用域内 Ruff 通过；新模块 Pyright 为
0 errors/0 warnings。全文件 `bootstrap/tools.py` 的既有 E402/F841 基线错误另行记录，
本单元只增加配置字段传播，不改该文件的导入布局或既有未使用变量。

整仓回归首次运行得到 2365 passed、187 skipped 和 3 个环境失败；两个编码样例在
`PYTHONUTF8=1` 下保持原断言通过，fresh configuration matrix 在新临时目录重跑
15/15 通过。公开 Change Gate 尚未形成语义结果：Docker 镜像构建超过 600 秒，
报告 `20260819-205211-f4153917` 明确记录 image-build failed，未执行场景且未把它
计为通过。该 Gate 留到根级验收重跑，feature flag 继续保持关闭。

## 12. 未决问题

生产开关是否启用由后续冻结多工具 base/candidate Gate 决定。在该 Gate 通过前，
默认值保持 `false`。
