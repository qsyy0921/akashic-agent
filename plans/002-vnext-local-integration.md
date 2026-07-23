# 002 · vNext 本地集成实施账本

> Normative SDD: [`../docs/design/vnext-local-integration.md`](../docs/design/vnext-local-integration.md)  
> Branch: `codex/vnext-20260723`  
> Worktree: `E:\agent\author_akashic_vnext`  
> Delivery: local

## Unit status

| Unit | Status | Depends on | Stop evidence |
|---|---|---|---|
| VNX-00 | DONE | - | 基线、恢复源、SDD、工作单元和 secret 边界已固定 |
| VNX-01 | DONE | VNX-00 | 上游 baseline Gate 与旧能力差距矩阵完成 |
| VNX-01A | DONE | VNX-01 | Windows runtime primitives、全量 pytest、两套 pyright 与 public Gate audit 通过 |
| VNX-01B | DONE | VNX-01A | 可重复前端 install、typecheck 与 build 通过 |
| VNX-02 | DONE | VNX-01B | 严格 `/responses`、续接、错误分类、主动复用与全量回归通过 |
| VNX-03 | DONE | VNX-02 | 脱敏 config load、Bot getMe、embedding/model health 与 Windows ACL 通过 |
| VNX-04 | DONE | VNX-02, VNX-03 | 生图插件、媒体投递、失败与路径负向测试通过 |
| VNX-05 | DONE | VNX-01, VNX-02 | 当前 digest Gate、active 启动、多工具、上下文与授权隔离通过 |
| VNX-06 | DONE | VNX-01, VNX-05 | 可靠投递、工具治理与连续性 Gate 通过 |
| VNX-07 | BLOCKED | VNX-03, VNX-04, VNX-06 | Candidate cutover、单实例和自动重启已通过；等待用户发起 Telegram 私聊 |
| VNX-08 | BLOCKED | VNX-07 | 公共验证已通过；等待 private maintainer Gate 仓库访问权 |

## VNX-00 · 基线与 SDD

- 可观察行为：后续实现只在最新上游候选 worktree 发生，旧 dirty worktree 和正式 workspace 不变。
- 允许路径：`docs/INDEX.md`、`docs/design/vnext-local-integration.md`、`plans/README.md`、本文件。
- 非目标：不改生产代码、配置、数据库、进程或远端分支。
- 验证：Git refs/worktrees/status；文档链接；secret pattern scan；完整 diff review。
- 停止：上述证据通过后把本单元改为 DONE，立即进入 VNX-01。

## VNX-01 · 当前基线与能力差距

- 可观察行为：得到可重复的上游测试基线和“上游已替代/可移植/需重写/不需要”矩阵。
- 允许路径：本文件和 SDD 的事实/矩阵；测试报告目录由现有 Gate 决定。
- 非目标：不迁移旧生产代码，不修改 oracle 以获得全绿。
- 验证：pytest、两套 pyright、前端 typecheck/build、change Gate；每个失败归类。
- 停止：所有 baseline 结果和差距 owner 已记录。

## VNX-01A · Windows runtime baseline unblockers

- 可观察行为：Windows 可导入完整 runtime；同进程/跨进程文件锁 fail-closed；盘符 socket 路径不当 TCP；Gate digest 不受 CRLF 影响。
- 允许路径：`core/common/file_lock.py`、五个现有锁调用方、`infra/control/socket.py`、`docker/debug/gate.py`、对应 tests 和本账本。
- 非目标：不改业务 schema、provider、插件、正式 workspace 或移动端 private Gate。
- 验证：锁冲突/释放、凭据原子写、SDK socket、Gate LF/CRLF、两套 pyright、全量 pytest；POSIX-only stop-script 测试在 Windows 明确 skip。
- 停止：Windows Python baseline 不再因平台原语失败；其他真实失败单独归因。

## VNX-01B · 前端可重复构建

- 可观察行为：全新 `npm ci` 得到同一依赖图，Windows 不直接 spawn `.cmd`，typecheck/build 均通过。
- 允许路径：`package.json`、`package-lock.json`、`scripts/build-plugin-preset.mjs`、`scripts/build-plugins.mjs`、对应最小验证与本账本。
- 非目标：不改 UI、组件行为、CSS 视觉或插件业务代码。
- 验证：删除隔离 `node_modules` 后 `npm ci`、`npm run typecheck`、`npm run build`。
- 停止：三条命令全绿且 Git diff 只含 lock/build portability 变化。

## VNX-02 · Terra Responses provider

- 可观察行为：named runtime `terra-responses` 仅向配置 endpoint 的 `/responses` 发送 `gpt-5.6-terra` 请求，保留工具调用 continuation。
- 实际允许路径：`agent/model_runtime/provider_profiles.py`、`agent/model_runtime/transports/responses.py`、`agent/provider.py`、`agent/config_models.py`、`bootstrap/proactive.py`、`config.example.toml`、对应 tests、SDD 与本账本。
- 非目标：不接入生图 MCP，不增加备用 provider，不改变其他 provider。
- 验证：payload、reasoning、工具 schema/choice、continuation、usage、timeout、错误分类、错误 endpoint/model 的负向测试。
- 停止：targeted 与 affected checks 通过且 provider SDD 已对账。

## VNX-03 · Bot、配置与 embedding

- 可观察行为：正式 secret config 能加载新 Bot、Terra main runtime 和既有本地 Qwen embedding；值不进入 Git 或日志。
- 允许路径：正式 Git 外配置、`CredentialStore`/private-path ACL、Telegram auth 引用、必要的配置测试、SDD 与本账本。
- 非目标：不发送测试广告，不改记忆事实，不重建正式向量库。
- 验证：脱敏 config inspection、Bot getMe identity、本地 embedding dimension/model health、Terra text probe。
- 停止：三个 dependency health 均明确 ready 或以外部缺失阻塞。

