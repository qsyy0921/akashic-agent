---
unit: akashic-vnext-local-integration
status: approved
---

# Akashic Agent vNext 本地集成 SDD

> 状态：approved / implementation in progress  
> 目标基线：`upstream/main@294ed10`  
> 实施账本：[`../../plans/002-vnext-local-integration.md`](../../plans/002-vnext-local-integration.md)  
> 最近核对：2026-07-23

## 1. 用户结果

在不破坏既有 Akashic workspace 的前提下，把最新上游版本运行成一个可持续维护的单机 Agent：

- Telegram 使用 `@akashic_qsyy0921_bot`；
- 主模型通过唯一的 OpenAI-compatible Responses 路径调用 `gpt-5.6-terra`；
- GPT 生图作为插件贡献的 MCP 工具被发现、授权、调用并作为图片投递；
- 被动对话、主动推送、记忆、工具、多工具调用、意图路由和 Supervisor 有可重复验证；
- 旧 Runtime 3.0 与路由实现只按契约迁移，不用旧目录覆盖新上游。

满足“验收标准”全部条件后停止。新发现若不影响这些结果，只写入 backlog，不进入本次实现。

## 2. 权威输入

按以下优先级解释冲突：

1. 用户当前 Goal 和明确批准边界；
2. [`../projectneed.md`](../projectneed.md) 的长期不变量；
3. accepted decisions 与 [`persistence-state-map.md`](persistence-state-map.md)；
4. 最新上游源码、配置、schema 和测试；
5. 旧 worktree 的 Runtime 3.0、Intent Routing V2/V3 SDD 和测试证据。

旧 `_handbook` 与旧账本是迁移证据，不覆盖新上游的插件、MCP、模型运行时和 workspace 合同。

## 3. 契约与依赖

```yaml
change_type: migration
semantic_delta: compatible
capability_owner: mixed
consumer_scope:
  - telegram
  - web-chat
  - passive-turn
  - proactive-turn
  - plugin-mcp
runtime_patch: required
runtime_patch_reason: >-
  Terra Responses transport、MCP 媒体结果和工具授权需要 Core 拥有协议、
  投递与安全语义；生图业务实现和 arXiv 等业务能力仍由插件拥有。
authoritative_state_owner:
  sessions: SessionManager
  workspace: selected Akashic workspace
  credentials: explicit config/environment/CredentialStore
  plugin_data: owning plugin
  delivery: Channel and outbound runtime
invariants:
  - OBJ-001
  - OBJ-002
  - STA-001
  - CAP-002
  - ERR-001
  - RUN-001
  - RUN-002
  - RUN-003
  - RUN-004
  - RUN-005
  - OUT-001
  - PLG-003
  - PLG-004
  - PLG-009
  - WSP-001
  - WSP-004
protected_state:
  - existing Akashic workspace contents
  - sessions.db messages and attachments
  - memory Markdown, memory2 and Akasha inputs
  - proactive, Wake and Drift continuity stores
  - user dirty worktrees and untracked files
allowed_effects:
  - fetch Git remote references
  - create an isolated Git worktree and local branch
  - run tests in disposable workspaces
  - call configured model and Telegram health endpoints during explicit smoke
  - replace the supervised local process only after candidate readiness
forbidden_effects:
  - commit, push, PR or force update without separate authorization
  - write secrets into Git, SDD, logs or test fixtures
  - send unsolicited Telegram messages
  - mutate the formal Akashic workspace during unit tests
  - add provider, store or protocol fallback paths
rollback: >-
  Keep E:\agent\author_akashic_runtime3 unchanged as the legacy recovery source,
  keep the current production checkout/process until candidate smoke passes,
  and switch back to the prior supervised command if cutover fails.
worktree_writer: E:\agent\author_akashic_vnext
```

### 3.1 设计不变量

- Core Runtime 独占 turn 顺序、可靠投递、授权、安全策略和 provider wire protocol；业务工具保持插件/MCP 可插拔。
- `terra-responses` 只使用配置的 OpenAI-compatible `/responses`，不试发 Chat Completions 或备用 provider。
- secret 只从 Git 外配置、环境变量或 `CredentialStore` 解析；测试和文档只使用无效占位值。
- 同一工具任务可执行多个独立 tool call，但候选、路由和模型输出都不构成副作用授权。
- 单机模块化单体是本次边界；不引入分布式一致性、第二套 Supervisor 或第二个 Bot poller。

## 4. 已核对事实、提案和未知

### 4.1 源码依据

