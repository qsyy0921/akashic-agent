---
unit: core-intent-view-fusion-v3
status: verified
depends_on: PLG-003, PLG-008, core-tool-graph-v1
---

# IntentView Fusion v3

## 1. 目标与范围

本单元验收并固化现有 Intent Routing V3：从已提交对话事实构造有界 RouteContext，
由受约束 LLM 只生成 IntentView，再把原始消息的词法/向量证据与 IntentView 派生查询
做确定性 RRF 融合，最后从当前 ToolRegistry snapshot 选择可见工具。

## 2. 职责与非目标

本单元负责上下文消歧、最多四个目标及依赖、三路候选证据、不确定性、禁用工具过滤、
快照绑定和 active Gate。它不允许 LLM 直接返回工具名或授权，不替代 ToolGovernor、
TaskPlan、参数校验和审批，也不靠关键词硬编码修复单个评测样例。

本单元不新增 Provider、不切换模型、不扩大在线上下文、不修改冻结评测 oracle，不把
一次 LLM 输出当作长期记忆或成功事实。

## 3. 契约与依赖

- RouteContextBuilder 只消费当前消息、最近八条已提交消息、回复摘要、附件种类和历史
  operation/output facts；总文本不超过 6000 字符并脱敏。
- LLMIntentViewProvider 只输出 schema v3 IntentView：目标、重写意图、假设能力、依赖和
  不确定性。提示词明确禁止注册工具名，解析器也接收 forbidden tool names 做校验。
- HybridRouteRetriever 只在当前 DiscoverySnapshot 文档中检索；LLM 派生查询不能创造
  Registry 中不存在的 capability。
- IntentV3TurnRouteAdvisor 通过 lexical、Qwen dense 和 LLM-view query 三路 RRF 排序，
  再过滤 unhealthy、disabled 和输出种类不匹配的 provider。
- active 模式必须验证源码、模型、prompt、数据集、catalog、配置和阈值摘要。

## 4. 设计不变量

1. LLM 信号是候选证据，不是工具授权或执行结果。
2. 缺少 RouteContext、IntentView schema 无效、模型不可用或快照不一致时不得伪造 resolved。
3. derived query 最长 96 字符；IntentView 最多四个目标，每目标最多三个假设能力。
4. 工具候选必须来自同一 DiscoverySnapshot，disabled/unhealthy 工具不能进入 preloads。
5. 多 provider 等价 operation 只按 snapshot health 和稳定名称选择，不接受目录顺序偏置。
6. active Gate 的任一权威摘要过期或阈值失败都必须拒绝启动。
7. 路由 trace 不保存密钥、完整隐藏思考或未脱敏历史。

## 5. 运行时流程

```text
committed history + current message
              |
              v
bounded/redacted RouteContext
              |
              +-------------------+
              |                   |
              v                   v
original lexical+dense      LLM IntentView
              |                   |
              |             derived queries
              |                   |
              +---------+---------+
                        v
             deterministic RRF fusion
                        |
                        v
       snapshot/health/disabled/output filters
                        |
                        v
        RouteAdvice -> TaskPlan -> ToolExecutor
```

## 6. 数据所有权与状态

RouteContext、IntentView、OperationEvidence 和 RouteAdvice 都是 C 类请求内视图。它们从
Session 和当前 Registry generation 重建，不更新历史消息、memory2 或插件状态。Gate
报告是启动凭证，不是在线学习数据；原始评测报告同时保存在机械盘报告目录。

## 7. 失败处理

协议控制消息直接 no_tool。已知 dense/IntentView 不可用在 shadow 模式返回零预加载的
unavailable advice；active 模式的 Gate 缺失或过期 fail-loud。IntentView transport 只允许
同模型、同端点、共享总超时的一次追加尝试，不引入备用 Provider。未知异常继续上抛。

## 8. 安全

RouteContextBuilder 对 API key、Telegram token、Bearer token 和常见 secret 赋值做脱敏。
LLM 不接收凭据，不能指定注册工具名；最终可见工具仍受 generation、snapshot、disabled、
ToolGraph、ToolGovernor 和审批约束。评测报告不包含 prompt 原文、响应正文或密钥。

## 9. 可观测性

RouteAdvice 记录 router/discovery snapshot、每路 rank、reason code、status、decision band、
uncertainty 和 latency。fresh Gate 记录数据集/catalog/model/prompt/config/source digest 以及
Recall、完整目标、上下文、澄清、可见率、状态和延迟指标。

## 10. 验收标准

1. 上下文跟进、意图切换、假切换、多目标依赖和 no-tool 均有 contract/integration 测试。
2. LLM 派生查询不能绕过 Registry、disabled、health、output kind 和 snapshot 检查。
3. 当前仓库 Gate 报告必须与源码、冻结数据集和 catalog 摘要一致。
4. 真实 Terra + 本地 Qwen Gate 达到既有最小/最大阈值且 failures 为空。
5. 相关单元/集成回归、Ruff、Pyright 和严格 SDD 校验通过。

最新真实 Gate（2026-08-19）：25 cases / 27 goals / 13 tools；operation Recall@1/3、
complete-operation recall、context resolution、no-tool precision/recall 和 visibility 均为
1.0；status accuracy 为 0.96；unnecessary clarification rate 为 0.095238；Gate passed，
failures 为空。原始报告位于机械盘 Junction 下的
`docker/debug/reports/intent-routing/m3-v3-gate-20260819.json`。

## 11. 源码依据

- `agent/routing/context.py:RouteContextBuilder`
- `agent/routing/intent_view.py:LLMIntentViewProvider,IntentViewAnalyzer`
- `agent/routing/advisor_v3.py:IntentV3TurnRouteAdvisor,_rank_operations`
- `agent/routing/hybrid.py:HybridRouteRetriever`
- `agent/routing/gate_v3.py:verify_v3_quality_gate`
- `agent/core/passive_turn.py:DefaultReasoner.run_turn`
- `eval/intent_routing/run_v3_eval.py`
- `eval/intent_routing/v3_dataset.zh.jsonl`
- `tests/test_intent_routing_v3_gate.py`

## 12. 未决问题

当前冻结集规模只有 25 例，不能支持“通用意图准确率 100%”结论。fresh Gate 有一个
不可行请求状态误判和两个多余澄清，其中澄清率距离 0.10 上限较近。扩大审核 Gold、
独立 Holdout 和安全对抗集属于 M6 数据/轨迹评测工作；在独立数据通过前不得上调宣传
口径或通过降低阈值掩盖误判。