### SDD-CR-VNX-03-001 · Telegram credential reference

```yaml
status: implemented in VNX-06B/VNX-06C; continuity Gate pending in VNX-06D
public_contract: channels.telegram.auth
change:
  - auth resolves one CredentialStore entry with driver=telegram_bot
  - legacy token remains supported for compatibility
  - configuring auth and token together is rejected as ambiguous
security_reason: keep the Bot token out of config, process arguments, Git and logs
non_goals:
  - no second secret store
  - no automatic fallback from a missing auth entry to token
  - no Telegram message send during credential migration
acceptance:
  - auth success, wrong driver, missing credential and dual-source tests
  - Windows ACL harden/readback and injected broad-principal negative test
```

## VNX-04 · GPT 生图插件

- 可观察行为：明确生图请求产生固定数量图片，工具结果经受约束 media path 进入 Telegram/Web outbound。
- 预计允许路径：新插件 canonical source、MCP typed declaration、最小 Core media/policy bridge、对应 tests 和 SDD。
- 非目标：不允许主动链路自动付费生图，不信任远端 MCP 自报风险，不加入自动重试。
- 验证：plugin generation、MCP handshake、Responses image payload、原子文件、路径越界、部分失败、approval、tool loop、Telegram media tests；一次授权 live smoke。
- 停止：一条请求只生成一次，图片 receipt 可观察，失败不伪装成功。

### SDD-CR-VNX-04-001 · MCP media、调用预算与显式授权

```yaml
status: implemented and verified in VNX-04
public_contracts:
  - McpServerSpec.call_timeout_seconds
  - McpServerSpec.media_output_roots
  - MCP structuredContent.media
  - ToolResult.media
  - PreToolCtx.turn_id
  - PreToolCtx.is_preflight
change:
  - validate local image path, file identity, size, sha256, signature and declared root
  - project MCP arguments to the remote input schema before crossing the process boundary
  - carry successful MCP media through ToolResult into passive outbound media
  - require an explicit passive image-generation request and one execution per turn
  - propagate Telegram media-send failure instead of reporting false delivery
security_reason: external MCP output and model-selected side effects are untrusted inputs
reliability_reason: image generation is paid and non-idempotent, so automatic retry or model polling is forbidden
limits:
  media_count: 4
  media_file_bytes: 10485760
  call_timeout_seconds_max: 600
non_goals:
  - no proactive image generation
  - no remote URL media
  - no generic approval ledger or durable multi-part outbound implementation
  - no alternate image provider, protocol fallback or automatic retry
acceptance:
  - MCP declaration, handshake, timeout and argument-boundary tests
  - media path, symlink, hash, size, signature and duplicate negative tests
  - plugin payload, fixed-count, cleanup, explicit-intent and one-turn-budget tests
  - passive ToolResult media and Telegram failure propagation tests
  - one explicitly authorized live image smoke after isolated checks pass
```

## VNX-05 · 意图路由 V3

- 可观察行为：上下文感知、lexical/dense/LLM IntentView 融合输出有界候选；一次请求可暴露多个工具。
- 允许路径：`agent/routing/**`、`agent/tools/registry.py`、`agent/config*.py`、`bootstrap/{providers,tools}.py`、`agent/core/passive_turn.py`、`eval/intent_routing/**`、对应 tests、Gate impact 与 SDD。
- 非目标：不生成执行 DAG，不绕过 policy，不改主动排序，不加第二向量库。
- 配置合同：`off|shadow|active` 三态；IntentView 恰好一次命名函数调用；dense 只使用显式 OpenAI-compatible embedding 配置，候选机复用本地 Qwen 4B/1024 维，不持久化消息或向量。
- 快照合同：路由请求、工具目录和执行共享当前 `RuntimeSnapshot.snapshot_id`；工具目录由当前 `ToolRegistry` 派生并计算独立 digest，插件切代后旧建议不得应用。
- 授权合同：active 只把最多 5 个、当前快照存在且未禁用的候选并入本轮 preload；`requires_turn_search`、tool hook、approval、预算和执行器继续独立裁决。
- 失败合同：shadow 只记录结构化不可用结果且不改变可见工具；active 的模型、embedding、快照或 Gate 失败直接中止本轮路由，不切关键词-only、旧模型或默认全工具。
- 验证：shadow parity、Recall@K、no-tool false positive、多目标、多工具、上下文省略、动态插件、模型超时/非法输出、digest Gate。
- 停止：先达到 SDD Gate，再由本 Goal 授权 active；不得靠降低阈值掩盖 Top-1 问题。

### VNX-05 execution contract

```yaml
unit_id: VNX-05
status: implemented_and_verified
baseline:
  upstream_routing_modules: absent
  recovery_source: E:\agent\author_akashic_runtime3@2577ccb dirty evidence tree
  recovery_report_authority: historical_only
architecture:
  intent_view_calls_per_turn: 1
  context_messages_max: 8
  context_characters_max: 6000
  goals_max: 4
  candidates_per_goal_max: 3
  preloads_max: 5
  retrieval_channels: [original_lexical, original_dense, llm_intent_view]
  embedding_persistence: none
  snapshot_authority: RuntimeSnapshot.snapshot_id
  candidate_is_authorization: false
activation:
  shadow: may observe without changing tool visibility
  active: requires exact current digest Gate and startup readiness
fallbacks_allowed: []
verification_record: see VNX-05 execution record below
```

## VNX-06 · Runtime 3.0 差距

