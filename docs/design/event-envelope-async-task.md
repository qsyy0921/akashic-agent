---
unit: core-event-envelope-async-task
status: verified
depends_on: RUN-001, RUN-003, core-failure-recovery-policy
---

# Event Envelope and Async Task State

## 1. 目标与范围

本单元为核心内部异步工作建立 EventEnvelope 和 AsyncTaskState 合同，并先接入现有后台
subagent spawn 链路。任务创建、开始、成功、失败和取消都形成唯一合法状态迁移；完成
回灌携带与任务身份绑定的 typed envelope。

## 2. 职责与非目标

本单元负责事件身份、关联/因果字段、UTC 时间、异步任务状态机、任务目录状态快照、
completion envelope 和取消终态。

本单元不替代 control TurnRecord、OutboxRecord、ToolCallRecord 或插件 EventBus，不改变
Telegram/CLI/JSON-RPC wire shape，不实现分布式 broker、通用 workflow engine、任务恢复
执行或跨机器 exactly-once。

## 3. 契约与依赖

- EventEnvelope 是不可变泛型值，包含 schema、event id/type、source、subject、UTC 时间、
  correlation/causation 和 typed payload。
- AsyncTaskState 只允许 `accepted -> running -> succeeded|failed|cancelled`；调度前失败可从
  accepted 进入 failed/cancelled。终态不能再次迁移。
- SubagentManager 继续拥有 asyncio task、snapshot lease 和会话回灌；任务目录只保存状态
  投影，不保存凭据或完整结果。
- SpawnCompletionItem 保持 `event` 兼容字段，并可携带与该 payload 同一对象的 envelope。
- MessageBus/ChatLane 继续拥有回灌顺序；envelope 不绕过 lane 或 outbound durability。

## 4. 设计不变量

1. event/task id、类型、source 和 subject 都有长度与格式边界。
2. 所有时间必须带时区并规范化为 UTC；started/finished 不能早于 accepted。
3. succeeded 不得有 error code；failed 必须有稳定 error code。
4. terminal 状态不能转移，取消与完成竞态只能有一个胜者。
5. completion envelope 的 subject id 必须等于 job id，payload 必须是 item.event。
6. `task-state.json` 使用同目录临时文件和 `os.replace` 原子发布；写失败不得假装成功。
7. 任务状态和 envelope 不包含 task 正文、结果正文、参数或凭据。

## 5. 运行时流程

```text
spawn request
    |
    v
accepted state --atomic snapshot--> task-state.json
    |
    v
running state  --asyncio task + snapshot lease
    |
    +---- success ----------> succeeded
    +---- error/incomplete -> failed
    +---- cancel -----------> cancelled
                                  |
                                  v
                    SpawnCompletionEvent
                                  |
                                  v
                      EventEnvelope payload
                                  |
                                  v
                     MessageBus / ChatLane
```

## 6. 数据所有权与状态

SubagentManager 是运行中 task/state 的唯一内存 owner。每个 `subagent-runs/<job-id>/` 下的
`task-state.json` 是 D 类任务审计投影，允许同一 task identity 按状态机原位替换；任务
产物和 memory 不由该文件拥有。进程重启后的自动重跑/补偿不在本单元内，不能把残留
running 文件解释成已成功。

## 7. 失败处理

非法状态迁移、naive datetime、缺失 failed error code、envelope/payload 身份不一致均
fail-loud。业务执行异常映射为 failed；预算耗尽/强制汇总映射为 failed/incomplete code；
显式取消映射为 cancelled。状态文件发布失败不发送成功 completion。

## 8. 安全

Envelope 与 task state 只保存类型、身份、时间、状态和稳定 reason/error code。原始任务、
结果、工具参数、模型思考和凭据不进入状态文件。EventEnvelope 不能授予工具权限，也不
改变 plugin generation lease、ToolGovernor 或 delivery owner。

## 9. 可观测性

任务状态提供 task id/kind、attempt、status、accepted/started/finished/updated 时间和
reason/error code；completion envelope 提供 event、correlation 和 causation id。既有
spawn trace 继续保存兼容业务状态，并增加 envelope header 与 canonical task status。

## 10. 验收标准

1. EventEnvelope 拒绝无效 id/type/source、naive timestamp 和空 subject。
2. AsyncTaskState 覆盖 accepted/running/succeeded/failed/cancelled 合法路径和非法迁移。
3. spawn 成功、incomplete/error、显式 cancel 和 shutdown cancel 都只产生一个终态。
4. task-state.json 原子更新且不包含任务/结果正文。
5. completion item 携带身份一致的 typed envelope，旧 handler 继续读取 item.event。
6. control turn、MessageBus、spawn completion 和插件热重载回归不下降。
7. 定向测试、Ruff、Pyright、严格 SDD 和无 fallback 审计通过。

## 11. 源码依据

- `agent/control/models.py:TurnStatus`
- `agent/control/runtime.py:ConversationRuntime`
- `session/reliability_records.py`
- `agent/background/runtime.py`
- `agent/background/subagent_manager.py`
- `bus/events.py:SpawnCompletionItem`
- `bus/internal_events.py:SpawnCompletionEvent`
- `bus/queue.py:MessageBus,ChatLane`
- `tests/test_subagent_manager.py`

## 12. 未决问题

重启后恢复或补偿 nonterminal spawn 需要任务输入的安全持久化、owner admission 和投递
幂等协议，属于独立架构切片；本单元只保证正常运行、异常和有序 shutdown 路径的终态，
并保留残留状态供后续恢复器识别。