| ID | 事实 | 证据 |
|---|---|---|
| F-001 | `origin/main` 与旧主目录同为 `2577ccb`；`upstream/main` 已到 `294ed10`，上游独有 38 个提交。 | Git refs 与 `rev-list` |
| F-002 | `E:\agent\author_akashic_runtime3` 含约 139 个 tracked 修改文件和大量新文件；Runtime 3.0 账本记录到 M6-07，Intent Routing V2/V3 账本均为 complete。 | 旧 worktree `git status` 与三个 progress 文件 |
| F-003 | 旧完成证据绑定 `2577ccb`，不能证明在 `294ed10` 上仍通过。 | 旧账本 baseline 字段 |
| F-004 | 新上游已引入 Supervisor、workspace lock、plugin generation、插件 MCP、模型 runtime、Responses transport、程序化控制和移动端协议。 | `main.py`、`agent/supervisor.py`、`agent/plugins/`、`agent/model_runtime/` |
| F-005 | 新上游要求 Skill 和 MCP 由插件 source 声明并经 generation 发布；workspace 直装只是兼容路径。 | PLG-009、`agent.plugins.base.Plugin.mcp_servers` |
| F-006 | 上游通用非 Codex provider 当前走 Chat Completions；现有 Responses transport 绑定 Codex auth 和流式协议。 | `agent/provider.py:LLMProvider`、`agent/model_runtime/transports/responses.py` |
| F-007 | 旧 Terra 实现固定使用 `gpt-5.6-terra`、Responses image tool 和同步非流式调用，但硬编码 endpoint 与 HOME workspace，不符合新 WSP 合同。 | 旧 `plugins/terra_imagegen/` 与对应测试 |
| F-008 | 最新上游 MCP client 能校验 text/image/resource 内容块，但目前把非文本块序列化为工具文本，没有自动形成 outbound media。 | `agent/mcp/client.py:McpClient.call` |
| F-009 | `@akashic_qsyy0921_bot` 是本次明确指定的 Bot；Token 属于外部 secret。 | 用户指令与截图 |
| F-010 | 最新上游 `private_runtime` 子模块在候选 worktree 尚未初始化。 | `git submodule status` |
| F-011 | 最新上游在 Windows 上不能完成测试收集：凭据存储无条件导入 `fcntl`，既有 `msvcrt` 锁又把 owner 正文写入被锁字节。 | 全量 pytest 的 80 个 collection errors；workspace/migration lock 的 2 个 targeted failures |
| F-012 | Windows 盘符路径被 app-server endpoint parser 误判为 `host:port`，导致 Python SDK 的 6 个真实 socket 测试失败。 | `sdk/python/tests/test_sdk.py` 与 `infra/control/socket.py:_parse_loopback_tcp` |
| F-013 | Change Gate catalog digest 直接哈希 checkout 换行，导致相同 Git 内容在 Windows CRLF 下被错误报告 stale。 | `gate.py audit` 的 digest 与 LF 归一化复算结果 |
| F-014 | 前端没有 lockfile；当前 semver 解析到不兼容的 `recharts`/Redux Toolkit/Immer 组合，且 Node 25 不能以 `shell:false` 直接 spawn `.cmd`。 | `npm run typecheck` 与 `npm run build` baseline |
| F-015 | 候选分支已实现 `terra-responses` profile、同步非流式 Responses transport、工具 continuation、typed failures 和主动链路 provider 复用。 | `agent/model_runtime/provider_profiles.py`、`agent/model_runtime/transports/responses.py`、`agent/provider.py`、`bootstrap/proactive.py`、`tests/test_terra_responses_runtime.py` |
| F-016 | 固定前端 lockfile 已归属现有 Change Gate `tooling` group，catalog baseline digest 同步且没有 accepted gap。 | `tests_scenarios/contracts/impact.toml`、`coverage-baseline.json`、`gate.py audit` |
| F-017 | `CredentialStore` 在 Windows 校验 owner/DACL，并加固目录、主文件、备份和锁文件；Telegram `auth` 只接受 `telegram_bot` driver，和内联 `token` 同时出现时拒绝加载。 | `core/common/private_path.py`、`agent/model_runtime/auth/store.py`、`agent/config.py`、`tests/test_private_path.py`、`tests/test_channel_credentials.py` |
| F-018 | Git 外候选配置已加载唯一 Terra runtime、指定 Bot 和本地 Qwen 4B embedding；真实 health 分别通过 Responses 随机 nonce、Bot `getMe` 和 1024 维非零向量。 | VNX-03 脱敏 live probe 与 `plans/002-vnext-local-integration.md` |
| F-019 | 既有 `memory2.db` 的 `vec_items` 是 `float[1024]`；本地 embedding API 在显式 `dimensions=1024` 时返回相同维度，因此本单元保留向量库且不重建。 | 只读 sqlite-vec schema probe 与 embedding health |
| F-020 | 候选 TOML 不含字面量 secret；候选凭据、旧正式配置和 CLIProxy 主配置的 Windows ACL 均已读回验证，值未写入 Git、命令行或账本。 | VNX-03 sensitive-key scan 与 private-path validation |
| F-021 | VNX-04 基线中，插件 MCP client 会校验但丢弃 `structuredContent`，`McpToolWrapper` 只能返回文本；`ToolResult` 与被动链路之间没有通用 MCP media bridge。 | VNX-04 实施前的 `agent/mcp/client.py:McpClient.call`、`agent/mcp/tool.py:McpToolWrapper`、`agent/tools/base.py:ToolResult` |
| F-022 | VNX-04 基线中，`ToolRegistry` 会把 `channel/chat_id` 等内部上下文合并到工具参数，MCP wrapper 未在进程边界前按远端 schema 投影参数。 | VNX-04 实施前的 `agent/tools/registry.py:ToolRegistry.execute`、`agent/mcp/tool.py:McpToolWrapper.execute_with_timeout` |
| F-023 | VNX-04 基线中，Telegram 会吞掉单张图片发送异常；修复后失败会向 MessageBus 传播，但总线在“文本已发、图片未发”后的整消息重试仍有部分重复风险。 | `infra/channels/telegram_channel.py:TelegramChannel._on_response`、`bus/queue.py:MessageBus._send_outbound` |
| F-024 | VNX-04 已实现受约束的 `structuredContent.media` 桥接：声明根、规范路径、普通文件身份、非符号链接、大小、SHA-256 和图片签名全部在 Core 内校验。 | `agent/mcp/client.py:McpClient.call_result/_parse_structured_media`、`agent/mcp/tool.py:McpToolWrapper`、`tests/test_mcp_media_bridge.py` |
| F-025 | `terra_imagegen` 是内置插件贡献的单一 MCP server；只接受被动链路中的明确用户生图请求，同一 turn 只执行一次，`n` 在一次工具调用中确定图片数量。 | `plugins/terra_imagegen/plugin.py`、`plugins/terra_imagegen/mcp/service.py`、`tests/test_terra_imagegen_plugin.py` |
| F-026 | 脱敏 live probe 已通过真实 Terra Responses 调用生成并校验一张 PNG；该证据覆盖 provider -> MCP -> 本地媒体，不覆盖 Telegram 用户端收图。 | `plans/002-vnext-local-integration.md:VNX-04 execution record` |
| F-027 | Windows checkout 的 CRLF 曾使 Linux Gate 镜像入口报 `bash\\r`；镜像构建现先规范化入口行尾，随后 7/7 公共场景通过且无 Docker 残留资源。 | `docker/debug/Dockerfile`、Change Gate report `20260723-033007-922007fd` |
| F-028 | 当前候选已实现 Intent Routing V3：上下文视图、lexical/Qwen dense/Terra IntentView 三路融合、ToolRegistry discovery digest、RuntimeSnapshot 绑定和 active Gate 均由当前源码重新生成并验证。 | `agent/routing/`、`agent/core/passive_turn.py`、`agent/tools/registry.py`、`eval/intent_routing/v3-gate-report.json` |
| F-029 | 最终 V3 Gate 覆盖 25 个用例、27 个目标和 13 个评测工具；Recall@1/3、完整集合召回、上下文、no-tool、澄清、可见率、IntentView 成功率和状态准确率均为 1.0。 | `plans/002-vnext-local-integration.md:VNX-05 execution record` |
| F-030 | Terra Responses 的真实 V3 Gate 出现可复现 HTTP 500 与流断开的 HTTP 408；Responses transport 将两者分类为可重试但不自行重试。IntentView 保留一次逻辑分析，仅允许同模型、同端点的一次追加尝试，并与首次尝试共享原总超时。重试仍失败时，当前快照下返回零预加载的 unavailable advice，让被动链路继续原 ToolSearch；快照不一致仍硬失败。 | `agent/model_runtime/transports/responses.py`、`agent/routing/intent_view.py`、`agent/routing/advisor_v3.py`、`plans/002-vnext-local-integration.md:SDD-CR-VNX-07-001` |
| F-031 | 正式 candidate 使用独立 Git 外 `PluginHome`，不读取旧服务的用户级共享插件 manifest；manifest 缺失时 launcher 失败。当前主动链路必须显式启用唯一 `default_proactive` provider。 | `scripts/run-akashic-supervised.ps1`、`plugins/default_proactive/plugin.py` |
| F-032 | 旧格式 manifest 可用单插件条目表达主动链路成员，但运行时发现以插件包开关为准；因此启动顺序必须先执行 `sync_manifest`，再发现和加载插件，否则首次启动会遗漏整包。 | `bootstrap/tools.py:CoreRuntime.start`、`tests/test_plugin_manager.py:test_core_runtime_start_wires_plugin_tool_hooks_to_loop_and_spawn`、`plans/002-vnext-local-integration.md:SDD-CR-VNX-07-002` |
| F-033 | 既有 workspace 的 AnyAction 配额文件可能是 version-1 的全空未使用哨兵；当前实现只兼容这一种精确状态，并在首次 snapshot 时通过原有 rollover 原子写成规范窗口。其他损坏状态继续失败关闭。 | `plugins/default_proactive/anyaction.py:QuotaStore._load/_is_legacy_pristine_state`、`tests/test_anyaction_quota.py`、`plans/002-vnext-local-integration.md:SDD-CR-VNX-07-003` |
| F-034 | `plugin doctor` 现在展开启用的内置插件包；显式包状态覆盖迁移前残留的成员条目，避免已禁用包被误报为启用。正式候选的六个插件均报告 healthy。 | `agent/plugins/doctor.py:run_plugin_doctor`、`tests/test_plugin_doctor.py`、VNX-07 live doctor |
| F-035 | Windows 私有路径加固只依赖 `icacls` 退出码与 Win32 ACL 读回，不再按当前代码页解码其输出，因此非英文用户名/系统语言不会触发无关的 `UnicodeDecodeError`。 | `core/common/private_path.py:harden_private_path`、`tests/test_private_path.py` |
| F-036 | 正式候选已由计划任务 `Akashic Agent Runtime3` 托管；PowerShell 同步拥有 `main.py supervise`，Python stdout/stderr 通过原始文件句柄写 UTF-8 日志，Windows 冷启动使用固定 60 秒 readiness 预算。Dashboard、Web Chat 和 app-server 分别监听 2236、6322、37071，且 readiness 与三端口属于同一 Gateway 进程。2026-07-24 受控切换后，计划任务保持 `Running`；下一分钟 trigger 已发生但 supervisor 启动计数不变，没有旧 launcher 或重复 poller。 | `scripts/run-akashic-supervised.ps1`、`tests/test_agent_restart.py`、VNX-08 continuation record |
| F-037 | 候选启动后加载 `akasha`、`default_memory`、`default_proactive`、`drift_flow`、`proactive_flow`、`terra_imagegen` 六个插件；默认主动生命周期已多次完成无内容 tick，没有伪造推送。 | VNX-07 candidate runtime log 与 plugin doctor |
| F-038 | 用户已对新 Bot 发送 `/start` 和约定 nonce；两条输入及两条回复按序进入 `sessions.db`，每条回复的 durable outbox 均为 `sent`、`attempt_count=1` 且只有一个 attempt，Telegram Web 可见两条回复。因此文本 Telegram E2E 已完成，不能据此声称图片 receipt。 | VNX-07 Telegram Web、database 与 outbox 脱敏核对 |
| F-039 | 公共 Change Gate 要求额外的 private maintainer Gate；当前锁定的 `private_runtime` 子模块没有本地 Git 对象，现有 SSH/GitHub CLI 身份也无仓库访问权。另一旧目录只有无版本脚本且不对应锁定提交，不能作为 Gate 证据。 | `.gitmodules`、`git submodule status`、VNX-08 access probe |
| F-040 | 当前最终公共验证为 `2380 passed / 186 skipped`、两套 Pyright 0 错误、前端 CI 通过、7/7 公共 Change Gate 通过且 Docker 无残留；高置信 token/private-key 扫描为 0。 | `plans/002-vnext-local-integration.md:VNX-08 execution record` 与最终 Git 外 Gate 报告 |
| F-041 | 正式候选已完成 Web Chat 真实被动往返：WebSocket 收到 `turn.started`、`answer.delta` 和 `message.final`，Terra 精确回显随机 nonce；同一会话持久化 user/assistant 消息，durable outbox 与唯一 delivery attempt 均为 `sent`。该证据覆盖 Channel 到可靠投递的完整本地链路，但不能替代 Telegram receipt。 | VNX-07 Web Chat E2E 与 `sessions.db` 脱敏只读核对 |
| F-042 | 正式候选的 Web Chat 生图 E2E 以一次明确请求完成一次 image tool call，并返回一个通过文件身份、非空与图片签名校验的媒体项；工具账本状态为 `external-side-effect / allow / succeeded`，对应 outbox 和唯一 delivery attempt 均为 `sent`。该证据把剩余风险收敛到 Telegram 媒体传输。 | VNX-07 Web Chat image E2E、`reliability_tool_calls` 与 `reliability_outbox` 脱敏只读核对 |
| F-043 | 先前的 Telegram nonce 阻塞已经解除；恢复后的文本 E2E 通过。`private_runtime` 访问拒绝在恢复审计中仍存在，必须在锁定 revision 上取得真实私有 Gate 证据，不能复用旧目录或公共 Gate。 | `plans/002-vnext-local-integration.md:External blocker audit` |
| F-044 | 第一次 Telegram 生图请求被 V3 正确路由到唯一生图 MCP，但主 Terra Responses 返回原生 `image_generation_call`，绕过了插件 hook、Core ToolPolicy、工具账本和 MCP media bridge；随后控制回复又因没有持久化 `delivery_id` 被 durable port 拒绝。原生调用可能已产生付费副作用，因此不能自动重试。 | 生产日志、`agent/model_runtime/transports/responses.py:OpenAICompatibleResponsesTransport._consume_response`、VNX-07 incident record |
| F-045 | 生图 MCP 现在可声明“单一高置信路由首步使用命名函数”；Core 只在 active、resolved、high-margin 且恰好一个候选时应用，后续步骤恢复 `auto`，hook/policy/ledger 仍在执行路径上。提交前控制回复改由 durable standalone intent 投递。真实 V3 Gate 已按当前摘要重跑并通过，服务已单实例重启；图片 receipt 仍等待一次新授权。 | `agent/plugins/specs.py`、`agent/plugins/manager.py`、`agent/tools/registry.py`、`agent/core/passive_turn.py`、`tests/test_intent_routing_v3_integration.py`、`tests/test_runtime3_delivery.py` |