- 可观察行为：当前上游对旧 Runtime 3.0 P0 合同有源码与 Gate 证据，真实缺口被最小修复。
- 允许路径：`agent/delivery/`、`agent/policy/`、`agent/turns/`、`agent/tool_hooks/`、`bus/queue.py`、`session/`、`bootstrap/`、`proactive_v2/`、对应 tests、SDD 与本账本；每个子单元仍按最小 write set 执行。
- 非目标：不回灌旧架构、不引入分布式系统、不复制上游已有 Supervisor/plugin/runtime。
- 验证：session order、outbound truth、cancel、snapshot binding、tool policy、memory/proactive continuity、crash/replay。
- 停止：差距矩阵不存在未处理 P0，P1 只记 backlog。

### SDD-CR-VNX-06-001 · 单机可靠投递与工具决策账本

```yaml
status: approved under the active root Goal
public_contracts:
  - sessions.db reliability schema
  - OutboxState: [pending, sending, sent, failed, unknown, cancelled]
  - ToolDecision: [allow, deny, require_approval]
  - ToolCallState: [prepared, executing, succeeded, failed, unknown, denied, cancelled, awaiting_approval]
  - ApprovalState: [requested, granted, consumed, denied, expired, revoked]
change:
  - atomically commit visible assistant message and outbound intent on passive and proactive paths
  - recover committed pending intents after restart
  - perform exactly one channel callback attempt for each claimed delivery attempt
  - classify no registered channel as known failed and callback exceptions as unknown
  - never automatically retry failed or unknown external delivery
  - require explicit reconciliation before an unknown delivery can become sent, failed or requeued
  - evaluate every real tool execution in a core-owned policy engine and append decision/call/audit records
  - keep route candidates and plugin hooks unable to grant authorization
security_reason: model output, route candidates, plugin metadata and MCP declarations are untrusted
reliability_reason: Telegram text plus media and paid tools may have partial external effects before an exception
migration:
  owner: SessionStore single SQLite connection
  mode: append-only versioned reliability migrations
  rollback: restore sessions.db together with its WAL and SHM files from the cutover backup
delivery_invariants:
  - pending may be claimed once
  - startup converts abandoned sending records to unknown
  - sent is terminal and cannot be replayed
  - failed is known-not-sent and may only be requeued explicitly
  - unknown is never requeued without an explicit operator decision
  - delivery_id and idempotency_key are unique
policy_invariants:
  - visibility is not authorization
  - deny and require_approval stop before Tool.execute
  - approval is bound to tool, canonical arguments, turn and snapshot
  - side-effecting calls have durable terminal or unknown truth
current_policy:
  allow:
    - classified read-only, write and read-write tools
    - proactive message_push staging; external delivery remains governed by durable outbox
    - classified external-side-effect tools outside the bounded approval set
  require_approval:
    - agent_restart
    - workspace_mcp_apply
    - workspace_mcp_remove
    - passive or subagent message_push
    - destructive risk
  deny:
    - unclassified risk
    - missing turn/session identity
    - replayed call_id
    - stale or changed approval binding
  secret_storage:
    - canonical argument hash
    - bounded redacted argument summary
    - result digest only
  note: route visibility and plugin metadata never grant authorization
fallbacks_allowed: []
non_goals:
  - no distributed queue, broker or leader election
  - no automatic delivery retry or alternate channel/provider
  - no wholesale port of the legacy Runtime3 kernel or duplicate snapshot/session owner
  - no approval UI redesign in this unit
  - no suspended-turn/resume protocol; Bot-side dangerous calls remain fail-closed until such a protocol is separately approved
acceptance:
  - atomic commit rollback fault injection
  - crash before claim recovery and crash during send unknown reconciliation
  - passive and proactive delivery truth tests including text plus media failure
  - no-subscriber, duplicate-notify and restart replay negative tests
  - policy allow, deny, approval binding, stale snapshot and ledger tests
  - affected regression, pyright, compileall and migration append-only checks
```

#### VNX-06 gap matrix

| Contract | Current upstream fact | Severity | VNX-06 action |
|---|---|---:|---|
| Same-session order | `PassiveMessageWorker` uses one queue/task per session | satisfied | Retain and regression-test |
| Turn persistence/CAS | `SessionStore.turns` and `ConversationRuntime` own terminal transitions | satisfied | Retain; do not port old runtime turns |
| Snapshot binding | `RuntimeSnapshotStore` leases each active turn/generation | satisfied | Retain; policy records the bound snapshot |
| Outbound durability | Successful turn is committed before `asyncio.Queue` enqueue | P0 | Add atomic message/outbox commit |
| Delivery truth | callback exception triggers blind retry and fallback text | P0 | One attempt, typed `failed`/`unknown`, no fallback |
| Proactive consistency | external send precedes visible history commit | P0 | Commit proactive message/outbox before dispatch |
| Tool authorization | hooks execute around tools but no core policy/approval ledger exists | P0 | Add fail-closed policy, approval and call ledger |
| Cross-session concurrency | per-session lanes exist; global work is no longer strictly single-lane | satisfied | Retain and regression-test |
| Durable inbound | channel ingress remains process-memory before admission | P1 | Backlog; channel APIs do not provide a uniform replay cursor |
| Interest ranker feedback | current proactive pipeline has no complete online feedback learner | P1 | Backlog; not required for delivery/policy correctness |

#### VNX-06 bounded units

| Unit | Status | Scope | Stop condition |
|---|---|---|---|
| VNX-06A | DONE | Audit and contract | CR and evidence-backed gap matrix recorded |
| VNX-06B | DONE | Durable outbound | Passive/proactive atomicity and restart/reconciliation tests pass |
| VNX-06C | DONE | Tool policy | Authorization, approval binding and ledger tests pass |
| VNX-06D | DONE | Continuity Gate | Fault/replay plus memory/proactive/snapshot affected regression passes |

