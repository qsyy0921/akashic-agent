# Codex Goal 短版 Prompt

用途：下面代码块直接复制给 Codex Goal。它是固定入口，只负责启动任务和指向维护文档，不承载最新方法、指标、失败分析或实验计划。

冻结规则：复制给 Codex Goal 的短版默认不改。后续需要调整、维护、迭代的内容，全部写入 `interview/12-continuous-memory-improvement-goal-prompt.md`。

```text
请在 E:\agent\akashic 中继续改进 Akashic 个人助手的长期记忆能力。

开始前先阅读并遵守：
interview/12-continuous-memory-improvement-goal-prompt.md

按该文档中的“维护配置”执行当前最新目标、约束、论文依据、下一步方法、评测协议和文档更新要求。不要凭直觉调规则；每个新方法都必须可复现、可量化、可解释，并保留完整实验产物。
```

维护规则：
- 复制给 Codex Goal 的只有上面的代码块。
- 这个短版默认不随实验进展变化。
- 不要把 method 编号、最新指标、临时结论、下一步实验细节写进短版。
- 需要长期维护的内容全部写入 `interview/12-continuous-memory-improvement-goal-prompt.md` 的“维护配置”。
- 只有工作目录、维护文档路径或入口规则本身变化时，才允许修改这个短版。
