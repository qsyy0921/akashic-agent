# NOW

这份文件只保存 Akashic Agent 当前仍未完成的工作。事项完成后删除，不保留“已完成”记录。

## P0 · 上下文与持久历史隔离

- 从 `DefaultReasoner` 移除 `SessionManager` 依赖；动态区块退化不改 session，history window 退化只改运行时视图，不写 store。
- 清理 `trim_history_async` 等含糊 API；需要独立 cache owner 时再引入运行时视图类型，不新增与 `PromptRenderInput` 同义的抽象。

## P0 · 独立语义验收

- 将 CTX-001 当前的 trace、完整状态快照和 fixture `DELETE` pilot 升级为 SQLite authorizer 与一次性候选真实 retry seam mutant；导入失败、fixture 失败或超时不得计为 mutant kill。
- 建立受保护路径 policy：`semantic_delta: none` 的普通实现改动不能同时修改 P0 oracle、mutant 或 coverage baseline 来获得全绿。
- 建立轻量 `change-intent` 校验，检查实际 diff、允许路径、受保护状态和副作用是否超出声明。

## P0 · 私有跨仓库 Gate

- 干净 core revision `00c1355` 已在受保护契约基线 `efd7e66` 上通过公共 Gate 7/7；正式 20-provider G2 为 19/20，且没有残留容器、网络或卷。
- 唯一失败是 DayNight 官方 revision `de7b202` 的测试仍把配置写入旧 `.akashic-plugin/data`。个人 Fork `qsyy0921/daynight_gate@4fd6643` 的单行测试修复已通过 3 项测试，但正式 Gate 的 owner trust 只接受 `akashic-plugins`，不能直接替换成个人仓库，也不能在 core 增加旧目录 fallback。
- DayNight 官方 PR `akashic-plugins/daynight_gate#1` 已提交且无冲突；维护者合入后冻结新的官方 revision 并重跑正式 G2。全绿后更新最终组合证据。外部状态只允许 `passed`、`failed` 或 `not_affected`，不受信任 PR 不接触 provider 清单、凭据或私有报告。

## P1 · 工作流扩展

- 把 `projectneed.md` 中其他 P0 不变量逐步迁入可执行契约，优先处理 MEM-001、MEM-002、OUT-001、PLG-001、PLG-004、WSP-001 和 BAK-001。
- 为高风险 refactor 增加 base/candidate 差分回放，核对持久 write set、事件、外部调用和错误分类。
- 由维护者继续确认 [`design/persistence-state-map.md`](design/persistence-state-map.md) 的 INT-009、INT-010、INT-012～INT-014，以及旧消息编辑和 turns retention；INT-001～INT-008、INT-011 已提升为 projectneed 条款。
- 收紧 Akasha 完整重建：固定读取 `sessions.db/messages` 与已有 `message_embeddings`，不调用 LLM；任一 embedding miss 或模型不匹配时 fail-loud，并用同输入 parity 证明图可复现。
- 把 `mcp/servers/*.toml` 直装声明和 workspace 手工 skill 目录迁移成插件贡献；迁移现存能力后收窄 `WorkspaceMcpAdmin`、watcher 和 loader，Skill/MCP 只保留插件安装、readiness 与 generation 发布这一个 owner。
- 把已确认的持久化状态地图转成机器可读备份 manifest，补齐目录快照、global companion state 和隔离恢复演练；确认 snapshot 能启动只读 runtime，并读取会话、记忆、调度、插件数据和主动流程连续性。
