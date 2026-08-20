---
unit: eval-offline-evolution-v1
status: verified
depends_on: eval-trajectory-v1, core-failure-recovery-policy, core-tool-graph-v1
---

# Offline Evolution Candidates v1

## 1. 目标与范围

本单元把冻结 trajectory aggregate 中的稳定失败信号转换为离线、只读、可审查的改进候选。
候选只指出 failure reason、受控 target surface、证据计数和必须重跑的 Gate；不生成补丁、不改
prompt/config/权重、不触发热重载或生产 promotion。

## 2. 职责与非目标

负责 trajectory report 校验、failure-to-target 白名单、证据时效、安全/Holdout Gate、确定性
candidate id、GO/NO-GO verdict 和 JSON CLI。不负责自动实验、模型训练、在线探索、bandit/RL、
代码写入、Git 提交、部署或在数据不足时猜测优化方向。

## 3. 契约与依赖

- 输入必须是 M6 schema v1 report，带 source SHA-256、attempt count 和 first-error histogram。
- 只识别显式 failure reason 白名单；未知 reason 触发 boundary NO-GO。
- 每个 reason 只映射一个受控 target surface 和 candidate kind。
- evidence_collected_at 与 as_of 都必须是 UTC-aware，且证据年龄不超过 policy 上限。
- safety/holdout case 数达到下限，violations/regressions 都为零，才允许进入人工审查。
- 输出 verdict 只有 `GO_FOR_HUMAN_REVIEW` 或 `NO_GO`；GO 也不表示可自动应用。

## 4. 设计不变量

1. 线上证据最多生成离线候选，永不直接改变 runtime。
2. candidate id 由 report digest、reason 和 target 确定，重复运行得到同一 identity。
3. boundary、retention、safety、holdout、sample 任一 Gate 失败即 NO_GO。
4. 报告中的 evaluations、消息、参数、结果和 thinking 不复制到候选包。
5. 未映射 failure 不得落入 generic fallback candidate。
6. 候选过期后必须用新冻结证据重新生成，不能延长旧 verdict。

## 5. 运行时流程

```text
M6 frozen report + evidence time + safety/holdout counts
                       |
                       v
             strict schema and digest checks
                       |
          +------------+------------+
          | boundary | retention | safety |
          +------------+------------+
                       |
                any failed -> NO_GO
                       |
                    all pass
                       v
          reason whitelist -> offline candidates
                       |
                       v
              GO_FOR_HUMAN_REVIEW
```

## 6. 数据所有权与状态

M6 report 是证据 owner；candidate bundle 是可再生 D 类评测产物，包含 source digest、Gate
结果、到期时间、计数和受控 target。bundle 不拥有源码、配置或生产状态，默认写入机械盘
报告 Junction；删除 bundle 不影响 runtime。

## 7. 失败处理

损坏 schema、计数矛盾、naive/future 时间、非法 policy 直接 fail-loud。有效输入但样本不足、
证据过期、安全违规、Holdout 回退、未知 failure 或无可行动信号返回结构化 NO_GO，不抛出后
伪造候选。空分母不解释成通过。

## 8. 安全

目标白名单不包含 auth、secret、ToolGovernor、可靠投递、持久化 owner 或生产启动配置。
候选只能建议 catalog example、tool dependency、recovery budget、clarification policy 或 turn
contract review。CLI 无网络、模型、插件、MCP 或 Git 写权限需求。

## 9. 可观测性

bundle 提供 policy、source digest、evidence age、每个 Gate 的 passed/reason、candidate id/kind/
target/failure count/rate、required gates、created/expires 时间和最终 verdict。所有 reason code
稳定，便于 M6/M7 报告差分。

## 10. 验收标准

1. 已映射且样本、时效、安全、Holdout 全过时生成确定性候选并标记人工审查 GO。
2. 未知 reason、过期证据、安全违规、Holdout 回退和样本不足分别得到 NO_GO。
3. 任何输出都不含 evaluations、消息正文、工具参数、prompt 或凭据。
4. 同输入和 as_of 重跑的 candidate identity 与 verdict 一致。
5. CLI smoke、单元测试、Ruff、Pyright 和严格 SDD 通过。

## 11. 源码依据

- `eval/trajectory/contracts.py`
- `eval/trajectory/scoring.py`
- `eval/trajectory/run.py`
- `docs/design/agent-closed-loop-v1.md`
- `docs/design/project-workbook-and-semantic-safety.md`

## 12. 未决问题

候选实现、base/candidate 实验和最终 production promotion 必须是后续独立 Goal，并继续经过
人工 SDD review；v1 不把“建议可调查”扩大成“建议自动修改”。
