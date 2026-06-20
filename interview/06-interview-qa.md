# 06. 高频面试问答

## 1. 这个项目解决什么问题？

它解决的是个人 AI 助手缺乏长期上下文和工具扩展能力的问题。普通聊天机器人每次只看当前对话，无法稳定记住用户偏好，也不能主动完成信息订阅、论文搜索、图片生成等任务。Akashic 通过长期记忆、工具注册、MCP 扩展和主动推送，把一次性聊天升级成可持续协作的个人助手。

## 2. 为什么不直接用现成聊天机器人？

现成聊天机器人通常缺少可控的长期记忆写入、证据追溯、工具编排和本地评估。这个项目更像 Agent Runtime：有 Phase 生命周期、EventBus、ToolRegistry、MCP、memory2 和 benchmark runner。它关注的是“如何构建一个可扩展、可评估、可长期运行的个人助手系统”。

## 3. 项目最核心的技术点是什么？

最核心的是三点：

1. 长期个人记忆：profile、preference、procedure、event 的写入、检索、更新和 source_ref 追溯。
2. 工具系统：ToolRegistry + ToolExecutor + deferred tool_search + MCP，使工具能力可扩展且不污染 prompt。
3. 主动推送：通过 proactive loop 拉取外部信息，判断是否值得推送，并通过 Telegram 主动触达用户。

## 4. Phase 生命周期有什么价值？

Phase 机制把单轮对话拆成多个阶段，每个阶段由模块组成，模块声明依赖关系后自动排序执行。它的价值是解耦：memory 检索、工具上下文同步、插件拦截、turn commit、主动推送副作用都可以插入到合适阶段，而不是把逻辑写死在主循环里。

## 5. 为什么需要 deferred tool search？

工具数量多时，全量暴露 schema 会让 prompt 变大，也会让模型更容易误调用工具。Deferred tool search 只把常用工具 always_on，其他工具通过工具目录检索和解锁。模型需要某个能力时先 tool_search，再调用被解锁的工具。这让系统可以接入大量 MCP 工具，同时保持上下文稳定。

## 6. MCP 和普通工具怎么取舍？

普通工具适合简单、稳定、强耦合的本地能力，比如 recall_memory、message_push。MCP 适合独立能力边界，比如 imagegen、arXiv、浏览器自动化、第三方 API。MCP 的好处是协议化、隔离部署、可独立维护，主 Agent 只需要通过统一 schema 调用。

## 7. 个人 memory 什么时候写入？

不是每条消息都写入长期记忆。对话结束后，系统通过 TurnCommitted 触发 consolidation。LLM 会从对话窗口中提取长期有价值的信息，例如用户画像、偏好和长期执行规则。写入门槛要高，原则是“6 个月后新会话仍然有用”才写。

## 8. 如何避免错误记忆？

我从四层处理：

1. 写入 prompt 高门槛，宁可漏写不要误写。
2. 类型约束明确，区分 profile、preference、procedure、event。
3. source_ref 追溯原始消息，重要回答可以回查证据。
4. 支持 supersede 和 forget，用户偏好变化时更新旧记忆。

## 9. Memory 和 RAG 有什么区别？

RAG 是从外部知识库检索文档来回答问题，Memory 是持续维护用户状态。个人助手的 memory 会在线写入、更新、遗忘，还要处理偏好变化、证据追溯和个性化注入。它不只是检索，更是用户模型的长期维护。

## 10. 记忆检索为什么不用纯向量？

个人记忆里有很多项目名、人名、工具名、日期和短句偏好。纯向量容易漏掉字面匹配，所以系统做了向量 lane 和关键词 lane，再用 RRF 融合。这样既保留语义召回，又保留精确词命中。

## 11. 用户偏好变化怎么办？

对 preference、procedure、部分 profile 这类状态型记忆，写入时会查找相似旧记忆。如果新记忆替代旧记忆，就把旧项标记为 superseded，并在 memory_replacements 记录替换关系。这样当前回答使用最新偏好，同时保留历史审计。

## 12. 主动推送如何避免打扰用户？

主动推送不是固定 cron。Proactive loop 会根据用户近期活跃度、冷却时间、去重状态和内容价值判断是否推送。对信息源区分 alert、content、context，高优先级 alert 可直接推送，普通 content 需要先判断 interesting，再结合近期聊天和历史推送生成消息。

## 13. imagegen 为什么生成后能自动推送？

imagegen 作为 MCP 工具返回 artifact。passive_turn 检测到这个工具成功后，会提取第一张图片路径，通过 OutboundDispatch 直接推送到当前 Telegram 会话，并把“已推送”的系统提示回填给模型，避免模型重复调用 message_push 或再次发送同一图片。

## 14. arXiv 搜索如何做成个人助手能力？

arXiv search 作为 MCP 工具注册进 ToolRegistry。用户可以自然语言触发搜索，未来也可以由 proactive loop 根据长期偏好定时搜索。搜索结果可以格式化后主动推送到 Telegram，这样就从“问答工具”变成了“个性化论文助手”。

## 15. 如何证明这个系统有效？

我接入了 LongMemEval 风格评测，能自动导入历史会话、触发记忆写入、运行 Agent QA 并记录 F1/EM、tool_chain 和错误。当前已经用 EverMemBench、SocialMemBench 做了 smoke test。虽然分数还不高，但评估链路可以帮助定位是模型、写入、检索还是回答整合的问题。

## 16. 当前项目最大的不足是什么？

当前 memory 还主要依赖 LLM 提取，容易漏写或误写；全量 benchmark 成本较高；主动推送对信息源质量和用户偏好建模依赖较强。下一步我会做 memory write gate、retriever ablation、source evidence mode 和 grouped benchmark runner。

## 17. 如果让你重构 memory，你会怎么做？

我会把 memory pipeline 拆成 raw event、candidate extraction、write gate、structured memory、retrieval、audit 六层。每条记忆加 confidence、source_ref、validity、version 和适用场景。写入异步化，检索先结构过滤再多路召回，最后 rerank，并定期做 memory audit 清理冲突和过期项。

## 18. 面试中如何讲项目难点？

可以讲三个难点：

1. 长期记忆可靠性：写少了没用，写错了会污染助手，需要门控、追溯和更新。
2. 工具扩展规模：工具多了不能全塞 prompt，需要 deferred search 和 MCP 边界。
3. 主动推送质量：既要及时，又不能打扰，需要兴趣判断、去重、冷却和失败降级。