### 4.2 已批准提案

| ID | 决定 |
|---|---|
| P-001 | 以最新 `upstream/main` 为集成基线；不把旧 worktree 整体 merge、copy 或覆盖到新树。 |
| P-002 | 旧实现按“上游已替代、可直接移植、必须重写、暂不需要”四类做能力差距表。 |
| P-003 | `terra-responses` 是 Core model runtime 的一种显式 provider profile，只走 Responses；失败直接暴露，不转 Chat Completions。 |
| P-004 | GPT 生图由插件贡献 MCP server；Core 只提供受约束的 MCP 元数据、ToolResult media 和可靠投递语义。 |
| P-005 | 意图路由先在新上游建立基线，再迁移 V3；候选只影响工具可见性，不绕过 ToolPolicy、approval 或执行预算。 |
| P-006 | Runtime 3.0 作为可靠性验收目录；上游已满足的单元以源码和当前 Gate 复核，只有缺口才写代码。 |
| P-007 | 正式进程切换采用 candidate readiness -> stop old supervisor -> start candidate -> health/smoke；失败恢复旧启动命令。 |
| P-008 | MCP media 使用 `structuredContent.media` 的受约束本地文件合同；Core 验证声明根目录、绝对路径、普通文件、非符号链接、大小、SHA-256 与图片签名后才生成 `ToolResult.media`。 |
| P-009 | 当前消息中的明确生图请求即为本次付费工具授权；插件 hook 拒绝 proactive/subagent、隐式请求和同 turn 第二次执行。多图只使用单次工具调用的 `n` 参数，不让 LLM 轮询或重复调用。 |
| P-010 | Intent Routing V3 作为 Core 的有界决策模块：业务工具元数据可插拔，但快照校验、active Gate、可见性合并和授权边界由 Runtime 持有。旧版路由报告只作历史证据，不能解锁当前 active。 |
| P-011 | 插件 MCP 可选择要求单一高置信路由在首个 reasoner step 使用命名函数；该选择不等于授权，不能绕过 hook、ToolPolicy、approval、预算或 ledger，多工具和后续步骤保持自动选择。 |
| P-012 | 正常回复继续使用 turn 与 outbox 原子提交；在正常提交前产生的 abort/error 控制回复必须显式写入 standalone durable intent，不能把未持久化 payload 交给 `DurableOutboundPort.dispatch`。 |

