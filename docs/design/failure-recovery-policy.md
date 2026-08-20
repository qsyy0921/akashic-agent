---
unit: core-failure-recovery-policy
status: verified
depends_on: ERR-001, RUN-002, CTRL-001, core-turn-status-frame
---

# Core Failure Recovery Policy

## 1. 目标与范围

本单元为 provider、工具、上下文和控制流边界提供一个核心拥有的故障分类与恢复
决策。调用者得到稳定的 `FailureRecord` 和 `RecoveryDecision`，不再分别用字符串、
异常类型和布尔值猜测是否重试。

## 2. 职责与非目标

本单元负责故障领域、类别、稳定 code、无正文指纹、恢复动作和预算耗尽判断，并把
结果接入 ControlExecutionError 与 ToolExecutionResult。

本单元不增加备用 Provider，不实现跨工具补偿，不改变 context trim 顺序，不新增
持久表，不捕获未知内部异常后继续，也不让分类器决定工具权限或任务成功。

## 3. 契约与依赖

- 模型运行时异常以 `agent.model_runtime.errors` 为权威类型来源。
- Chat Completions 兼容层继续公开 `ContentSafetyError`、`ContextLengthError` 和
  `LLMNetworkTimeoutError`。
- ToolExecutor 继续拥有 pre-hook、授权、invoker、可靠性账本和 post-hook 顺序。
- ControlExecutionError 保留现有 `error_type` 和 `retryable` 兼容字段，并增加结构化
  failure/recovery 投影。
- `RecoveryPolicy` 只给出决策；实际重试只能由拥有对应操作和预算的调用边界执行。

## 4. 设计不变量

1. 权限、安全、认证、额度、无效输入、协议和内部错误不得得到同路径重试决策。
2. 限流、网络超时、连接失败和服务端瞬态故障最多在显式预算内同路径重试。
3. 上下文超长只允许既有 context planner 缩减请求后重试，不允许备用模型或静默截断。
4. 同一故障指纹达到上限后必须转为 `abort`。
5. 故障指纹只使用领域、类别、code 和异常类型，不包含消息正文、参数或凭据。
6. 未知异常分类为 internal，并保持 fail-loud；分类不能把异常变成成功结果。

## 5. 运行时流程

```text
┌──────────────────┐
│ Exception/denial │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ FailureClassifier│  domain + class + code + fingerprint
└────────┬─────────┘
         ▼
┌──────────────────┐
│ RecoveryPolicy   │  attempt budget + same-fingerprint budget
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Owning boundary  │  retry / reduce_context / clarify / abort
└──────────────────┘
```

Control 边界把已知 provider 异常转换为结构化 ControlExecutionError。ToolExecutor 在
denied/error 结果上附加 FailureRecord；调用者仍通过原有 status 判断成功、拒绝和错误。

## 6. 数据所有权与状态

FailureRecord 和 RecoveryDecision 是不可变的 C 类运行时值，不拥有数据库、文件或
外部状态。当前只随既有错误对象和工具轨迹向上返回，不新增持久写入、逻辑失效或
物理删除。后续轨迹评测可以读取脱敏字段，但不能自动把它们写入长期记忆。

## 7. 失败处理

分类器本身只执行确定性类型映射。调用参数非法时立即抛出 ValueError；未知异常返回
internal/abort 供边界记录后继续抛出，而不是吞掉。预算耗尽返回带 `exhausted=true` 的
abort 决策。恢复动作没有可执行 owner 时，调用边界必须终止。

## 8. 安全

`permission_denied`、`content_safety`、`authentication` 和 `quota` 永不重试。分类器
不接收或保存工具最终参数、Provider 凭据、响应正文和模型思考。ToolGovernor 的
allow/deny 结果优先于任何恢复建议，RecoveryPolicy 不能绕过审批。

## 9. 可观测性

结构化证据包含 `domain`、`failure_class`、`code`、`fingerprint`、`action`、
`attempts_used`、`max_attempts` 和 `exhausted`。日志可以记录这些字段，但不得记录
异常正文作为指纹。M6 将复用该结构定位轨迹首错。

## 10. 验收标准

1. 每个受支持 provider 异常得到稳定分类与唯一默认恢复动作。
2. PermissionError、ValueError、认证、额度和安全拒绝的 `retryable` 均为 false。
3. timeout/rate-limit/connection 在预算内为 retry，达到次数或同指纹上限后为 abort。
4. ControlExecutionError 保持旧字段兼容，并暴露同一结构化决策。
5. ToolExecutionResult 的 denied/error 带 failure，success 不带 failure。
6. 未知内部错误不得被自动重试或转换为成功。
7. 定向测试、相关回归、Ruff、Pyright 和严格 SDD 校验通过。

## 11. 源码依据

- `agent/model_runtime/errors.py`
- `agent/provider.py`
- `bootstrap/control_execution.py:execute_control_turn`
- `agent/control/errors.py:ControlExecutionError`
- `agent/tool_hooks/executor.py:ToolExecutor`
- `agent/tool_hooks/types.py:ToolExecutionResult`
- `core/net/http.py:RetryPolicy`
- `docs/projectneed.md:ERR-001`

实现证据：

- `agent/reliability/failures.py` 提供不可变 FailureRecord、RecoveryDecision、分类器和
  有界 RecoveryPolicy；Provider/OpenAI 适配只在分类时惰性导入。
- `bootstrap/control_execution.py` 只捕获已有 provider 边界异常，未知 Runtime 异常
  继续上抛；ControlExecutionError 保留原有兼容字段。
- `agent/tool_hooks/executor.py` 为所有 denied/error 结果附加 failure/recovery；AST
  审计确认生产代码没有遗漏构造点。
- 30 项首轮定向测试、66 项扩展 reasoner/subagent/governance/control 回归和最后
  17 项改后复核均通过；作用域 Ruff 通过；核心新增模块 Pyright 为 0 errors/0 warnings。
- `git diff --check` 通过，仅有既有 Windows 换行提示；完整写集没有新增 Provider、
  store、queue、catch-and-continue 或成功占位路径。

## 12. 未决问题

流式响应 idle watchdog 需要 Provider transport 提供独立时间戳和取消 owner，不在本
切片中顺带实现。它保留为根 Goal M1 后续的独立候选，只有当前分类合同不足以表达
流式超时时才进入写集。
