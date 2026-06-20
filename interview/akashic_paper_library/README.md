# Akashic Personal Assistant Paper Library

本库只围绕 Akashic 作为“个性化助手”时最核心的研究问题组织论文：长期记忆、个性化建模、工具调用/主动性、记忆评测、记忆安全。

## 收录规则

- 核心库优先收录 2025/2026 年正式主会论文：ICLR、ICML、AAAI、ACL、NAACL、EACL、EMNLP、COLING、WWW。
- arXiv、Workshop、Withdrawn Submission 只放候选扩展，不混入核心 Zotero 导入集。
- 与 Akashic 的关联必须明确：能指导 memory schema、memory update/retrieval、personalization、tool/proactive evaluation 或安全治理。
- Zotero 中统一放在 `Akashic` 父 collection 下，并按 `classification.md` 的 7 个子 collection 分类；每篇论文必须至少进入一个子分类。

## 分类维护

- `classification.md`: 当前 Zotero 子分类、判定标准、现有论文归类和新增论文入库规则。
- `classification-manifest.json`: 当前 23 篇论文的主分类/副分类清单，用于后续校验和补充新论文。
- 后续新增论文时，先判断主分类，再补副分类；不要只导入到 `Akashic` 父 collection 后就停止。

## PDF 本地副本

- `pdfs/`: Akashic 核心论文 PDF 的本地副本目录。
- `pdfs/download-manifest.json`: PDF 下载/复制清单，记录每篇论文对应的 PDF 文件和来源。
- `download_pdfs.py`: 批量补齐 PDF 的脚本；后续新增论文时可以重跑。
- 论文 PDF 体积不大，统一直接复制到本地 `pdfs/`，不要只保留 Zotero 链接、远程 URL 或临时下载路径。

## 阅读优先级

| 优先级 | 论文 | 会议 | 适合 Akashic 的原因 |
| --- | --- | --- | --- |
| P0 | Mem-PAL: Towards Memory-based Personalized Dialogue Assistants for Long-term User-Agent Interaction | AAAI 2026 | 直接面向长期用户-助手交互、个性化服务和中文 PAL-Bench，可作为个人助手 memory benchmark 参考 |
| P0 | SeCom: On Memory Construction and Retrieval for Personalized Conversational Agents | ICLR 2025 | 讨论 memory unit 粒度、conversation segmentation 和 compression denoising，适合优化 Akashic 的记忆写入与检索 |
| P0 | In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents | ACL 2025 | 提出前瞻/回顾反思机制，可指导长期记忆的异步总结、检索反馈和在线修正 |
| P0 | LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory | ICLR 2025 | 适合作为 Akashic 个人助手长期记忆 QA 的核心评测集 |
| P0 | Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions | ICLR 2026 | 从 incremental multi-turn 角度测 agent memory 能力，适合补充交互式测评 |
| P1 | H-Mem: Hybrid Multi-Dimensional Memory Management for Long-Context Conversational Agents | EACL 2026 | 时间树 + 语义树的双层记忆结构，适合设计 time/topic 双索引 |
| P1 | Amory: Building Coherent Narrative-Driven Agent Memory through Agentic Reasoning | EACL 2026 | 用 narrative、momentum consolidation 和 coherence retrieval 提升长期对话记忆一致性 |
| P1 | H-MEM: Hierarchical Memory for High-Efficiency Long-Term Reasoning in LLM Agents | EACL 2026 | 分层抽象和 index routing，适合降低 memory retrieval 延迟 |
| P1 | Personalized Large Language Model Assistant with Evolving Conditional Memory | COLING 2025 | 直接面向个性化 LLM 助手，关注 evolving conditional memory |
| P1 | Hello Again! LLM-powered Personalized Agent for Long-term Dialogue | NAACL 2025 | 长短期 memory bank、topic retrieval、动态 persona 建模 |
| P1 | Towards Lifelong Dialogue Agents via Timeline-based Memory Management | NAACL 2025 | 用 timeline 表达用户行为变化和因果演化，适合支持 memory version/update |
| P1 | Exploring the Potential of LLMs as Personalized Assistants: Dataset, Evaluation, and Analysis | ACL 2025 | HiCUPID 个性化助手 benchmark，可补充用户偏好和回复质量评测 |
| P1 | Evaluating Personalized Tool-Augmented LLMs from the Perspectives of Personalization and Proactivity | ACL 2025 | 适合评估 Akashic 的 MCP/tool 调用是否真正个性化、主动 |
| P1 | Memory OS of AI Agent | EMNLP 2025 | 用 OS 类比短期/中期/长期记忆管理，适合面试中讲 memory architecture |
| P2 | MemInsight: Autonomous Memory Augmentation for LLM Agents | EMNLP 2025 | 强调自动 memory augmentation 和语义结构增强，可参考为异步 memory worker |
| P2 | MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents | ICLR 2026 | 用 RL 学习压缩状态，适合长期任务低上下文成本方向 |
| P2 | Look Back to Reason Forward: Revisitable Memory for Long-Context LLM Agents | ICLR 2026 | 支持 revisitable memory 和非线性回看，适合 long-context agent 设计 |
| P2 | From RAG to Memory: Non-Parametric Continual Learning for Large Language Models | ICML 2025 | HippoRAG 2，把 Graph/RAG 推向长期记忆，适合检索层升级 |
| P2 | MemoRAG: Boosting Long Context Processing with Global Memory-Enhanced Retrieval Augmentation | WWW 2025 | 全局 memory-enhanced retrieval，可作为 RAG/Memory 融合参考 |
| P2 | Improving Factuality with Explicit Working Memory | ACL 2025 | 用显式 working memory 改善 factuality，适合短期任务状态设计 |

## Zotero 标签方案

导入 BibTeX 时使用 `keywords` 生成标签：

- `akashic-paper-library`
- `personal-assistant`
- `agent-memory`
- `long-term-memory`
- `personalization`
- `benchmark`
- `tool-use`
- `proactive-agent`
- `memory-retrieval`
- `memory-safety`

## 文件

- `akashic-topconf-2025-2026.bib`: 完整核心库 BibTeX。
- `zotero-import-new.bib`: 用于 Zotero 导入的新条目集；如果 Zotero 已有条目，应避免重复导入。
- `classification.md`: Akashic 论文库分类规则。
- `classification-manifest.json`: 可核对的分类 manifest。
- `download_pdfs.py`: PDF 本地副本下载/补齐脚本。
- `pdfs/`: 当前 23 篇核心论文的 PDF 本地副本。

## 候选扩展

这些论文/方向相关，但不进入核心主会库：

- PersonaAgent: NeurIPS 2025 Workshop poster，个性化 agent 框架很相关，但不是主会论文。
- MemoryCD: ICLR 2026 Workshop LLA，适合后续 benchmark 扩展。
- Collaborative Memory: ICLR 2026 submission 页面显示为 submitted，不能按正式录用主会论文引用。
- A-Mem、LightMem、StructMem、EvolveMem 等 2025/2026 arXiv memory 架构论文，可作为工程参考，但不放入“顶会核心库”。
