# 07. 简历表达

## 项目名称

Akashic 个人 AI 助手系统

## 项目简介

Akashic 是一个面向个人长期协作场景的 AI 助手系统，支持 Telegram/QQ 等多渠道接入，具备长期记忆、工具调用、MCP 扩展、主动推送和评估能力。系统通过可插拔 Phase 生命周期组织单轮对话，通过 memory2 维护用户画像、偏好和长期执行规则，通过 deferred tool_search 按需加载外部工具，并结合 proactive loop 实现论文搜索、图片生成、信息订阅等个性化主动服务。

## 技术栈

Python 3.12、asyncio、FastAPI、SQLite、Telegram Bot、QQ/NapCat、MCP、OpenAI function calling、embedding 检索、关键词检索、RRF、LongMemEval、Docker。

## 主要功能

- 设计并实现 Agent 单轮对话生命周期，将消息处理拆分为 BeforeTurn、BeforeReasoning、PromptRender、Reasoner、AfterReasoning、AfterTurn 等阶段，支持插件按依赖关系注入逻辑，降低核心流程耦合。

- 构建个人长期记忆系统，支持 profile、preference、procedure、event 等记忆类型，完成对话后 consolidation、SQLite 结构化存储、source_ref 证据追踪、supersede 更新和 recall_memory 检索。

- 优化记忆检索链路，结合 embedding 向量检索、关键词召回、辅助查询和 RRF 融合，解决个人记忆中项目名、人名、工具名、日期等字面信息容易被纯向量检索漏召回的问题。

- 实现 ToolRegistry 和 deferred tool_search 机制，将高频工具设为 always_on，其余工具按需检索和解锁，降低工具 schema 对 prompt 的污染，并支持大量外部工具扩展。

- 接入 MCP 工具体系，将 ChatGPT imagegen、arXiv search 等外部能力封装为标准工具，统一进入 ToolExecutor、hook、事件和可观测链路。

- 完成 Telegram 主动推送链路，支持 imagegen 图片生成后自动推送、arXiv 搜索结果格式化推送，以及 proactive loop 中基于兴趣判断、去重和冷却的个性化信息触达。

- 建立长期记忆评估流程，适配 LongMemEval 风格 benchmark，并接入 EverMemBench、SocialMemBench 等数据集，记录 F1/EM、tool_chain 和错误信息，用于定位记忆写入、检索和回答整合问题。

## 350 字版本

Akashic 是一个面向个人长期协作场景的 AI 助手系统，支持 Telegram/QQ 多渠道接入，具备长期记忆、工具调用、MCP 扩展、主动推送和评估能力。项目将单轮对话拆分为 BeforeTurn、BeforeReasoning、PromptRender、Reasoner、AfterReasoning、AfterTurn 等可插拔阶段，通过 EventBus 解耦记忆写入、工具日志和后处理逻辑。记忆系统使用 Markdown + SQLite memory2 混合架构，沉淀用户画像、偏好、长期执行规则和事件，支持 source_ref 追溯、supersede 更新，以及向量检索、关键词召回、RRF 融合的多路检索。工具层实现 ToolRegistry 和 deferred tool_search，只暴露高频工具，其余工具按需解锁，降低 prompt 污染，并通过 MCP 接入 imagegen、arXiv search 等外部能力。主动推送链路基于 proactive loop，对外部信息源进行兴趣判断、去重和冷却控制，可将论文搜索结果或生成图片主动推送到 Telegram。项目还接入 LongMemEval 风格评测，用于量化长期记忆效果并定位写入、检索和模型回答问题。

## 面试开场版本

> 我做的是一个个人 AI 助手系统，重点不是简单聊天，而是让 Agent 能够长期理解用户。它有三个核心能力：第一是长期个人记忆，能保存用户画像、偏好和长期规则；第二是工具扩展，通过 deferred tool_search 和 MCP 接入 imagegen、arXiv 等外部能力；第三是主动推送，系统可以根据用户兴趣定时筛选内容并推送到 Telegram。为了避免只做 demo，我还接入了 LongMemEval 风格评测，用数据观察 memory 写入、召回和回答质量。

## STAR 表达

Situation：
普通聊天机器人只能处理当前上下文，缺乏长期用户状态和主动服务能力。

Task：
构建一个可长期运行的个人 AI 助手，让它能记住用户偏好、调用外部工具，并主动推送有价值信息。

Action：
设计 Phase 生命周期和 EventBus 解耦对话链路；实现 memory2 长期记忆系统；引入 deferred tool_search 降低工具 schema 污染；通过 MCP 接入 imagegen 和 arXiv；建设 proactive loop 与 benchmark runner。

Result：
系统可以通过 Telegram/QQ 进行多轮对话，跨会话召回用户偏好，按需调用外部工具，并将图片生成或论文搜索结果主动推送给用户；同时具备本地评估链路，可持续量化和优化长期记忆能力。

