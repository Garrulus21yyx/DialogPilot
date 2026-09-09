---
layout: default
title: 手册源码快照与证据口径
permalink: /handbook-evidence.html
---

# DialogPilot 架构边界：来源与证据

> 核查日期2026-09-09，源码冻结于 `89feac2`。主规划职责调整由 `d8e8933` 交付，最新 task22 报告随89feac2保存。网页发布在main/docs，开发应用代码未合并到main。源码链接与[逐文件SHA-256]({{ '/assets/handbook/source-snapshot.json' | relative_url }})固定到该版本；工作区仍在并行修改的文件仅记录身份，不据此新增已实现结论。上一轮快照另存[2026-09-08归档]({{ '/assets/handbook/source-snapshot-2026-09-08.json' | relative_url }})。

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
| 早期中文扩库40题/6287文档 | 完整覆盖30→32.5%；两臂都OK的39题均12/39，救回3误伤3 | 改写已证语义净收益、metadata硬过滤已验收 |
| Encoder冻结权重独立复核 | 中英文各校准/评测35，均5/35且只预测退款，未启用 | 已实现高质量线上快速路由 |
| Reranker小规模微调 | Top5 24/40→24/40，无收益 | 微调有效；目前仍暂停 |

| Metadata贯通80题 | 完整可见32.5→77.5%，错误范围来源73→0题；16题Top5损失、2题超时 | 当前仍未消费的盲测、业务完成率 |
| 同80题答案评估 | 严格完整有据57/80；参考与审阅非独立人工 | 生产准确率、独立人工Gold |
| Markdown标题切块40开发题 | 离线5秒SQL预算，wire完整33→37题，候选完整40→40 | 默认已采用、750ms预算性能通过 |
| 原生FTS另一批Wix20 | pack文章Recall67.5→60%，完整12→11题，候选不采用 | 更快就代表质量更好 |
| 原生FTS中文40回归 | 750ms下原生40可执行、wire完整33/40；BM25全不可用，与历史5秒wire一致 | 排序质量0→82.5%的提升 |
| 主Agent职责收缩后的task22 | d8e8933，读取与提案准备发生，无地址写入；ENV/ACTION/ALL均0，无评分器错误 | 审批展示与续接已闭环、泛化成功率 |

报告：[rag-three-dataset-final-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-three-dataset-final-2026-09-08.zh-CN.md.txt' | relative_url }})、[ecommerce-rag-heldout-pair-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-rag-heldout-pair-2026-09-08.zh-CN.md.txt' | relative_url }})、[ecommerce-complex-rag-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-complex-rag-2026-09-08.zh-CN.md.txt' | relative_url }})、[domain-encoder-reviewed-trial-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__domain-encoder-reviewed-trial-2026-09-08.zh-CN.md.txt' | relative_url }})、[rag-reranker-finetune-2026-09-07.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-reranker-finetune-2026-09-07.zh-CN.md.txt' | relative_url }})。

### 本次结构变更与证据对应

- 主 Agent 只读取证、知识查询、领域委派与原生对话；新写操作准备由领域承担。见[职责边界](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/conversation-responsibility-boundary.md)。
- requested_information 只投影模型需要的字段名与说明；私有任务绑定仍由运行时保存。见[输入投影](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/pending_input_view.py)。
- requested_objective、observed_segment、pending_actions 和 turn_execution 共同描述回答范围；作者与核验使用同一快照。见[回答组装](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/response_assembly.py)。
- task22 为已消费开发任务，offset17、seed300、80步预算；误选task29的中断不计入。第三轮仅准备账户修改，但审批展示受阻，之后核验schema失败，用户撤回。详见[完整运行结论](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/artifacts/eval/tau3-task22-scoped-conversation-corrected-2026-09-09/REPORT.md)。

源码回归与组件模型探针提供各自范围的证据；本次没有重跑这些实验。未提交的进一步审批发布修改仍在工作区，不能混进89feac2已发布设计。

### 本次报告原文快照

- [docs/conversation-responsibility-boundary.md]({{ '/assets/handbook/evidence/docs__conversation-responsibility-boundary.md.txt' | relative_url }})
- [docs/ecommerce-scoped-rag-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-scoped-rag-2026-09-08.zh-CN.md.txt' | relative_url }})
- [docs/ecommerce-answer-quality-2026-09-09.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-answer-quality-2026-09-09.zh-CN.md.txt' | relative_url }})
- [docs/rag-header-retrieval-pair-2026-09-09.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-header-retrieval-pair-2026-09-09.zh-CN.md.txt' | relative_url }})
- [docs/rag-native-fts-acceptance-2026-09-09.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-native-fts-acceptance-2026-09-09.zh-CN.md.txt' | relative_url }})
- [artifacts/eval/tau3-task22-scoped-conversation-corrected-2026-09-09/REPORT.md]({{ '/assets/handbook/evidence/artifacts__eval__tau3-task22-scoped-conversation-corrected-2026-09-09__REPORT.md.txt' | relative_url }})

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

手册有130个详细追问，经典知识解释用于帮助完整口述；实际是否启用、指标高低、API默认以源码快照与报告为准。简历页保留单独标注的模拟写作版本，不混入本页实测表。

## 5. 框架与后端专题补充

新增Q93—Q120覆盖LangChain/LangGraph基础、观察进展与硬预算、FastAPI异步/生命周期/鉴权、Redis缓存与事务、PostgreSQL锁/幂等/Outbox/索引/慢SQL。原理与源码一起解释；缓存雪崩方案、分布式锁和生产限流等候选知识不冒充已部署功能。源码仍固定89feac2，补充文件身份已加入manifest。

## 6. 简历与当前项目的双向校准

已对照用户上传PDF的DialogPilot五条经历，补充逐条口述、源码机制、验证与追问。没有上传PDF或个人联系方式，也没有混入其他项目。旧“100题68%→84%”模拟版本已从讲述页替换为当前PDF主线及逐项证据状态，保留原resume锚点。

简历32%/41%沿用此前模拟口径；50题76%本轮在相关报告/summary/manifest范围未找到匹配证据。三套检索与中文80题的实际指标保留各自范围，不因此一并标为模拟。所有对应关系见[简历指标表]({{ '/project-pitch.html#resume-metrics' | relative_url }})。

补充实现固定于[61a9b88](https://github.com/Garrulus21yyx/DialogPilot/commit/61a9b887c5d922e6d7c553507cf01442c5c5c4c8)：未送达审批恢复和失败进度投影。操作集合与确定性批准范围卡属于正在验证的工作区方案，未作为已发布架构。文件身份见[增量快照]({{ '/assets/handbook/resume-alignment-snapshot.json' | relative_url }})。本次没有新跑模型或业务基准。
