---
unit: provider-contract-forward-port
status: verified
depends_on: plugin-runtime-api-v2-integration, agent-closed-loop-v1
---

# Provider Contract Forward Port

## Scope

本单元把当前 Provider Gate 已证明缺失的两组上游公共合同前移到 qsyy 运行时：完整
ChannelMessage 投递合同，以及 MCP 候选只读工具和 managed-service 验证端口声明。

## Responsibilities and non-goals

本单元负责结构化渠道附件、DeliveryReceipt、单 adapter 注册、候选声明规范化和现有
渠道适配。它不改变可靠 outbox 的“先持久化再发送”所有权，不实现最新上游的全部
移动端或插件 rollout 功能，也不增加 v1/v2 双协议。

## Contracts and dependencies

- `upstream@55ffd248` 的完整渠道消息合同。
- `upstream/main@90cf9f96` 的 `McpServerSpec.candidate_read_only_tools` 与
  `ManagedServiceSpec.validation_port_env` 声明。
- `agent.delivery.DeliverySupervisor` 和 session outbox 仍是可靠投递真相源。
- 私有 Gate 报告 `20260820T052128Z-9ea05fe2` 冻结并通过了 20 个 Provider
  revision。

## Invariants

1. 一个逻辑消息只调用一个渠道 adapter，并返回 success、partial 或 failed 终态。
2. Telegram 等内置渠道不得因合同迁移重复发送附件。
3. DurableOutboundPort 必须保留 outbox-first 语义，并把终态转换为 DeliveryReceipt。
4. MCP 候选只读工具名必须是非空、无重复字符串；非法声明 fail-loud。
5. Runtime manager 在 prepare 阶段仍禁止后台任务；手工绑定 live scope 的上下文按 active
   测试上下文处理。
6. 当前单元不得用 catch-and-continue 或未知字段忽略来通过 Provider Gate。

## Runtime flow

1. MessagePushTool 把文本和附件组装成 ChannelMessage。
2. 已注册渠道 adapter 一次性提交逻辑消息并返回 DeliveryReceipt。
3. 被动总线和 durable outbox 把成功终态映射回现有 Turn 提交语义。
4. 插件安装和运行时收集阶段校验 Provider 声明并保留规范化字段。

## Data ownership and state

Channel adapter 拥有平台提交和 canonical media 结果；DeliverySupervisor 拥有 durable
outbox 终态；PluginManager 只拥有声明校验和 generation 视图。不得复制 Provider 凭据
或业务状态。

## Failure handling

渠道 adapter 异常转换为 failed/partial receipt；durable outbox 的 failed、unknown 和
cancelled 都返回失败 receipt。无效 MCP 或 service 声明在安装或 generation 收集阶段
直接拒绝。回滚点为 `241bc4e5`。

## Security

日志和 receipt 不记录 token。候选只读声明不会自动把正式工具降级为只读，也不会让
候选绕过 ToolPolicy。私有 Provider 仍只在离线、无网络 Gate 容器内执行。

## Observability

测试记录渠道终态、附件 canonical path 和 Provider 分阶段结果。公开与私有 Gate 报告
继续写到机械盘 Junction，不写入仓库大文件。

## Acceptance criteria

1. 渠道、outbox、Telegram、Web、Mobile 和 proactive 回归通过。
2. Feishu、QQBot、feed-mcp、fitbit-mcp 当前 revision 完成安装与原生测试。
3. prepare 阶段任务隔离测试继续通过，observe/proactive-feedback 当前测试通过。
4. 完整 pytest、Ruff、Pyright、公开 Gate 和 20-provider 私有 Gate 通过。
5. 无 Docker Gate 残留资源，无敏感信息进入 Git。

## Source evidence

- `bus/events.py:ChannelMessage,DeliveryReceipt`
- `agent/tools/message_push.py:MessagePushTool.dispatch`
- `agent/turns/outbound.py:DurableOutboundPort`
- `infra/channels/delivery.py:deliver_message_parts`
- `agent/plugins/specs.py:McpServerSpec,ManagedServiceSpec`
- `agent/plugins/install.py:_load_mcp_specs`
- `agent/plugins/manager.py:_resolve_mcp_servers,_resolve_managed_services`
- `agent/plugins/context.py:PluginContext.create_task`
- `tests/test_provider_contract_forward_port.py`

## Verification evidence

- Core commit: `b2b8f18c54fc3ad1bae0d945542155364e6d4005`。
- 完整测试：2455 passed，190 skipped。
- Ruff：通过；Pyright：0 errors。
- V3 路由报告：
  `H:/AkashicTestReports/author_akashic_closed_loop/docker-debug-reports/intent-routing/v3-provider-contract-20260820-125956.json`，Gate passed。
- 公开 Change Gate：
  `H:/AkashicTestReports/author_akashic_closed_loop/docker-debug-reports/change-gate/20260820-130805-d0199a75`，7 个场景通过。
- 私有 Contract Gate：
  `H:/AkashicTestReports/akashic-private-contract-gate/reports/20260820T052128Z-9ea05fe2`，20/20 Provider 通过且无残留容器。

## Open questions

无。后续若需要完整 candidate service 隔离和 promote/latest artifact 协议，应作为独立
单元从最新上游移植，不在此处扩张。