### 4.3 未决问题

- Bot 的 `allow_from` 与主动目标值已原样保留但不写入本文；文本消息往返已经验证。第一次生图尝试的付费副作用状态未知，下一次图片验收必须由用户重新明确授权且只能发送一次。
- 公共 Gate 已判定 private maintainer Gate 必需；当前身份无子模块仓库访问权，需所有者授予访问或提供正确仓库地址后运行。
- 通用 approval/ledger 已在 VNX-06C 落到核心可靠性 schema；聊天内“暂停原 turn、审批后恢复”的交互协议仍未实现，因此需要审批的 Bot 工具当前按 fail-closed 停在真实执行之前。

### 4.4 旧能力差距矩阵

| 能力 | 最新上游判定 | vNext 处理 | 当前证据 |
|---|---|---|---|
| Supervisor、默认托管启动、workspace lock | 上游已替代，但 Windows 锁实现有缺陷 | 保留上游结构，只修跨平台锁原语 | `main.py`、`agent/supervisor.py`、RUN-004 |
| App server、turn/session control、取消 | 上游已替代 | 不迁移旧 control/runtime 目录；以当前测试复核 | `agent/control/`、`agent/turns/`、`session/store.py` |
| 插件 generation、热重载、Skill、插件 MCP | 上游已替代且契约更新 | 使用新 `Plugin` contribution/generation API | `agent/plugins/`、`agent/mcp/`、PLG-009 |
| 被动/主动 pipeline、Markdown/memory2/Akasha | 上游已有独立演进 | 保留上游，按 P0 不变量做差距审计 | `agent/core/`、`proactive_v2/`、`core/memory/`、`plugins/akasha/` |
| Terra Responses 文本 provider | 上游缺失；旧实现绑定旧 API | 按当前 model runtime 重写唯一 Responses transport/profile | `agent/provider.py`、旧 Terra provider tests |
| GPT 生图 MCP 与 ToolResult media | 上游只有部分媒体边界 | 迁移行为契约，按新插件 generation 和 workspace 规则重写 | `agent/mcp/client.py`、`agent/tools/base.py`、旧 `terra_imagegen` |
| Intent Routing V3 | 上游缺失 | 迁移 contract/context/fusion/eval，重新跑当前工具目录与模型 Gate | 旧 `agent/routing/`、`eval/intent_routing/` |
| capability snapshot、ToolPolicy、approval ledger | 上游没有同等完整实现 | VNX-06C 已实现核心策略、精确审批绑定和持久化审计；路由仍不能授权 | `agent/tool_governance.py`、`session/tool_ledger_repository.py` |
| durable Inbox/Turn/Outbox 与 delivery reconciliation | 上游只有 turn 表和进程内 dispatch | VNX-06B 已实现消息/outbox 原子提交、单次 callback 和显式对账；durable inbound 留在 P1 | `session/store.py`、`agent/delivery/supervisor.py` |
| 主动兴趣反馈/ranker/投递策略 | 旧实现完整、上游只保留主动 pipeline | 作为 VNX-06 P0/P1 审计项；不阻塞 Terra 与生图 | 旧 Runtime3 M4/M6 账本 |
| arXiv 业务工具 | 上游缺失，旧插件使用旧描述格式 | 在核心链路稳定后迁移到新插件 API；不放进 Core | 旧 `plugins/arxiv_research/` |
| 移动端与私有 runtime | 上游已有，当前 Goal 非目标 | 只准确报告 Gate 状态，不为本地 Telegram 交付扩展 | `infra/mobile_realtime/`、未初始化 `private_runtime` |