## VNX-07 · 正式切换与守护

- 可观察行为：Windows 启动后只有一个 Supervisor 和一个 Telegram poller；异常退出按合同重启。
- 预计允许路径：现有 Supervisor/启动脚本、正式 Git 外配置、运行账本。
- 非目标：不创建第二套守护器，不同时运行旧新 Bot poller。
- 验证：端口/PID/boot id、restart Gate、Dashboard/Web/Telegram smoke、旧进程不存在。
- 停止：candidate 连续运行且重启后仍能回复。

Candidate 的 Git 外插件控制面使用独立 `PluginHome`。正式 launcher 必须同时收到
config、auth store、workspace 和 plugin home；plugin manifest 不存在时直接失败，
不得回退到用户级共享 manifest。这样旧服务与 candidate 的插件开关不会在切换窗口
互相热更新。

### SDD-CR-VNX-07-001 · IntentView 瞬时传输故障恢复

```yaml
status: approved under the active root Goal
trigger:
  - two real V3 Gate runs observed four upstream Responses HTTP 500 failures
  - a later Gate also observed HTTP 408 when the upstream stream closed before completion
  - failures were RetryableTransportError, not auth, quota, timeout, schema or model mismatch
change:
  - keep one logical IntentView analysis per turn
  - permit at most two HTTP attempts to the same Terra model and endpoint
  - retry only RetryableTransportError and keep both attempts inside the existing total timeout
  - classify Responses HTTP 408 and 5xx as retryable transport failures without retrying inside the transport
  - log only the retry attempt number, never request content or credentials
  - after retry exhaustion, publish a current-snapshot unavailable advice with no preloads
  - let the passive path continue its existing ToolSearch discovery when advice is unavailable
non_goals:
  - no alternate model or provider
  - no retry for authentication, quota, rate limit, timeout, context, protocol or schema errors
  - no relaxation of V3 quality thresholds
  - no degradation across a RuntimeSnapshot mismatch
verification:
  - focused retry and no-retry classification tests
  - active unavailable advice validates against the current discovery snapshot
  - snapshot publication mismatch remains fail-closed
  - current-source real Terra plus Qwen V3 Gate
  - active startup digest verification
rollback: remove the bounded IntentView retry and regenerate the current-source Gate report
```

### SDD-CR-VNX-07-002 · 插件包清单首启迁移顺序

```yaml
status: implemented; focused verification passed; live cutover pending
trigger:
  - the isolated candidate PluginHome used a legacy member entry for default_proactive
  - plugin discovery filtered package members before sync_manifest migrated that entry
  - the first boot therefore loaded zero default proactive providers and only then rewrote the manifest
change:
  - reconcile legacy plugin/package manifest entries before plugin discovery and load
  - keep workspace MCP publication before plugin loading
  - keep skill synchronization and tool-hook binding after successful plugin loading
non_goals:
  - no implicit package activation beyond the existing sync_manifest rules
  - no second proactive provider and no retry loop inside plugin loading
verification:
  - startup-order regression requires workspace MCP -> manifest sync -> plugin load
  - plugin-package migration suite
  - candidate must load exactly one default proactive runtime provider on first boot
rollback: restore post-load manifest sync only after candidate PluginHome is written in canonical package form
```

### SDD-CR-VNX-07-003 · AnyAction 初始配额状态兼容迁移

```yaml
status: implemented; focused verification passed; live cutover pending
trigger:
  - the existing workspace contains a version-1 proactive quota file written as an all-empty unused state
  - strict current validation rejected its empty window_key before snapshot rollover could canonicalize it
change:
  - recognize only the exact pristine sentinel: version=1, used=0 and all window/time fields empty
  - retain it in memory until the first snapshot computes the configured timezone window
  - atomically persist the normal version-1 state through the existing rollover write path
non_goals:
  - no deletion or blind reset of the quota file
  - no acceptance of partial emptiness, non-zero usage, invalid timestamps or malformed JSON
verification:
  - exact pristine sentinel migrates to the expected window and reset timestamp
  - a similar state with non-zero usage remains rejected
  - AnyAction and support regression suite
rollback: require operators to migrate the pristine file out of band before restoring strict load-only validation
```

## VNX-08 · 最终对账

- 可观察行为：代码、测试、运行证据、SDD 和账本一致。
- 允许路径：受影响 SDD、plans index、必要报告摘要。
- 非目标：不做发布外的美化和新功能。
- 验证：全量 pytest、pyright、前端、secret scan、public/private Gate 状态、live smoke、完整 diff review。
- 停止：根 Goal Definition of done 全部满足。

## Current execution record

```yaml
unit_id: VNX-00
status: done
baseline:
  target: upstream/main@294ed10
  origin_main: 2577ccb
  legacy_runtime3_base: 2577ccb
recovery_sources:
  - E:\agent\author_akashic
  - E:\agent\author_akashic_runtime3
candidate:
  worktree: E:\agent\author_akashic_vnext
  branch: codex/vnext-20260723
files_changed:
  - docs/INDEX.md
  - docs/design/vnext-local-integration.md
  - plans/README.md
  - plans/002-vnext-local-integration.md
tests_run:
  - git worktree/ref/status audit: passed
  - local Markdown link check: 0 broken
  - credential pattern scan: 0 findings
  - git diff --check: passed
fallbacks_added: []
remaining_risks:
  - legacy work is uncommitted and bound to an older upstream API
  - private_runtime submodule is not initialized in candidate
  - live Terra and Bot credentials have not yet been probed
next_unit: VNX-01
```

## VNX-01 execution record

