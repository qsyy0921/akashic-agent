# 01. 项目概览

## 一句话介绍

Akashic 是一个面向个人长期协作场景的 AI 助手，支持 Telegram/QQ 等多渠道接入，具备长期记忆、工具调用、MCP 扩展、主动推送和评估能力，可以在多轮、多天、多任务中持续理解用户偏好并主动完成任务。

## 项目定位

不要把它讲成“接了 Telegram 的聊天机器人”。更准确的定位是：

- 个人助手：服务一个长期用户，围绕用户偏好、工作流、兴趣和历史上下文持续优化。
- Agent Runtime：不是一次 prompt，而是一套可插拔的对话生命周期、工具执行和事件系统。
- Memory-first：核心能力是跨会话记忆、召回和更新，不只是把最近聊天记录塞进上下文。
- Tool/MCP 平台：通过内置工具和 MCP 扩展，把图片生成、论文搜索、消息推送等能力接入 Agent。
- Proactive Assistant：不只被动回复，还能根据外部信息源和用户状态判断是否主动推送。

## 技术栈

- 语言与运行时：Python 3.12、asyncio、FastAPI、SQLite。
- Agent 编排：自研 Phase 生命周期、EventBus、ToolRegistry、ToolExecutor。
- 记忆系统：Markdown memory、SQLite memory2、embedding 检索、BM25/关键词召回、RRF 融合、source_ref 证据追踪。
- 工具系统：OpenAI function calling 风格 schema、deferred tool search、MCP stdio client、插件注册。
- 渠道接入：Telegram Bot、QQ/NapCat、Dashboard。
- 主动推送：proactive loop、MCP 信息源、去重、冷却、message_push。
- 评估：LongMemEval 适配器、EverMemBench、SocialMemBench、GroupMemBench probe、本地 benchmark runner。

## 主要功能

1. 多渠道个人对话接入  
   支持 Telegram 和 QQ 等消息入口，将外部消息规范化为统一会话，再进入 Agent 被动回复链路。个人助手场景中，Telegram 更适合作为稳定交互和主动推送渠道。

2. 可插拔 Phase 生命周期  
   单轮对话被拆为 BeforeTurn、BeforeReasoning、PromptRender、Reasoner、AfterReasoning、AfterTurn 等阶段。插件可以按 slot 依赖注入逻辑，避免硬改核心流程。

3. 个人长期记忆系统  
   系统将对话中的用户画像、偏好、长期执行规则和事件沉淀到结构化存储中。回复前做语义检索和关键词召回，回复后异步 consolidation，支持 source_ref 追溯和 supersede 更新。

4. Deferred Tool Search  
   工具不是全部暴露给模型，而是将常用工具 always_on，其余工具通过 tool_search 按需检索并解锁。这样可以降低 prompt 体积，减少模型误调用，也方便扩展大量 MCP 工具。

5. MCP 工具扩展  
   外部能力通过 MCP server 注册成标准 Tool，例如 ChatGPT imagegen、arXiv search。Agent 只关心统一工具 schema，不需要知道工具背后是本地函数、插件还是 MCP 子进程。

6. 主动推送能力  
   Proactive loop 定期拉取外部信息源，经过 gate、fetch、judge、resolve、deliver 流程，判断是否值得推送。适合做论文监控、信息订阅、提醒和个性化推荐。

7. 记忆评估闭环  
   项目已经接入 LongMemEval 风格评测，并适配 EverMemBench、SocialMemBench、GroupMemBench probe，用于观察长期记忆在偏好、人物、时间线和跨会话问题上的表现。

## 面试中最值得强调的技术含量

- 不是单 prompt，而是完整 Agent Runtime。
- 不是简单 RAG，而是长期个人记忆的写入、检索、更新、证据追踪和评估。
- 不是固定工具列表，而是 deferred tool search + MCP 动态扩展。
- 不是一次性提醒，而是主动推送链路，有去重、冷却、兴趣判断和外部信息源接入。
- 有 benchmark 和可观测结果，能讨论系统缺陷和优化方案。

## 不建议主讲的内容

- 暂时不要把群聊长期记忆作为核心卖点。当前更稳的讲法是个人助手，群聊观察只作为未来扩展方向。
- 不要强调“反向代理 ChatGPT”本身。面试时更应该讲 MCP 工具化接入和图像生成 pipeline。
- 不要把模型能力讲成项目能力。模型是底座，项目价值在架构、记忆、工具、评估和工程闭环。