VNX-06 的源码复核把范围收敛为单机可靠性层：当前 `turns`、会话 admission、每会话 lane、插件 generation lease 和 Supervisor 已满足对应 P0，没有重复实现。新增的可靠性 schema 只拥有两类缺口：`reliability_outbox` 记录持久化投递真相，`reliability_tool_calls` / `reliability_tool_approvals` / `reliability_audit_events` 记录最终参数下的授权与执行真相。生产 `AppRuntime` 不再启动内存 outbound worker；`unknown` 只能显式对账，不能自动重发。完整合同和当前验证状态见 `plans/002-vnext-local-integration.md:SDD-CR-VNX-06-001`。

### 4.5 VNX-06 当前工具治理合同

1. 插件 `pre_tool_use` 可以拒绝或改写参数，但不能授权；Core 在参数改写完成后计算 canonical hash 并执行最终策略。
2. `read-only`、`write`、`read-write` 和已分类的普通 `external-side-effect` 当前允许执行并进入 ledger；未知风险 fail-closed。
3. `agent_restart`、`workspace_mcp_apply`、`workspace_mcp_remove`、`destructive` 风险，以及被动/subagent 的 `message_push` 要求操作员审批。
4. proactive/Drift 的 `message_push` 只是本轮草稿 staging，策略允许后仍须经过 resolver、session/outbox 原子提交和 delivery supervisor，staging 成功不等于 Telegram 已送达。
5. 审批绑定 `turn_id + snapshot_id + tool_name + args_hash`，只能消费一次；参数、快照、call binding 或终态发生变化时拒绝重放。
6. 外部副作用抛出异常时，ledger 记录 `unknown` 而不是自动重试；本地可判定失败记录 `failed`。
7. 数据库不保存原始参数或工具结果，只保存脱敏限长摘要、参数哈希和结果摘要。
8. 当前只提供核心 operator service 方法，没有聊天内 suspended-turn/resume 协议；审批必需工具不会伪装执行成功。