```yaml
unit_id: VNX-01
status: done
baseline:
  target: upstream/main@294ed10
tests_run:
  - production pyright: passed, 0 errors
  - tests pyright: passed, 0 errors
  - control schema check: passed
  - Python SDK pyright: passed
  - migration append-only: passed
  - full pytest: blocked during collection
  - frontend typecheck: failed
  - frontend build: failed
  - public change Gate audit: failed
gap_matrix:
  - upstream_replaces: supervisor, control, plugin generation, plugin MCP, base memory/proactive
  - rewrite: Terra Responses, GPT image plugin/media bridge, Intent Routing V3
  - selective_p0_port: ToolPolicy, approval ledger, durable delivery reconciliation
  - out_of_scope: distributed runtime, mobile expansion, remote publication
failures:
  - Windows fcntl import and locked owner-byte semantics
  - Windows drive path parsed as TCP endpoint
  - CRLF-sensitive Gate catalog digest
  - unlocked frontend dependency drift and direct .cmd spawn
  - private/mobile Gate unavailable in this dirty docs candidate and uninitialized private checkout
next_action: execute VNX-01A before any feature migration
```

## VNX-01A execution record

```yaml
unit_id: VNX-01A
status: done
write_set:
  - core/common/file_lock.py
  - bootstrap/workspace_lock.py
  - agent/migrations/runner.py
  - agent/model_runtime/auth/store.py
  - agent/plugins/install.py
  - agent/plugins/skill_links.py
  - agent/restart.py
  - agent/supervisor.py
  - bootstrap/runtime_readiness.py
  - scripts/rolling_backup.py
  - scripts/check_migrations_append_only.py
  - infra/control/socket.py
  - infra/persistence/json_store.py
  - docker/debug/gate.py
  - docker/debug/workspace_mcp_reload_probe.py
  - main.py
  - plugins/akasha/core.py
  - corresponding tests
scope_notes:
  - full baseline exposed Windows atomic replace, plugin rollback/symlink, Supervisor commit transport, process identity, TOML path and debug-probe portability defects
  - fixes stayed within runtime/platform primitives and their verification; provider, routing and product behavior were not added
credential_incident:
  finding: two default CredentialStore tests wrote test literals under the real Windows user profile
  recovery: both files contained only known test literals and were moved to timestamped test-pollution quarantine names; active auth paths are absent
  prevention: AKASHIC_AUTH_FILE plus an autouse per-test temporary credential path
tests_run:
  - full pytest: passed, 2244 passed / 186 skipped / 0 failed
  - production pyright --level error: passed, 0 errors
  - tests pyright --level error: passed, 0 errors
  - Change Gate audit: passed
  - migration append-only against upstream/main: passed
  - workspace MCP real AppRuntime reload probe: passed, 3 tests
  - git diff --check: passed
  - changed-diff credential pattern scan: 0 findings
  - real user auth active-path check: absent after tests
fallbacks_added: []
remaining_risks:
  - Windows auth/config protection still needs explicit ACL enforcement in VNX-03; POSIX chmod bits are not evidence on Windows
  - private/mobile Gate remains out of this single-machine Windows unit and the private submodule is uninitialized
next_action: execute VNX-01B deterministic frontend install and Windows build portability
```

## VNX-01B execution record

```yaml
unit_id: VNX-01B
status: done
write_set:
  - .gitignore
  - package.json
  - package-lock.json
  - scripts/build-plugin-preset.mjs
  - scripts/build-plugins.mjs
  - frontend/dashboard/public/sdk/preset.css
  - _handbook/plugins-tutorial.md
  - tests_scenarios/contracts/impact.toml
  - tests_scenarios/contracts/coverage-baseline.json
dependency_contract:
  recharts: 3.8.1
  redux_toolkit_override: 2.10.1
  immer_override: 10.2.0
build_contract:
  - Tailwind and esbuild execute their installed JavaScript CLIs through process.execPath
  - missing local CLI fails explicitly; there is no npx/network fallback
  - repository sources are the default preset input; installed plugin sources require explicit AKASHIC_PLUGIN_HOME
change_request:
  id: SDD-CR-VNX-01B-001
  decision: approved under the active root Goal
  reason: package-lock.json became an executable-name input but lacked an owner mapping
  change: map only package-lock.json to the existing tooling group and refresh the catalog digest
  semantic_effect: no new scenario, no accepted gap, no priority reduction
tests_run:
  - npm ci: passed, 614 packages from lockfile
  - npm run typecheck: passed
  - npm run build: passed for dashboard, chat and four builtin plugin panels
  - preset second-build SHA-256 equality: passed
  - npm dependency tree: exact versions match contract
  - Change Gate audit after lockfile mapping: passed
  - Change Gate semantic tests: passed, 8 tests
  - git diff --check: passed
fallbacks_added: []
remaining_risks:
  - Vite reports existing chunks above 500 kB; this is a performance backlog, not a build failure
next_action: execute VNX-02 Terra Responses transport and provider profile
```

## VNX-02 execution record

