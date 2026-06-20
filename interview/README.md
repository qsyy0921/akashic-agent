# Akashic 个人助手面试资料

这个目录只围绕“个人 AI 助手”这条主线整理，不把群聊记忆作为核心卖点。面试表达建议从“长期记忆 + 工具扩展 + 主动推送 + 可评估”四个关键词展开。

## 目录

| 文件 | 用途 |
| --- | --- |
| [01-project-overview.md](./01-project-overview.md) | 项目定位、技术栈、主要功能和一句话介绍 |
| [02-architecture.md](./02-architecture.md) | 整体架构、被动对话链路、Phase 生命周期 |
| [03-personal-memory.md](./03-personal-memory.md) | 个人长期记忆系统的设计、写入、检索、更新和可靠性 |
| [04-tools-mcp-proactive.md](./04-tools-mcp-proactive.md) | tool_search、MCP 注册、imagegen、arXiv、主动推送 |
| [05-evaluation.md](./05-evaluation.md) | 记忆能力评估、已有数据集、当前结果和改进方向 |
| [06-interview-qa.md](./06-interview-qa.md) | 高频面试问答，适合背诵和追问演练 |
| [07-resume.md](./07-resume.md) | 简历项目简介、技术栈、主要功能、可量化表达 |
| [08-algorithm-metrics-baseline.md](./08-algorithm-metrics-baseline.md) | 当前算法指标 baseline、问题定位和优化目标 |
| [09-memory-literature-improvement-plan.md](./09-memory-literature-improvement-plan.md) | memory 文献驱动的阶段性改进计划 |
| [11-2026-top-conference-memory-reading-notes.md](./11-2026-top-conference-memory-reading-notes.md) | 2026 顶会/高质量 memory 论文阅读笔记 |
| [codex-goal-short-prompt.md](./codex-goal-short-prompt.md) | 直接复制给 Codex Goal 的固定短版入口，不承载动态实验状态 |
| [12-continuous-memory-improvement-goal-prompt.md](./12-continuous-memory-improvement-goal-prompt.md) | 持续维护的 memory 改进配置；动态目标、指标、失败分析和下一步实验都写在这里 |
| [memory_evaluation_report.md](./memory_evaluation_report.md) | SocialMemBench 方法对比、指标、失败类型和面试表述 |

## 面试主线

Akashic 可以介绍为一个面向个人长期协作场景的 AI 助手。它不是只做单轮问答，而是把用户消息接入、长期记忆、工具调用、MCP 扩展、主动推送和评估闭环组合在一起，让助手能够跨会话理解用户偏好，并在合适时机主动完成信息检索、图片生成、论文推荐等任务。

推荐开场：

> 我做的是一个个人 AI 助手系统，重点不是简单聊天，而是让 Agent 具备长期记忆、可插拔工具能力和主动触达能力。系统通过 Phase 生命周期组织单轮对话，通过 memory2 做长期个人记忆的写入和检索，通过 tool_search 和 MCP 按需加载外部工具，并通过 proactive loop 把 arXiv 等信息源筛选后推送到 Telegram。

## 需要重点准备的问题

1. 为什么这个项目不是普通聊天机器人？
2. 长期记忆和 RAG 的区别是什么？
3. 记忆什么时候写入？如何避免错误记忆？
4. 用户偏好变化时如何更新旧记忆？
5. 为什么要做 deferred tool search？
6. MCP 和普通 tool 的边界是什么？
7. 主动推送如何避免打扰用户？
8. 如何证明 memory 系统有效？
9. 项目目前最大不足是什么？下一步怎么优化？
