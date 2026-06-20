# 04. 工具、MCP 与主动推送

## ToolRegistry

ToolRegistry 是项目工具系统的中心。每个工具注册时会保存：

- 工具对象。
- OpenAI function calling 风格 schema。
- risk 等级：read-only、write、external-side-effect。
- always_on 标记。
- search_hint。
- source_type：builtin、plugin、mcp。
- source_name：MCP server 名或插件来源。

相关代码：

- `agent/tools/registry.py`
- `agent/tools/meta/register.py`
- `bootstrap/tools.py`

面试表达：

> 我把工具抽象成统一 Tool 接口，并在 ToolRegistry 中维护 schema、风险等级和来源。这样内置工具、插件工具、MCP 工具都可以进入同一条执行链路。

## Deferred Tool Search

问题：工具越来越多时，如果每轮都把所有工具 schema 塞进 prompt，会造成上下文膨胀、模型误调用和成本上升。

解决方案：

- 常用工具设置为 always_on，例如 recall_memory、message_push、web_fetch。
- 其他工具作为 deferred tools，只进入工具目录，不直接暴露 schema。
- 模型需要工具时先调用 tool_search。
- tool_search 返回匹配工具，并把这些工具 schema 解锁到当前 turn。
- 如果模型直接调用未加载工具，系统会返回提示，让模型先 `tool_search(query="select:工具名")`。

相关代码：

- `agent/tools/tool_search.py`
- `agent/tools/registry.py`
- `agent/core/passive_turn.py`
- `agent/tools/search_backend.py`

面试表达：

> Deferred tool search 本质上是工具级检索增强。模型默认只看到少量 always_on 工具和工具目录提示，需要某个能力时再检索并解锁 schema。这样能把工具数量扩展到很多 MCP server，而不会让每轮 prompt 都被工具 schema 污染。

## MCP 接入

MCP 用来把外部能力变成标准工具。项目中的 McpServerRegistry 负责：

1. 读取 `mcp_servers.json`。
2. 启动 MCP stdio 子进程。
3. 通过 JSON-RPC 获取远端工具列表。
4. 使用 McpToolWrapper 包装成项目本地 Tool。
5. 注册到 ToolRegistry，source_type 标记为 mcp。
6. 启动时后台重连，关闭时统一断开。

相关代码：

- `agent/mcp/client.py`
- `agent/mcp/registry.py`
- `agent/mcp/tool.py`
- `agent/mcp/manage_tools.py`
- `bootstrap/toolsets/mcp.py`

面试表达：

> MCP 在这个项目里是能力扩展边界。Agent 不直接依赖某个外部服务 SDK，而是通过 MCP server 暴露标准工具。比如 imagegen 和 arXiv 都可以作为 MCP 工具注册进 ToolRegistry，模型只看到统一 schema。

## imagegen pipeline

imagegen 的面试讲法不要强调“反代网页”，而要讲成“把图像生成能力 MCP 工具化”：

1. ChatGPT imagegen 作为 MCP server 接入。
2. MCP 工具注册为 `mcp_chatgpt_imagegen__chatgpt_image_generate`。
3. 模型需要生成图片时先 tool_search 解锁工具。
4. 工具返回 artifact，其中包含生成图片路径。
5. passive_turn 检测 imagegen 工具成功后，自动提取图片 artifact。
6. OutboundDispatch 立即把图片推送到当前 Telegram 会话。
7. 系统提示模型不要重复调用 message_push 发送同一张图。

相关代码：

- `agent/core/passive_turn.py`
- `agent/lifecycle/phases/after_reasoning.py`
- `agent/turns/outbound.py`

面试表达：

> 我对 imagegen 做了特殊后处理，不是等模型拿到工具结果后再决定怎么发，而是在工具成功返回 artifact 后由 pipeline 自动推送第一张图片，并把“已发送”的事实回填给模型，避免重复发送或卡在工具结果展示上。

## arXiv search pipeline

arXiv 适合作为个人助手的代表 demo：用户长期关注某个方向，系统按需搜索并主动推送结果。

流程：

1. arXiv MCP server 注册搜索工具。
2. 用户询问“找找 arXiv 中关于 token 压缩的最新论文”。
3. 模型通过 tool_search 解锁 arXiv 工具。
4. 调用 `mcp_arxiv__arxiv_search` 获取论文列表。
5. passive_turn 检测 arXiv 工具成功且当前 channel 是 Telegram。
6. 格式化论文列表并自动推送给 Telegram。
7. 回填系统提示，避免模型再用 message_push 重复发送。

相关代码：

- `agent/core/passive_turn.py`
- `agent/mcp/registry.py`
- `proactive_v2/mcp_sources.py`

面试表达：

> arXiv 不是只做一个搜索函数，而是接进了工具发现、工具执行和主动推送链路。这样用户可以自然语言触发搜索，也可以未来由 proactive loop 根据个人偏好定时搜索并推送。

## 主动推送

主动推送是个人助手区别于聊天机器人的关键能力。

核心问题：

- 什么信息值得推送？
- 什么时候推送不会打扰用户？
- 如何避免重复推送？
- 推送失败如何降级？

当前链路：

1. ProactiveLoop 按自适应间隔 tick。
2. MCP pool 连接外部数据源。
3. fetch alert/content/context 三类信息。
4. Agent 对 content 做 interesting/not_interesting 分类。
5. 有高优先级 alert 时直接推送。
6. 有 interesting 内容时结合近期聊天和主动推送历史生成消息。
7. message_push 暂存并发送。
8. finish_turn 记录 reply/skip。
9. state 记录 seen、delivery、semantic dedupe、cooldown。

相关代码：

- `proactive_v2/loop.py`
- `agent/core/proactive_turn.py`
- `proactive_v2/tools.py`
- `proactive_v2/state.py`
- `proactive_v2/judge.py`

面试表达：

> 主动推送不是简单 cron。系统会先过 gate 判断是否适合打扰，再拉取 MCP 信息源，区分 alert/content/context，使用 LLM 判断兴趣并做去重，最后通过 message_push 发到目标渠道。这样可以支持论文订阅、状态提醒和个性化推荐。

## MCP vs 普通 Tool

| 维度 | 普通 Tool | MCP Tool |
| --- | --- | --- |
| 运行位置 | 项目进程内 | 外部 MCP server 子进程或服务 |
| 适合能力 | 简单本地函数、内置能力 | 独立服务、浏览器自动化、第三方 API |
| 部署边界 | 跟主项目耦合 | 可独立维护、替换、重启 |
| 注册方式 | Python 代码注册 | MCP list_tools 后包装注册 |
| 面试关键词 | 统一执行链路 | 插件化、协议化、能力隔离 |

回答建议：

> 简单、稳定、强耦合的能力适合普通 Tool；独立、可能长耗时、依赖外部运行环境或未来要复用的能力适合 MCP。imagegen 和 arXiv 都适合 MCP，因为它们是独立能力边界。