```yaml
unit_id: VNX-02
status: done
write_set:
  - agent/model_runtime/provider_profiles.py
  - agent/model_runtime/transports/responses.py
  - agent/provider.py
  - agent/config_models.py
  - bootstrap/proactive.py
  - config.example.toml
  - tests/test_terra_responses_runtime.py
  - docs/design/vnext-local-integration.md
  - plans/002-vnext-local-integration.md
transport_contract:
  provider: terra-responses
  model: gpt-5.6-terra
  protocol: OpenAI-compatible Responses
  request_mode: non-streaming and stateless
  endpoint: configured API root plus /responses
  reasoning_effort: high
  continuation: opaque reasoning plus function call and function result replay
  chat_fallback: forbidden
security_contract:
  - credential must resolve before client construction
  - URL userinfo, query, fragment and method suffixes are rejected
  - non-loopback plaintext HTTP is rejected
  - response model must exactly match the requested model
  - arbitrary request extra_body fields are rejected
tests_run:
  - Terra/model-runtime/proactive targeted tests: passed, 79 tests
  - provider/bootstrap/settings affected tests: passed, 68 tests
  - real loopback HTTP capture: passed, exact /v1/responses and no Chat access
  - full pytest after Gate reconciliation: passed, 2268 passed / 186 skipped / 0 failed
  - production pyright --level error: passed, 0 errors / 0 warnings
  - tests pyright --level error: passed, 0 errors / 0 warnings
  - Change Gate audit: passed
  - changed-tree credential pattern scan: 0 findings
  - git diff --check: passed
fallbacks_added: []
remaining_risks:
  - the real reverse proxy endpoint, credential and model response are not proven until VNX-03 live health
  - Windows CredentialStore ACL enforcement remains VNX-03 work
  - this unit does not provide image generation or media delivery
next_action: execute VNX-03 secret-safe Bot, Terra and local embedding health verification
```

## VNX-03 execution record

```yaml
unit_id: VNX-03
status: done
write_set:
  - core/common/private_path.py
  - agent/model_runtime/auth/store.py
  - agent/config.py
  - config.example.toml
  - tests/test_private_path.py
  - tests/test_channel_credentials.py
  - Git-external candidate config and CredentialStore
  - docs/design/vnext-local-integration.md
  - plans/002-vnext-local-integration.md
credential_contract:
  - Telegram auth resolves exactly one driver=telegram_bot entry
  - auth and inline token are mutually exclusive
  - missing credentials and broad owner/DACL fail closed
  - private directory, primary, backup and lock file are all hardened and read back
candidate_shape:
  model_runtime_count: 1
  provider: terra-responses
  model: gpt-5.6-terra
  reasoning_effort: high
  bot: "@akashic_qsyy0921_bot"
  embedding_model: qwen3-embedding:4b
  embedding_dimensions: 1024
  inline_secret_fields: 0
live_health:
  - Terra candidate provider returned the exact random nonce with usage through Responses
  - Telegram getMe returned the required username and bot identity
  - local embedding returned a finite nonzero 1024-dimensional vector
  - CLIProxy remained healthy after its config ACL was hardened
data_compatibility:
  - existing memory2 vec_items schema is float[1024]
  - no production memory row or vector was rewritten
tests_run:
  - credential/channel/model targeted regression: passed, 61 tests
  - fresh-init failure correction regression: passed, 60 tests
  - production pyright --level error: passed, 0 errors
  - tests pyright --level error: passed, 0 errors
  - candidate config sensitive-key scan: 0 literal secret fields
  - git diff --check: passed
  - full pytest after correction: 2273 passed / 186 skipped / 1 unrelated timing failure
  - isolated plugin fast-exit timing test: passed 11 consecutive runs
fallbacks_added: []
remaining_risks:
  - final Telegram user-message round trip waits for single-poller cutover
  - proactive remains disabled until its Runtime 3.0 policy and target are verified
  - no-readiness plugin service uses a 200 ms stability window and showed one loaded-suite timing miss; audit in VNX-04/VNX-06
next_action: execute VNX-04 GPT image plugin MCP and constrained media delivery
```

## VNX-04 execution record

```yaml
unit_id: VNX-04
status: done
write_set:
  - agent/mcp/client.py
  - agent/mcp/host.py
  - agent/mcp/tool.py
  - agent/plugins/specs.py
  - agent/plugins/manager.py
  - agent/tools/base.py
  - agent/tools/registry.py
  - agent/tool_hooks.py
  - agent/core/passive_turn.py
  - infra/channels/telegram_channel.py
  - plugins/terra_imagegen/**
  - docker/debug/Dockerfile
  - main.py
  - corresponding tests and SDD
media_contract:
  - only declared workspace-relative output roots are trusted
  - Core verifies canonical path, regular-file identity, no symlink, size, SHA-256 and PNG/JPEG/WebP signature
  - up to 4 files and 10 MiB per file enter ToolResult.media
  - registry context is never forwarded across the MCP schema boundary
authorization_contract:
  - only an explicit passive user request may invoke gpt_image.generate_image
  - preflight does not consume the turn budget
  - one actual generation call per turn; n controls the requested fixed count
  - proactive and subagent generation are rejected
  - provider polling, automatic retries and alternate image providers are absent
live_probe:
  - candidate plugin generation and real MCP subprocess handshake passed
  - one explicitly authorized Terra Responses request produced one validated PNG
  - validated file size was 1707471 bytes and the temporary probe workspace was removed
  - this proves provider, MCP and local media validation; Telegram receipt remains VNX-07 E2E
tests_run:
  - focused compatibility and media regression: passed, 15 tests
  - plugin manager ownership regression: passed, 62 tests
  - affected MCP/plugin/passive/channel/runtime regression: passed, 459 tests
  - production pyright --level error: passed, 0 errors / 0 warnings
  - tests pyright --level error: passed, 0 errors / 0 warnings
  - strict SDD validator: passed, 0 warnings
  - Change Gate audit: passed
  - Change Gate run: passed, 7/7 public scenarios and image build
  - Change Gate residual resources: 0 containers / 0 networks / 0 volumes
fallbacks_added: []
remaining_risks:
  - Telegram image receipt is not claimed until the single-poller cutover
  - generic durable multipart delivery and approval ledger remain owned by VNX-06
  - plugin-doctor currently inspects installed manifests and does not enumerate this builtin plugin; runtime discovery and handshake are verified
next_action: execute VNX-05 intent routing V3 baseline, bounded implementation and evaluation
```