## 5. 目标与范围

```text
┌──────────────── Telegram / Web Chat ────────────────┐
│ Inbound + Attachment                                │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌──────────────── Core Runtime ────────────────────────┐
│ Supervisor -> Channel -> MessageBus -> Turn Runtime  │
│ Session order | budgets | ToolPolicy | outbound      │
└──────────────┬───────────────────────┬───────────────┘
               │                       │
               ▼                       ▼
┌──── Model Runtime ─────┐   ┌──── Plugin Generation ──┐
│ terra-responses        │   │ intent modules          │
│ one Responses protocol │   │ arXiv MCP               │
└──────────┬─────────────┘   │ GPT image MCP           │
           │                 └──────────┬───────────────┘
           ▼                            ▼
┌── OpenAI-compatible ───┐   ┌──── Tool Registry ──────┐
│ gpt-5.6-terra proxy    │   │ discovery -> policy ->  │
└────────────────────────┘   │ executor -> ToolResult  │
                             └──────────┬───────────────┘
                                        ▼
                              ┌──── Outbound Media ─────┐
                              │ workspace-owned file -> │
                              │ Telegram/Web attachment │
                              └─────────────────────────┘
```

### 5.1 职责与非目标

#### Core 不插件化

- turn admission、同 session 顺序、取消和预算；
- ToolPolicy、approval、ledger 和 capability snapshot；
- session/turn 持久化、outbound 提交和 channel dispatch；
- provider wire protocol、secret 解析和 Supervisor。

#### 插件化

- arXiv、图片生成等业务工具；
- intent 的规则、检索、模型视图与提示注入模块；
- proactive source、ranking 特征和业务反馈解释；
- dashboard/plugin UI。

### 5.2 Terra Responses 已实现合同

1. 配置层只把 `provider = "terra-responses"` 与 `model = "gpt-5.6-terra"`、`reasoning_effort = "high"` 的组合识别为该 profile。
2. `LLMProvider` 为该 profile 只构造 `OpenAICompatibleResponsesTransport`；Codex Responses 和既有 Chat Completions 路径保持独立。
3. base URL 必须是无内嵌凭据、query、fragment 和 method suffix 的 HTTP(S) API root；非 loopback 明文 HTTP 在请求前拒绝。
4. transport 固定发送非流式、`store=false` 的 `/responses` 请求，携带工具 schema、命名/自动 tool choice、并行工具能力、输出预算和 encrypted reasoning continuation。
5. `model_state` 只保存同 runtime、同模型的 opaque reasoning item；下一轮与 assistant function call、tool result 一起无状态重放。
6. 认证、限流、额度、上下文、服务端、超时和协议错误使用已有 model runtime 类型显式失败；不重试到 Chat，也不伪造成功。
7. 主动链路复用同一 Terra provider；调用方的通用“关闭思考”参数不能破坏 profile 固定的 high reasoning 合同。

VNX-03 已通过由候选配置和 `CredentialStore` 组装的真实随机 nonce 请求；响应模型、usage 和正文均满足合同。该证据只证明依赖与候选配置 ready，不等同于最终 Supervisor 或 Telegram 消息往返。

### 5.3 本机配置与凭据合同

1. Git 外 TOML 只保存 `auth` 引用；Terra、Telegram 和 embedding 的值保存在一个显式 `CredentialStore` 文件中。
2. 正式配置只声明一个 `terra-responses` named runtime；未配置独立 fast/agent/vl runtime 时，各调用方显式复用 main，不创建第二 provider 或 fallback。
3. Telegram `auth` 与 `token` 互斥；缺失、错误 driver 或过宽 ACL 都在配置加载时失败。
4. Windows 私有目录、凭据主文件、备份、锁文件及含 secret 的外部服务配置必须通过 owner/DACL readback；POSIX 继续使用 `0700/0600`。
5. 本地 Qwen 4B embedding 固定请求 1024 维，以匹配现有 memory2 向量表；模型或维度不匹配时失败，不自动重建正式记忆。

### 5.4 MCP media 与生图授权合同

1. `McpServerSpec` 可声明有限的 `media_output_roots` 和单次 `call_timeout_seconds`；目录必须是 workspace 内的相对路径，不能是 workspace 根、绝对路径或含 `..` 的路径。
2. MCP `structuredContent.media` 每项固定包含 `kind=image`、绝对 `path`、`mime_type`、`sha256` 和 `size_bytes`；最多 4 项、单项最多 10 MiB，只接受 PNG/JPEG/WebP。
3. Core 在打开文件后以实际文件句柄复核普通文件、大小、哈希和签名；远端 URL、符号链接、越界路径、重复路径或自报元数据不一致均使本次工具调用失败。
4. MCP wrapper 只向远端发送其 input schema 声明的参数；Agent 内部 `channel/chat_id` 上下文不跨 MCP 进程边界。
5. 生图插件只暴露一个同步 `generate_image` 工具。一次工具调用按 `n=1..4` 生成固定数量图片；每张图片对应一次非重试 Responses `image_generation` 请求，任一失败会删除该调用尚未提交的全部文件。
6. pre-tool hook 只允许 passive turn 中当前用户消息的明确生成/绘制图片意图；同一 turn 只消费一次预算，批量重复调用全部拒绝。工具失败后不在同 turn 自动重试。
7. MCP 子进程从 `AKASHIC_CONFIG_FILE` 和 `AKASHIC_WORKSPACE` 加载同一 Terra runtime，凭据仍由 `CredentialStore` 解析；插件不声明、复制或记录 key、token 或 endpoint 凭据。
8. Telegram 图片发送异常必须向 MessageBus 抛出，不能记录 warning 后伪装送达；跨文本与媒体的持久化去重/恢复由 VNX-06 的 outbound 合同完成。
9. 生图 MCP 可声明高置信命名选择。Core 只在当前 active route 为 `resolved/high_margin`、恰好一个 routed tool 且该工具显式声明时，要求首个 reasoner step 返回该函数；后续 step 恢复 `auto`。路由仍不授予执行权限，插件 hook、ToolPolicy、approval、预算和 ledger 必须继续通过。
10. 主文本 provider 返回原生 `image_generation_call` 不属于被动链路的受信媒体合同，必须失败关闭；Core 不把它转换为图片、不绕过 MCP，也不自动重试可能已计费的调用。

