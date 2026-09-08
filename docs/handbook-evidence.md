---
layout: default
title: 手册源码快照与证据口径
permalink: /handbook-evidence.html
---

# DialogPilot 架构边界：来源与证据

> 核查日期2026-09-08。写作起点59dd33c，最终合入当日新增的ceeab4e扩库报告；代码快照以[逐文件SHA-256]({{ '/assets/handbook/source-snapshot.json' | relative_url }})为准。文档发布到main/docs，应用开发分支没有合并到main。源码链接指向快照基点的GitHub文件；标记working_tree_modified的文件还检查了未提交修改，其hash保存在manifest，链接展示的是最近提交版本。

## 1. 怎样使用证据

一项主张必须说明四件事：对应哪个实现/配置；数据、split和预算是什么；真正执行了哪些阶段；尚未证明什么。API返回Completed、内部Verifier通过、pytest通过和独立业务正确是不同证据。历史报告正文的快照保存在本手册assets中，防止并行实验继续更新后旧数字失去上下文。

本轮不运行LLM、训练、RAG或业务恢复实验，只做源码阅读、文档/链接校验、站点构建与浏览器检查。页面的技术原理与当前实现是解释，不是新实验结论。源码变化后应重新核对manifest，而不是长期把本页称“实时最新”。

## 2. 当前可讲的真实实验

| 范围 | 已观察结果 | 不能推导的结论 |
|---|---|---|
| 三套同预算融合对照 | Doc开发300完整覆盖66.33→69.33%；MTRAG已消费35片段Recall33.19→44.14%；Wix已消费20文章Recall55→67.5% | 三套新鲜盲测、论文SOTA或业务准确率 |
| 上下文压缩开发8×2 | 16/16结构与语义维度通过 | 任意长会话零损失、独立人工E2E正确率 |
| 中文混合链路历史80题 | 可见来源85→90%；自动PASS61→76，仍有误通过 | 95%真实业务准确率 |
| 中文复杂小库40题 | 最终完整覆盖62.5→77.5%，17chunks/candidate20 | 大库召回改善 |
| 中文扩库40题/6287文档 | 完整覆盖30→32.5%；两臂都OK的39题均12/39，救回3误伤3 | 改写已证语义净收益、metadata硬过滤已验收 |
| Encoder冻结权重独立复核 | 中英文各校准/评测35，均5/35且只预测退款，未启用 | 已实现高质量线上快速路由 |
| Reranker小规模微调 | Top5 24/40→24/40，无收益 | 微调有效；目前仍暂停 |

报告：[rag-three-dataset-final-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-three-dataset-final-2026-09-08.zh-CN.md.txt' | relative_url }})、[ecommerce-rag-heldout-pair-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-rag-heldout-pair-2026-09-08.zh-CN.md.txt' | relative_url }})、[ecommerce-complex-rag-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-complex-rag-2026-09-08.zh-CN.md.txt' | relative_url }})、[domain-encoder-reviewed-trial-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__domain-encoder-reviewed-trial-2026-09-08.zh-CN.md.txt' | relative_url }})、[rag-reranker-finetune-2026-09-07.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-reranker-finetune-2026-09-07.zh-CN.md.txt' | relative_url }})。

## 3. 官方原理与数据来源

以下在2026-09-08核查。用于解释机制和数据定位，项目的选择及收益仍由本地源码/实验支持，不从官方示例推断本项目已经实现。

| 来源 | 支持什么 | 本项目如何使用 |
|---|---|---|
| [LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents) | create_agent、工具和middleware抽象 | Worker标准循环，应用决定业务合同 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | checkpoint、thread、恢复 | PG checkpointer与应用状态分工 |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | 中断与恢复、节点重入注意事项 | 审批等待与业务幂等须同时设计 |
| [PydanticAI Agent](https://pydantic.dev/docs/ai/core-concepts/agent/) | 类型化输出、工具、依赖接口 | 对比框架与受限grounded边界，非主运行引擎 |
| [Transformers分类](https://huggingface.co/docs/transformers/tasks/sequence_classification) | 序列分类训练/推理 | 当前domain encoder实现类型 |
| [SetFit](https://huggingface.co/docs/setfit/conceptual_guides/setfit) | 句表示微调与分类方式 | 可比较的替代范式，未声称采用 |
| [PostgreSQL全文检索](https://www.postgresql.org/docs/current/textsearch-controls.html) | tsvector、排序与查询 | 区分原生FTS与项目BM25 SQL |
| [pgvector](https://github.com/pgvector/pgvector) | exact/ANN、HNSW参数与过滤 | 解释索引，不冒充本项目性能数据 |
| [BGE-M3](https://huggingface.co/BAAI/bge-m3) | 多语、多表示检索模型 | 只按实际provider启用能力，不自动继承全部功能 |
| [BGE reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3) | query-document精排 | 本地精排路径，与LLM listwise区分 |
| [Doc2Dial](https://doc2dial.github.io/data.html) | 文档依据对话与grounding | span/完整条款评估 |
| [MTRAG](https://github.com/IBM/mt-rag-benchmark) | 多轮检索与生成评测 | collection passage与真实query分开 |
| [WixQA](https://huggingface.co/datasets/Wix/WixQA) | 产品支持问答、文章ID与语料 | 产品指引和文章检索；数据卡MIT，版本以manifest为准 |
| [τ工具交互benchmark仓库](https://github.com/sierra-research/tau2-bench) | 原生用户—工具环境与评分 | 项目τ³适配仍import tau2；具体实验上游revision由运行manifest界定 |

## 4. 参考页面与写作边界

写作颗粒度参考用户提供的[Affordance Runtime追问页](https://garrulus21yyx.github.io/affordance-runtime/interview-playbook.html)：短答后展开机制、追问和源码定位。这里只借用阅读组织，不迁移其DOM、Monitor、AndroidWorld或PydanticAI主运行时结论到DialogPilot。

手册有80个详细追问，经典知识解释用于帮助完整口述；实际是否启用、指标高低、API默认以源码快照与报告为准。简历页保留单独标注的模拟写作版本，不混入本页实测表。