## VNX-05 execution record

```yaml
unit_id: VNX-05
status: done
write_set:
  - agent/routing/**
  - agent/tools/registry.py
  - agent/plugins/manager.py
  - agent/config.py
  - agent/config_models.py
  - agent/core/passive_turn.py
  - agent/looping/core.py
  - agent/looping/ports.py
  - bootstrap/tools.py
  - config.example.toml
  - eval/intent_routing/**
  - tests/test_intent_routing_*.py
  - tests/test_tool_search.py
  - tests_scenarios/contracts/impact.toml
  - Git-external candidate config
  - corresponding SDD and ledger
runtime_contract:
  modes: [off, shadow, active]
  intent_view_calls_per_turn: 1 logical call
  intent_view_transport_attempts_max: 2
  intent_view_retry_class: RetryableTransportError only
  intent_model: gpt-5.6-terra
  retrieval_channels: [original_lexical, original_dense, llm_intent_view]
  embedding_model: qwen3-embedding:4b
  embedding_dimensions: 1024
  context_messages_max: 8
  context_characters_max: 6000
  goals_max: 4
  candidates_per_goal_max: 3
  preloads_max: 5
  candidate_is_authorization: false
quality_gate:
  report: eval/intent_routing/v3-gate-report.json
  cases: 25
  goals: 27
  tools: 13
  operation_recall_at_1: 1.0
  operation_recall_at_3: 1.0
  complete_operation_set_recall: 1.0
  complete_operation_set_exact_match: 0.904762
  no_tool_precision: 1.0
  no_tool_recall: 1.0
  context_resolution_accuracy: 1.0
  required_clarification_recall: 1.0
  unnecessary_clarification_rate: 0.0
  end_to_end_visibility_rate: 1.0
  intent_view_success_rate: 1.0
  status_accuracy: 1.0
  cold_start_latency_ms: 7262.242
  local_warm_latency_p95_ms: 1598.619
  external_llm_latency_p95_ms: 12492.811
tests_run:
  - routing contracts, context, fusion, Gate and integration: passed, 56 tests
  - affected reasoner/tool/plugin/config/runtime regression: passed, 326 tests / 2 skipped
  - production pyright --level error: passed, 0 errors / 0 warnings
  - tests pyright --level error: passed, 0 errors / 0 warnings
  - compileall: passed
  - real Qwen probe: passed, two finite normalized 1024-dimensional vectors
  - real Terra plus Qwen quality Gate: passed, no failures or misses
  - exact active startup Gate and advisor construction: passed
  - RuntimeSnapshot publication mismatch negative test: passed
  - routed visibility cannot grant requires_turn_search authority: passed
fallbacks_added: []
remaining_risks:
  - complete set exact match is below 1.0 because valid ambiguous/equivalent alternatives can add a top candidate; complete recall and per-goal Top-1 remain 1.0
  - final user-visible routing behavior waits for the single-poller Telegram cutover in VNX-07
  - routing latency includes one remote Terra call and is unsuitable for a silent keyword-only fallback
next_action: execute VNX-06 Runtime 3.0 P0 gap audit and bounded fixes
```

## VNX-06 execution record

```yaml
unit_id: VNX-06
status: done
completed_subunits: [VNX-06A, VNX-06B, VNX-06C, VNX-06D]
implemented:
  - append-only reliability schema v1 for outbox/delivery attempts
  - append-only reliability schema v2 for tool calls, approvals and audit events
  - passive message plus outbound intent atomic commit
  - proactive message plus outbound intent commit before channel callback
  - one channel callback attempt with failed versus unknown truth
  - startup recovery and explicit failed/unknown reconciliation
  - production ephemeral outbound worker removed and queue disabled fail-closed
  - final-argument ToolPolicy after plugin pre-hooks and before real invoker
  - exact approval binding to turn, snapshot, tool and canonical arguments
  - single-use approval consumption, replay denial and external ambiguity ledger
  - bounded redacted argument summaries and result digests without raw secrets
  - shared governance wiring for passive, proactive, Drift and subagent calls
  - plugin MCP snapshot media capability reads the normalized server mapping
evidence:
  - session/reliability_schema.py
  - session/outbox_repository.py
  - session/tool_ledger_repository.py
  - agent/delivery/supervisor.py
  - agent/tool_governance.py
  - agent/tool_hooks/executor.py
  - agent/lifecycle/phases/after_reasoning.py
  - agent/turns/orchestrator.py
  - bootstrap/app.py
tests_run:
  - runtime3 tool governance: passed, 13 tests
  - runtime3 delivery plus runtime smoke: passed, 49 tests
  - proactive_v2 suite: passed, 378 tests
  - passive lifecycle, turn, runtime and migration group: passed, 150 tests
  - bootstrap, subagent and proactive facade group: passed, 97 tests
  - tool executor, shell hook and plugin manager group: passed, 71 tests
  - support modules: passed, 46 tests
  - memory, control, plugin MCP and snapshot continuity group: passed, 333 tests / 6 skipped
  - intent routing offline contracts and integration: passed, 56 tests
  - changed production pyright --level error: passed, 0 errors / 0 warnings
  - repository compileall: passed
  - migration append-only check against upstream/main: passed
  - git diff --check: passed, line-ending warnings only
fallbacks_added: []
remaining_work_owned_by_later_units:
  - rerun the real VNX-05 routing Gate in VNX-08 because passive source digest changed
  - perform candidate production startup and Telegram cutover in VNX-07
explicit_limit:
  - approval persistence and operator service methods exist
  - a user-facing suspended-turn approval/resume protocol does not exist in this unit
  - therefore approval-required Bot calls stop before execution instead of pretending success
next_action: execute VNX-07 single-supervisor cutover and live smoke
```