### 5.5 Intent Routing V3 合同

1. 路由只处理 passive natural-language turn。协议控制消息先由确定性 gate 排除；主动候选排序、工具执行和任务 DAG 不属于本模块。
2. `RouteContext` 最多读取 8 条已提交 user/assistant 消息、总计 6000 字符；reply excerpt 最多 800 字符。它可携带上一轮 operation/output kind，但不读取长期记忆正文、完整工具结果、二进制附件或 secret。
3. 原始消息同时进入 metadata lexical 与本地 Qwen 4B dense 支路。Terra 在同一 turn 恰好调用一次严格命名函数，生成 1..4 个不含真实工具名的 IntentGoal/HypotheticalCapability；每个 goal 最多 3 个派生检索查询。
4. 融合先在每个 operation 内选一个 provider，再对 original lexical、original dense 和 LLM view 做确定性 RRF；每个 goal 最多 3 个候选，整轮最多 preload 5 个 goal 的首选工具，包括同一请求中声明依赖关系的后续 goal。阈值下降不能替代 Top-1/集合指标。
5. 工具发现文档由当前 `ToolRegistry` 的受信元数据和 schema 派生。没有显式等价关系时，每个工具使用自己的稳定 operation id；动态插件/MCP 发布新 `RuntimeSnapshot` 后立即形成新 discovery digest。
6. `RouteAdvice` 必须同时绑定当前 `RuntimeSnapshot.snapshot_id` 和 discovery digest。Reasoner 应用前重新验证 snapshot、工具存在性和 disabled 集合；`preloadable` 只约束跨 turn LRU，当前 turn 的 `requires_turn_search` 授权仍由独立 search scope 控制。任何快照不一致在 active 模式中显式失败。
7. 路由候选只扩大本轮 LLM 可见 schema。它不能写 session LRU，不能给 `requires_turn_search` 工具授予当前 attempt 的 search grant，也不能绕过 tool hook、approval、风险预算或 executor。
8. shadow 模式计算并记录脱敏指标，但不修改可见集合；依赖不可用时输出结构化 unavailable。active 模式必须在启动时验证与当前源码、prompt、模型、配置、数据集和目录 digest 完全一致的通过报告；运行时失败不退回关键词-only 或全工具模式。
9. dense 只调用配置的 OpenAI-compatible `/embeddings`，在内存中按 discovery digest 缓存目录向量；不建立第二数据库、不持久化用户消息/查询/embedding。候选机固定复用 `qwen3-embedding:4b` 的 1024 维接口。
10. 离线 Gate 至少约束 operation Recall@1/3、完整 operation 集合、no-tool precision/recall、上下文消解、必要/多余澄清、可见性和延迟；旧 worktree 的报告与 digest 不匹配，不能作为当前证明。

## 6. 数据所有权与状态

| 对象 | 增加 | 原位/逻辑变化 | 物理减少 | 恢复证据 |
|---|---|---|---|---|
| Git candidate worktree | 新增源码、测试、SDD diff | 当前分支前进 | 不自动清理旧 worktree | branch HEAD、完整 diff、旧 worktree 保持不变 |
| `sessions.db/messages` | 正式 turn 追加消息 | 本 Goal 不改旧正文 | 仅用户独立删除操作允许 | cutover 前后完整消息快照和最大 seq |
| `uploads/` 与生成图片 | 用户请求产生新文件并由消息引用 | 不原位覆盖 | 本 Goal 不实现 GC | 文件 hash、session media 引用、Telegram receipt |
| plugin-data | 插件按自己的 schema 增加状态 | 仅插件状态机更新 | 卸载不删除；本 Goal 不永久删除 | 隔离 workspace 重启后读取同状态 |
| 配置和凭据 | 用户在本地 secret 边界写入 | 明确配置管理可替换 | 本 Goal 不删除凭据 | 脱敏 config load、provider health、Bot getMe |
| proactive/Wake/Drift DB | 正式 tick 追加连续性记录 | 按既有状态机更新 | 本 Goal 不清理 | 重启后 dedupe/cursor/ack smoke |

测试只使用一次性 workspace、plugin home、config、HOME 和假 provider，不读取或写入正式状态。

## 7. 运行时流程

### 7.1 被动生图

```text
用户请求 -> Context/Intent candidate -> ToolSearch 暴露生图 schema
-> ToolPolicy 绑定当前 turn 与参数 -> GPT image MCP
-> Responses image_generation -> workspace-owned image file
-> ToolResult(text + media) -> Reasoner 最终文本
-> Turn commit -> outbound dispatch(text + image) -> Telegram receipt
```

