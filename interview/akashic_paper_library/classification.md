# Akashic 论文分类规则

本分类只面向 Akashic 作为个人助手时的核心能力：长期记忆、个性化、工具调用、主动推送、记忆评测与可靠性。

## Zotero 分类结构

在 Zotero 的 `Akashic` collection 下维护 7 个子 collection：

| 编号 | 分类 | 收录标准 |
| --- | --- | --- |
| 01 | Benchmarks and Evaluation | 数据集、benchmark、评价协议、指标设计、实验框架 |
| 02 | Personalization and Dialogue Agents | 用户画像、长期对话、偏好建模、个性化助手行为 |
| 03 | Memory Architecture and OS | memory bank、分层记忆、系统架构、agent memory OS |
| 04 | Memory Construction and Consolidation | 记忆抽取、压缩、反思、更新、长期沉淀 |
| 05 | Retrieval RAG and Indexing | 记忆检索、索引、RAG 融合、rerank、图/树结构检索 |
| 06 | Tool Use and Proactivity | tool-augmented agent、MCP/tool 使用、主动性评测 |
| 07 | Working Memory Factuality and Safety | working memory、事实性、拒答、安全治理、错误记忆抑制 |

## 当前分类

### 01 Benchmarks and Evaluation

- Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions
- Evaluating Personalized Tool-Augmented LLMs from the Perspectives of Personalization and Proactivity
- Exploring the Potential of LLMs as Personalized Assistants: Dataset, Evaluation, and Analysis
- LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory
- Mem-PAL: Towards Memory-based Personalized Dialogue Assistants for Long-term User-Agent Interaction

### 02 Personalization and Dialogue Agents

- Exploring the Potential of LLMs as Personalized Assistants: Dataset, Evaluation, and Analysis
- Hello Again! LLM-powered Personalized Agent for Long-term Dialogue
- In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents
- Mem-PAL: Towards Memory-based Personalized Dialogue Assistants for Long-term User-Agent Interaction
- Personalized Large Language Model Assistant with Evolving Conditional Memory
- SeCom: On Memory Construction and Retrieval for Personalized Conversational Agents
- Towards Lifelong Dialogue Agents via Timeline-based Memory Management

### 03 Memory Architecture and OS

- From Isolated Conversations to Hierarchical Schemas: Dynamic Tree Memory Representation for LLMs
- H-MEM: Hierarchical Memory for High-Efficiency Long-Term Reasoning in LLM Agents
- H-Mem: Hybrid Multi-Dimensional Memory Management for Long-Context Conversational Agents
- MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents
- MemAgent: Reshaping Long-Context LLM with Multi-Conv RL-based Memory Agent
- Memory OS of AI Agent

### 04 Memory Construction and Consolidation

- Amory: Building Coherent Narrative-Driven Agent Memory through Agentic Reasoning
- In Prospect and Retrospect: Reflective Memory Management for Long-term Personalized Dialogue Agents
- MemInsight: Autonomous Memory Augmentation for LLM Agents
- Personalized Large Language Model Assistant with Evolving Conditional Memory
- SeCom: On Memory Construction and Retrieval for Personalized Conversational Agents

### 05 Retrieval RAG and Indexing

- From RAG to Memory: Non-Parametric Continual Learning for Large Language Models
- H-MEM: Hierarchical Memory for High-Efficiency Long-Term Reasoning in LLM Agents
- H-Mem: Hybrid Multi-Dimensional Memory Management for Long-Context Conversational Agents
- Look Back to Reason Forward: Revisitable Memory for Long-Context LLM Agents
- MemGuide: Intent-Driven Memory Selection for Goal-Oriented Multi-Session LLM Agents
- MemoRAG: Boosting Long Context Processing with Global Memory-Enhanced Retrieval Augmentation
- SeCom: On Memory Construction and Retrieval for Personalized Conversational Agents

### 06 Tool Use and Proactivity

- Evaluating Personalized Tool-Augmented LLMs from the Perspectives of Personalization and Proactivity
- MemGuide: Intent-Driven Memory Selection for Goal-Oriented Multi-Session LLM Agents

### 07 Working Memory Factuality and Safety

- Improving Factuality with Explicit Working Memory
- Look Back to Reason Forward: Revisitable Memory for Long-Context LLM Agents
- MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents

## 新论文入库规则

1. 新论文先进入 Zotero 的 `Akashic` 父 collection。
2. 每篇论文必须有且只有一个主分类；如果主题明显交叉，可以加入 1 到 2 个副分类。
3. 主分类按“最适合指导 Akashic 当前工程优化的问题”决定，而不是只看论文标题。
4. 标签至少保留 `akashic-paper-library`、年份、会议/来源、主题标签。
5. 如果论文只是工程参考但不是 2025/2026 顶会主会论文，放入 README 的“候选扩展”，不要混进核心 BibTeX。
6. PDF 统一直接复制到本地 `pdfs/` 目录，文件名使用 BibTeX key，例如 `huang_etal_2026_mempal.pdf`。
7. 更新 Zotero 分类后，同步更新 `classification-manifest.json` 和 `pdfs/download-manifest.json`，保证本地文档、PDF 副本和 Zotero 状态一致。

## 质量检查

当前基线：

- `Akashic` 父 collection：23 篇。
- 子分类覆盖：23 篇。
- 未分类论文：0 篇。
- 本地 PDF 副本：23 篇。

后续新增论文时，最重要的检查是：父 collection 中不能存在没有进入任何子分类的论文。
