---
unit: core-memory-publication-gate
status: verified
depends_on: MEM-001, MEM-003, core-failure-recovery-policy, core-event-envelope-async-task
---

# Memory Publication Gate

## 1. 目标与范围

本单元把现有 `consolidation -> PENDING.md -> MemoryOptimizer -> MEMORY.md` 明确为候选发布协议。
PENDING 仍是人类可读候选缓冲，MEMORY 仍是已发布长期档案，memory2 继续消费 consolidation
事件保存可检索细节；不新建第二套记忆引擎。

## 2. 职责与非目标

发布 Gate 负责候选格式、去重、隐私、确定性冲突检查、发布事务和无明文审计。Optimizer
仍负责 LLM 合并和 SELF 更新，MarkdownMemoryStore 仍拥有文件原子写和重启恢复。

不在本单元实现开放域事实核验、医疗推断、自动解决语义冲突、向量库迁移、跨进程锁、在线
训练或人工审核 UI。不能把 PENDING、MEMORY 和 memory2 合并成同一状态层。

## 3. 契约与依赖

- 标准候选为单行 `- [tag] content`；兼容旧的 `- content`，但新 consolidation 必须折叠换行。
- Gate 输出 accepted、rejected 和稳定 reason code；只有 accepted 文本进入 LLM prompt。
- 凭据、token、password、private key 等 secret-bearing 候选必须拒绝，且审计账本不得保存明文。
- 当前 MEMORY 已有的完全重复项不再发布。
- `key: value` 类型候选若与同 key 当前值冲突，只有 `correction` tag 可以覆盖；否则拒绝为
  `conflict_requires_correction`。
- 模型生成的完整 MEMORY 再经过结构和 secret 扫描，失败时不得覆盖旧 MEMORY。

## 4. 设计不变量

1. rejected 候选不会进入模型 prompt 或 MEMORY。
2. ledger/manifest 不保存候选、MEMORY、prompt、凭据或异常正文。
3. 只有结构校验、隐私校验、原子写和 hash 确认全部成功才可提交 snapshot。
4. 任何无法由持久证据确认的 crash 状态都不能解释成发布成功。
5. MEMORY 发布与 SELF 更新是两个显式阶段；后者失败不得篡改前者的事实状态。

## 5. 运行时流程

```text
PENDING.md --rename--> PENDING.snapshot.md
                          |
                          v
             snapshot.state.json: prepared
                          |
                    deterministic gate
                          |
            +-------------+-------------+
            |                           |
        no accepted                 accepted only
            |                           |
         audit reject                LLM merge
            |                           |
          commit             state: publishing + output hash
                                        |
                                 atomic MEMORY write
                                        |
                               state: published -> commit
```

若进程在 MEMORY 写入后崩溃，启动恢复比较 manifest 的预期 hash 与当前 MEMORY hash；一致则
完成 snapshot commit，不一致则把 snapshot 回滚到 PENDING。损坏 manifest 必须 fail-loud。

## 6. 数据所有权与状态

`PENDING.md`/snapshot 属于候选层，`MEMORY.md` 属于发布层，`publication-ledger.jsonl` 只保存
transaction id、candidate hash、tag、reason、时间和最终 output hash。ledger 与 manifest 均不
保存候选正文、模型 prompt、MEMORY 全文或异常消息。

## 7. 失败处理

- Gate 拒绝是 resolved rejection，不调用 LLM，不写 MEMORY。
- LLM、输出校验、备份或 MEMORY 写失败时回滚 snapshot 并传播原异常。
- manifest/ledger 写失败不得继续发布。
- SELF 更新发生在 MEMORY 发布提交后；SELF 失败不伪装成 MEMORY 回滚，两者分别报告。
- crash recovery 只能根据持久 hash 证明发布完成，不能猜测成功。

## 8. 安全

Gate 在 LLM 前后各扫描一次 secret；`key_info` 只允许非敏感账号标识。候选和输出不会写入
审计账本，日志只记录数量、hash 和 reason code。发布 Gate 不改变工具权限，也不允许插件
绕过 MarkdownMemoryStore 直接写 MEMORY。

## 9. 可观测性

每次带 snapshot 的运行都有 transaction id、pending digest、accepted/rejected candidate digest、
稳定 rejection reason 和 published memory digest。manifest 显示 prepared/publishing/published/
committed 状态；这些字段足以定位首个失败阶段，但不构成可恢复的任务队列。

## 10. 验收标准

1. 合格候选才进入 LLM，隐私、重复、无效和未声明冲突均有稳定 rejection reason。
2. correction 可覆盖同 key 旧值；普通冲突不得发布。
3. 模型输出含 secret、格式无效或写入失败时，旧 MEMORY 保持且 snapshot 回滚。
4. MEMORY 已写但进程未 commit 的模拟状态在重启后根据 hash 完成提交；hash 不一致则回滚。
5. 审计账本不包含候选、secret、prompt 或 MEMORY 正文。
6. 现有 consolidation、optimizer、memory store、Dashboard 手动触发和 memory2 回归通过。
7. Ruff、Pyright、严格 SDD 与无 silent fallback 审计通过。

## 11. 源码依据

- `core/memory/markdown.py:_format_pending_items,MarkdownMemoryMaintenance`
- `agent/memory.py:MemoryStore.snapshot_pending,commit_pending_snapshot,rollback_pending_snapshot`
- `proactive_v2/memory_optimizer.py:MemoryOptimizer`
- `memory2/post_response_worker.py`
- `tests/test_memory_optimizer.py`
- `tests/test_memory_store_questions.py`
- `_handbook/memory-markdown.md`

## 12. 未决问题

开放域同义冲突、事实时效和人工确认队列需要独立评测数据与 UI owner；本单元只对可证明的
结构化冲突 fail-closed。候选被拒绝后原始对话仍在 session/HISTORY 证据链中，但不会把原文
复制到审计账本。
