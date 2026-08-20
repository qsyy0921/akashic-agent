---
unit: plugin-runtime-api-v2-integration
status: proposed
depends_on: PLG-003, PLG-004, PLG-009, agent-closed-loop-v1
---

# Plugin Runtime API v2 Integration

## Scope

本单元把公共核心、内置插件和私有 Provider 统一到唯一的 Plugin Runtime API v2，
使当前 20-provider 私有 Gate 能在同一生命周期合同下执行。迁移依据是上游提交
`b8a6da37` 的 v2 原子发布合同；不合并与该合同无关的后续上游功能。

## Responsibilities and non-goals

本单元负责 `prepare -> activate -> retire -> terminate` 生命周期、候选准备阶段的任务和
持久写隔离、generation 退役、原子快照发布、恢复账本，以及内置插件和测试夹具的 v2
声明。

本单元不支持 v1/v2 双协议，不保留 `initialize` 兼容入口，不修改业务工具行为，不引入
新的 Provider、模型或存储，也不自动安装或执行未经冻结的私有 Provider revision。

## Contracts and dependencies

- `upstream/main@90cf9f96`
- `upstream` 的 Plugin Runtime API v2 发布提交 `b8a6da37`
- `agent/plugins/base.py`
- `agent/plugins/manager.py`
- `agent/plugins/context.py`
- `agent/plugins/generation.py`
- `E:/agent/akashic-private-contract-gate/cross_repo_contracts.toml`

## Invariants

1. Core 只接受 `api_version == 2`；v1 插件必须显式迁移，否则 fail-loud。
2. `prepare` 是异步候选准备阶段，不得启动后台任务，不得直接发布 KV 写入。
3. `activate` 是同步提交边界；只有通过 Gate 的候选才能进入 active snapshot。
4. snapshot 关闭 admission 后必须调用一次 `retire`；旧 lease 排空后才能 `terminate`。
5. 已退役 generation 除其受控 cleanup task 外不得继续写插件 KV。
6. 准备、激活、发布或恢复失败必须保留上一 active snapshot，不得静默降级。
7. Plugin API 迁移不得改变 ToolPolicy、MCP 媒体校验、可靠投递或 M0-M7 所有权。

## Runtime flow

1. Core 发现插件并完成无副作用的静态贡献收集和 v2 Gate。
2. Core 创建隔离 generation，运行异步 `prepare`，候选 KV 写只进入暂存视图。
3. Readiness 和 snapshot invariant 通过后执行同步 `activate` 并原子提交 snapshot/KV。
4. 旧 snapshot 关闭 admission 后调用 `retire`；现有 lease 继续读取被冻结的旧能力。
5. lease 排空后运行异步 `terminate`、作用域清理并完成 reload journal。
6. 任一步失败都中止候选并保留上一 active snapshot。

## Data ownership and state

PluginManager 拥有 generation 状态、snapshot publication 和 reload journal；PluginScope
拥有后台任务、进程和 cleanup；插件只拥有其 workspace `plugin-data/<plugin-id>`。候选
KV 由 `PreparedPluginKVStore` 暂存，只有 activate 成功后提交。Reload journal 保存原子
发布的阶段和精确 source revision，不保存业务数据。

允许写集：

- `agent/plugins/{base,context,generation,manager,reload_journal,scope,snapshot,watcher}.py`
- `bootstrap/app.py`
- `plugins/*/plugin.py` 中的生命周期声明
- `docker/debug/` 的 Plugin API v2 Gate 合同
- 直接覆盖该合同的插件、热重载、恢复和 MCP 声明测试
- 本 SDD、`docs/INDEX.md` 和完成后的 `docs/NOW.md`

`agent/core`、`agent/planning`、`agent/reliability`、记忆发布和业务 MCP 实现不是本单元的
重构目标；只有失败测试证明接口适配必要时才能做最小改动。

## Failure handling

候选验证失败返回明确 Gate check；启动恢复找不到精确 source revision 时拒绝启动；
准备态 KV 不提交；激活失败保留旧 snapshot；退役清理错误进入 cleanup failures。
回滚点是本地提交 `4f482f1c`，生产服务在全部 Gate 通过前继续运行 `qsyy0921` 旧进程。

## Security

生命周期状态和 reload journal 只记录插件身份、revision、snapshot、phase 与限长错误，
不得记录凭据或工具参数。私有 Gate 必须绑定 core source digest、provider commit、镜像
digest 和 profile digest，并在容器内离线执行。Provider 代码不得在宿主机执行，凭据不
进入日志、报告、Git 或测试 fixture。

## Observability

Gate check 暴露 `api_version`、`lifecycle_api`、prepare/readiness/publish 阶段和错误；
reload journal 暴露 generation、source revision、snapshot 和 phase。公开/私有 Gate 报告
写入机械盘报告根，并记录镜像与源摘要、每个 Provider 终态和资源清理结果。

## Acceptance criteria

1. v1、`initialize` 覆盖和非法同步/异步生命周期都被静态 Gate 拒绝。
2. prepare 不能启动任务，KV 写只在 activate 成功后一次提交。
3. activate/发布失败保留旧 snapshot；retire 后旧 generation 无法继续写。
4. 内置插件、热重载、重启恢复和 M0-M7 回归全部通过。
5. Ruff、Pyright、严格 SDD 校验和完整 pytest 通过。
6. 公开 Docker Gate 通过且无残留资源。
7. 私有 20-provider Gate 全部通过且无残留资源。
8. 不存在 v1/v2 compatibility shim、旧 Provider pin 或 catch-and-continue fallback。

## Source evidence

- `agent/plugins/base.py:Plugin`
- `agent/plugins/context.py:PluginContext,PreparedPluginKVStore`
- `agent/plugins/generation.py:PluginGeneration`
- `agent/plugins/manager.py:PluginManager._load_one,publish_prepared,terminate_all`
- `agent/plugins/snapshot.py:RuntimeSnapshotStore`
- `agent/plugins/reload_journal.py`
- `tests/test_plugin_api_v2_gate.py`
- `tests/test_plugin_hot_reload.py`
- `tests/test_plugin_reload_journal.py`
- `docker/debug/plugin_api_v2_gate.py`

## Open questions

无。若当前 Provider 依赖上游 v2 之后的其他公共合同，必须以新的失败证据建立独立
切片，不得继续扩大本单元。