## VNX-07 execution record

```yaml
unit_id: VNX-07
status: blocked
cutover:
  scheduled_task: Akashic Agent Runtime3
  checkout: E:\agent\author_akashic_vnext
  config_source: Git-external CredentialStore references
  candidate_launchers: 1
  legacy_launchers: 0
  duplicate_pollers_observed: 0
  dashboard: http://127.0.0.1:2236
  web_chat: http://127.0.0.1:6322
  app_server: 127.0.0.1:37071
runtime_health:
  - Dashboard, Web Chat and settings HTTP probes returned 200
  - readiness PID matched the process owning Dashboard, Web Chat and app-server
  - Telegram getMe matched @akashic_qsyy0921_bot
  - six expected plugins loaded and plugin doctor reported all six healthy
  - default proactive lifecycle started and repeated no-content ticks completed normally
  - sessions.db integrity_check returned ok with pre-cutover message history preserved
web_chat_e2e:
  text_nonce: VNX-WEB-E2E-20260723-2112
  text_result:
    - WebSocket emitted turn.started, answer.delta and message.final
    - Terra response echoed the exact nonce
    - sessions.db persisted one user row and one assistant row
    - durable outbox reached sent with exactly one sent delivery attempt
  image_nonce: VNX-WEB-IMAGE-20260723-2115
  image_result:
    - one explicit request produced exactly one image tool call and one media item
    - the media file passed regular-file, non-empty and PNG/JPEG/WebP signature checks
    - the tool ledger recorded external-side-effect / allow / succeeded
    - durable outbox and delivery attempts each increased once with status sent
  scope_limit: proves passive/runtime/tool/MCP/media/outbox through Web Chat, not Telegram transport
restart_probe:
  method: terminate only the candidate gateway child and observe Scheduled Task recovery
  result: passed
  evidence:
    - new process identity and boot id appeared within the configured restart cadence
    - one candidate launcher and zero legacy launchers remained after recovery
    - Dashboard, Web Chat and app-server returned to listening state
live_telegram_gate:
  nonce: VNX-E2E-20260723-2006
  database_matches: 0
  operator_probe: Telegram returned chat_not_found before a private conversation was started
  required_user_action: open @akashic_qsyy0921_bot, press Start or send /start, then send the nonce
  claims_withheld:
    - passive Telegram reply receipt
    - GPT image Telegram receipt
changes_during_cutover:
  - plugin package manifest is synchronized before first plugin discovery/load
  - exact pristine legacy AnyAction quota sentinel migrates on first snapshot
  - plugin doctor expands built-in package members and package state overrides stale member entries
  - Windows ACL subprocess output is discarded to avoid locale-dependent decode failures
tests_run:
  - plugin package startup/quota/doctor/private-path focused regression: passed, 84 tests
  - PowerShell launcher parser: passed
  - controlled crash/restart smoke: passed
fallbacks_added: []
remaining_risks:
  - a Telegram Bot cannot initiate a private conversation; Telegram E2E waits for user /start
  - final image request must be user-triggered to prove paid-tool authorization and Telegram photo receipt
next_action: complete the user-triggered text and image Telegram round trips
```

## VNX-08 execution record

```yaml
unit_id: VNX-08
status: blocked
verification_completed:
  - final full pytest: passed, 2377 passed / 186 skipped
  - production pyright --level error: passed, 0 errors / 0 warnings
  - tests pyright --level error: passed, 0 errors / 0 warnings
  - compileall: passed
  - frontend npm run ci:frontend: passed
  - migration append-only against upstream/main: passed
  - Change Gate audit: passed
  - public Change Gate: passed, 7/7 selected scenarios
  - public Gate Docker residual resources: none
  - high-confidence token/private-key scan: 0 findings
  - production credential-URL scan: 0 findings; one deliberate rejection fixture under tests
  - git diff --check: passed, line-ending warnings only
private_gate:
  status: pending_maintainer
  required_by_public_plan: true
  pinned_revision: 83f3648424864f690ae5c3636b76d3436902cecd
  local_submodule_objects: absent
  access_probe: current SSH and GitHub CLI identity cannot resolve the private repository
  rejected_substitute: an older unversioned local private_runtime directory is not revision evidence
remaining_verification:
  - user-triggered Telegram text and image receipts
  - private maintainer Gate or repository access supplied by the owner
next_action: wait for Telegram user action and private repository access, then run only the remaining receipts and Gate
```

## External blocker audit

```yaml
status: blocked
consecutive_goal_turns: 3
telegram:
  required_evidence:
    - inbound user message for nonce VNX-E2E-20260723-2006
    - matching assistant reply and one sent delivery attempt
    - one explicit image request with one Telegram media receipt
  current_evidence:
    - nonce matches in sessions.db: 0
    - Bot API identity, poller startup and single-instance runtime remain healthy
  unblock:
    - user opens @akashic_qsyy0921_bot and sends /start
    - user sends VNX-E2E-20260723-2006
private_gate:
  required_evidence:
    - private maintainer contract Gate at the pinned submodule revision
  current_evidence:
    - local submodule Git objects are absent
    - SSH BatchMode access returns permission denied
  unblock:
    - repository owner grants the current GitHub identity read access
    - or owner provides the correct accessible repository URL at the pinned revision
preserved_state:
  - candidate service remains online under Akashic Agent Runtime3
  - Git worktree, SDD, external credentials and verification reports are retained
  - no commit, push or pull request was created
resume_action: rerun Telegram text/image E2E and private Gate without repeating completed units
```