同一请求可以调用多个工具；每个调用独立进入 policy、budget 和 ledger。图片只在工具成功且路径属于允许根目录时进入 outbound。

### 7.2 主动链路

业务插件只提供 source event 和候选。主动 runtime 读取长期记忆和近期上下文进行判断，delivery policy 再决定是否发送。生图工具默认不由主动候选自行触发；若以后允许，必须单独定义付费副作用、频率和用户授权合同。

## 8. 失败处理

- provider/model/protocol 不匹配：配置加载或首个 probe 明确失败，不试发另一协议；
- MCP 启动或 schema 错误：拒绝 candidate generation，保留 last-known-good；
- 生图超时：本次调用失败，不自动重发可能产生付费副作用的请求；
- 图片内容无效：拒绝文件发布，删除本次未提交临时文件，不影响旧附件；
- media 路径越界、符号链接或非普通文件：拒绝 outbound 并记录安全原因；
- Telegram dispatch 失败：保留 turn 和 delivery 的真实状态，不伪造已送达；

### 8.1 安全

- approval 未完成：返回结构化等待状态，不让 LLM 无限轮询或重复生成；
- `CredentialStore` 引用缺失、driver 错误、双来源或 ACL 过宽：启动失败，不读取备用 token；
- 日志只保留脱敏错误分类、correlation id 和状态，不记录 secret 或完整图片 payload。

## 9. 可观测性

- 单元账本记录每个 slice 的基线、write set、命令、结果、失败归因、fallback 与剩余风险。
- model runtime 对外暴露分类后的认证、限流、额度、上下文、网络和协议失败；不得把依赖失败记录为成功回复。
- live probe 只记录 endpoint 类型、模型 ID、HTTP/协议状态和 correlation id，不记录 Authorization、完整响应正文或用户消息。
- Supervisor、Telegram poller、Dashboard、MCP 与主动 tick 的最终运行证据在 VNX-07/VNX-08 以 PID、boot id、health 和 receipt 对账。

## 10. 工作单元

详细 write set、非目标和命令由实施账本维护。固定顺序如下：

1. `VNX-00`：冻结基线、恢复源和 SDD。
2. `VNX-01`：运行最新上游 baseline，生成旧能力到新能力的差距矩阵。
3. `VNX-01A`：关闭 Windows runtime/platform baseline 缺口。
4. `VNX-01B`：固定前端依赖图并完成 Windows 可重复构建。
5. `VNX-02`：实现 Terra Responses provider profile 与 transport。
6. `VNX-03`：脱敏迁移正式配置并验证新 Bot/embedding/模型健康。
7. `VNX-04`：实现 GPT image plugin MCP、媒体桥接和 Telegram 投递测试。
8. `VNX-05`：迁移意图路由 V3 和多工具评测，先 shadow 后 active。
9. `VNX-06`：按当前源码复核 Runtime 3.0，只实现未被上游满足的 P0 缺口。
10. `VNX-07`：Supervisor 单实例、自动重启和正式 candidate cutover。
11. `VNX-08`：全量 Gate、SDD 对账和最终运行证据。

任何单元在自己的验收通过且 SDD 与代码一致时停止，不顺手实现下一单元。

## 11. 验证层级

1. 单元测试：provider payload、MCP schema、路径约束、policy、media、routing。
2. 受影响回归：model runtime、plugin generation、tool loop、outbound、Telegram、proactive。
3. 静态与构建：production/test pyright、前端 typecheck/build、secret scan。
4. Change Gate：`python docker/debug/gate.py run --base upstream/main`，准确报告 private 状态。
5. 隔离 smoke：临时 workspace 下启动 Supervisor，Web/Dashboard/插件 readiness 可用。
6. 明确授权的 live smoke：Terra 文字回复、一次低数量图片生成、Bot getMe 与一条用户触发回复。
7. Cutover smoke：只有一个 Bot poller；Dashboard、Telegram、memory、proactive health 均可观察。

## 12. 回滚

- 代码：保留 `upstream/main@294ed10` 和旧 `E:\agent\author_akashic_runtime3`；candidate 未通过前不替换正式 checkout。
- 运行：记录旧 Supervisor 命令/PID/端口；candidate readiness 失败时停止 candidate 并恢复旧命令。
- 配置：修改前创建不含输出内容的权限保持备份；失败恢复原文件，不修改凭据值。
- workspace：本 Goal 不执行破坏性迁移；需要一次性迁移时另建 work unit、backup 和 integrity check。

## 13. 验收标准

- [x] 最新上游 baseline 与旧能力差距均有当前证据。
- [x] `@akashic_qsyy0921_bot` 在单一 Supervisor 下接收并回复同账号消息，持久化与唯一 delivery attempt 已核对。
- [x] 主模型明确通过 `terra-responses` 调用 `gpt-5.6-terra`，没有 Chat fallback。
- [ ] 用户明确请求时，GPT image MCP 只生成请求数量的图片并由 Telegram 正确投递。
- [x] 意图路由支持上下文、多目标和多工具，离线 Gate 与 active smoke 达标。
- [ ] 被动、主动、记忆、插件/MCP 和投递的 P0 不变量通过当前 Gate。
- [x] Windows 自动启动/重启不产生重复 poller，失败状态可观察。
- [ ] 相关 SDD 和账本从 proposed/in-progress 对账到 verified，未完成项与风险明确。
- [x] 未新增 secret、生产 fallback、silent success、mock/no-op 或无界重试。
