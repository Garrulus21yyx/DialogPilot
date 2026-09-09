---
layout: default
title: RAG 离线在线、选型与评测详解
permalink: /rag-study.html
---

# DialogPilot RAG 架构边界与评测讲解

> 2026-09-09同步至源码89feac2；最新状态含Metadata、标题切块与原生FTS验收，历史来源仍保留原日期。

> 本文把检索原理、实际代码和项目实验分开讲。当前主线仍有质量缺口，reranker 微调暂停；本文不是新实验，也没有把内部 verified 当作正确答案。回到[完整架构]({{ '/architecture.html' | relative_url }})或[详细追问]({{ '/interview-guide.html' | relative_url }})。

## 1. 为什么客服 RAG 比问一个 FAQ 难

政策通常有基础条件、例外、渠道和时间范围。“拆封还能退吗”可能要结合产品类别、是否质量问题、购买渠道及有效政策。若只召回“七日内可退”，答案看似相关却漏了限制。多轮里“那运费呢”还需要前文的产品、退货原因；“我的退款到账了吗”则是私有实时状态，必须查业务工具。

所以项目同时关注三个问题：完整问题是否形成；必要证据是否最终进入模型；模型是否按证据正确表达。只评 Recall 不知道生成是否漏条件；只评答案不知道好结果靠检索还是模型已有知识；只看内部核验无法发现裁判误判。

## 2. 离线入库：从原文到可激活索引

输入不是裸向量列表，而是原文及来源身份。SourceRevision 保存版本、校验和、适用信息；切块保留 chunk 与原文字符范围。文本、metadata、向量和词法项进入同一个 generation，generation 保存 embedding provider/model/digest/dimension、预处理版本、词法 ranker、距离度量和索引参数。索引准备完毕再切换 active generation，避免查询同时混进不同模型空间或未完整导入的内容。

当前 Knowledge 导入链在 `PostgresKnowledgeStore`：解析/切块 → 重用符合身份的已有向量或计算新向量 → register generation → write source/chunks → project → 建 HNSW → READY → activate_direct。这里是项目当前本地单主链，不应讲成已实现完整企业灰度发布平台。

删除和撤回不仅是向量表删行：source 的有效性改变后，缓存中的证据也应失效或在返回/发布前复验。否则数据库已有新政策，缓存仍引用旧政策。原始来源拥有有效性，缓存和检索投影不拥有另一份独立真相。

源码：[infrastructure/postgres_knowledge_store.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/postgres_knowledge_store.py)、[application/knowledge_source.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/knowledge_source.py)、[infrastructure/postgres_knowledge_source.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/postgres_knowledge_source.py)、[application/retrieval_generation_rebuild.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/retrieval_generation_rebuild.py)。

## 3. 切块怎么选，为什么不是越小越好

固定 token 切块容易复现，长度和重叠可控，但可能切断标题、条件和例外。结构切块按章节/段落组织，有助保留局部语义，但“结构保留”不等于理解条款的业务逻辑。当前示例配置是 structure_aware，预算 512 tokens、overlap 64；具体行为由 DocumentChunker 和导入参数决定。结构感知仍是文本处理，不能据此宣称复杂 PDF、扫描表格和跨页单元格已验证。

小块提高位置精度，但会拆散“只有在……除非……”；大块保留上下文，却增加无关内容、精排成本和最终预算占用。overlap 提高边缘信息被保留的机会，同时增加重复候选和存储。比较 chunk size 时必须固定最终输入 token，而不是让大块每题多喂一倍正文。

父子检索是小块召回、较大父块供生成；有益时能补上下文，但也可能把一个高分 child 扩成大段无关正文，挤掉另一条必要证据。项目做过 parent 内重检索等开发比较，未得到稳定收益的方案不采用。要证明优于 flat，应同数据、同最终5/2600预算，报告完整必要证据和额外噪声，而不是只看文章命中。

源码：[mcp/document_chunker.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/mcp/document_chunker.py)、[mcp/context_packer.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/mcp/context_packer.py)。历史选择记录：[rag-local-selection-results-2026-09-07.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-local-selection-results-2026-09-07.zh-CN.md.txt' | relative_url }})。

## 4. BM25、FTS 和 Dense 到底是什么

BM25 是词法匹配排序：稀有词贡献更大，同一词重复出现的收益逐渐饱和，文档过长会被归一化。单个查询词的主要项可写为：

```text
IDF(t) × tf(t,d) × (k1+1)
-----------------------------------------------
tf(t,d) + k1 × (1-b + b × len(d)/avg_len)
```

当前 PG BM25 SQL 使用 k1=1.2、b=.75，对授权范围内语料统计 N、df、tf、平均长度，再聚合各词贡献。中文先经过项目词法处理形成词项。它适合精确产品名、订单术语、编号和稳定政策词，但词面完全不同可能漏掉语义相关片段。PostgreSQL 原生 `ts_rank_cd` 则是另一套全文检索排序，不是 BM25；代码支持两种 ranker，Knowledge generation 当前声明的是 BM25。

Dense 用编码器将文本变为向量，以 cosine 等距离找语义邻居。它可能召回“退货邮费”和“寄回运费”这样的异词表达，但也可能混淆同产品不同条件、不同时间的政策。Embedding 不会自动成为条件逻辑引擎。因此词法精确性与语义召回互补，最终还需适用信息与核验。

项目有 hash_baseline 与 BGE-M3 两条显式 provider 选择。hash 保证轻量启动和确定性，不应被说成深度语义模型。BGE-M3 可提供多语语义表示；官方支持多种表示方式，不意味着本项目已全部启用 sparse/ColBERT。项目本地语义实验需在 manifest 指明实际 provider、权重版本、维度和预处理。[BGE-M3 官方模型卡](https://huggingface.co/BAAI/bge-m3)。

源码：[infrastructure/hybrid_retrieval_backend.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/hybrid_retrieval_backend.py)、[application/chinese_lexical.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/chinese_lexical.py)、[infrastructure/dense_embedding_factory.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/dense_embedding_factory.py)、[infrastructure/bge_m3_embedding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/bge_m3_embedding.py)。

## 5. HNSW 是什么，项目到底怎么用

HNSW 是 Hierarchical Navigable Small World，一种图结构近似最近邻索引。节点是向量，上层节点稀疏，用于远距离快速导航；向下逐层靠近目标，在底层维护候选并探索近邻。可以想成先在高速路上接近城区，再在街道里找目的地，但不是按业务类别建的树。

三个参数必须分清：M 控制每个节点的连接规模；ef_construction 控制建图时探索候选的宽度；ef_search 控制查询时探索宽度。提高它们通常用更多内存、构建时间或查询延迟换更好的近邻质量，不能承诺单调改善所有生产指标。

当前 Knowledge generation 标记 HNSW、cosine，建图参数 M=16、ef_construction=64，`create_generation_hnsw_index` 创建按 generation 限定的向量表达式索引。代码中的 generation 元数据不是 SQL planner 一定选该索引的证据，尤其查询带 tenant、generation、适用范围和额外排序条件。要用 `EXPLAIN (ANALYZE, BUFFERS)` 看实际路径，并与 exact 扫描比较。仓库没有在这里证明某个统一 ef_search 最优。

HNSW Recall@K 是“近似返回与精确向量 TopK 的一致性”，RAG Recall@K 是“返回与人工相关证据的覆盖”，二者不是同一个分母。即便 HNSW 完美复现 exact，embedding 也可能排错政策。过滤还可能使 ANN 候选不足；需测试不同过滤选择性、是否补足K和尾延迟，不能只测无过滤全库。[pgvector 官方索引与过滤说明](https://github.com/pgvector/pgvector)。

IVFFlat 则先把向量分成若干区域，查询探测部分区域，参数通常看 lists/probes；构建、内存和检索权衡不同。本项目没有完成 HNSW/IVFFlat 的同条件性能竞赛，选择 HNSW 是现有 PG 链路的实现方案，不是宣布它在所有规模更优。

源码：[infrastructure/hybrid_retrieval_backend.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/hybrid_retrieval_backend.py)、[infrastructure/postgres_knowledge_store.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/postgres_knowledge_store.py)。面试中不要承诺未实测的百万向量延迟。

## 6. 在线检索每一步的输入输出

```text
当前消息 + 相关历史 + 身份/范围
→ 主 Agent 形成完整 query（或 HISTORY 模式 QueryTransformer）
→ raw / standalone 等有效查询变体
→ 每种变体的 Dense / Lexical 候选
→ weighted RRF + 去重 + candidate_k 截取
→ reranker 返回完整候选的合法排序
→ ContextPacker 在 final_k / token 预算下选证据
→ EvidencePack + 实际 ToolMessage
→ Worker/compose 形成答案
→ 支持性/需求覆盖核验 + 引用/来源复验
```

`KnowledgeRetriever.retrieve` 先核对模型身份，再处理 query cache、candidate cache、rerank cache 和 pack cache。候选来源不可用、无证据、来源冲突、重复 ID、非法 provenance 都是类型化结果。reranker 若漏 ID、重复 ID 或伪造候选，会回退既有候选顺序并记 fallback。返回最终 pack 前再确认来源仍有效，处理查询期间来源更新的竞争。

系统注入身份和来源范围，模型仅在允许的业务过滤字段中表达条件。授权过滤和软 metadata hint 不同：前者决定是否可读，后者影响排序，不可用“低权重”表示禁止访问。

源码：[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/knowledge_retriever.py)、[application/knowledge_tool_contract.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/knowledge_tool_contract.py)、[infrastructure/knowledge_applicability.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/knowledge_applicability.py)。

## 7. 为什么选 RRF，权重怎么算

Dense cosine 与 BM25 分数分布不同，不能随意直接相加。RRF 使用排名而非原始分数：`score(d)=Σ w_i/(k+rank_i(d))`，某路没召回该文档就不贡献。k 越大，前几名的相对差距越平；k 较小更强调顶部排序。它减少分数校准负担，但也丢掉了“第一名领先多少”的信息。

项目要分开讲两组权重：Dense/Lexical 是召回算法融合；raw/standalone 是查询变体融合。当前前者默认 .5/.5、k10；后者在没有 expansion 时相当于 .25/.75。raw 为保留用户原始锚点，standalone 为多轮指代补全；这是已有方案，不是理论最优常数。参数实际来自启动环境和 Bundle，不能只看 `.env.example` 推断某次运行。

为什么从 .25/.75 调到 .5/.5？旧配置可能让词法独有候选填满最终池，Dense 较高排名也被截掉。三套原数据的固定预算重放均显示均衡方案有净收益，因此采用实用默认。但数据已消费、规模不一，不能说交叉验证证明了统一最优。动态权重会增加一个预测与校准问题，现有证据不足时先不引入。

| 数据/标注单位 | 固定样本 | 旧权重→均衡：最终覆盖 | MRR@5 | nDCG@5 |
|---|---:|---:|---:|---:|
| Doc2Dial：全部必要证据 case | 300开发 | 66.33%→69.33% | .5644→.5854 | .5895→.6128 |
| MTRAG：官方 passage | 35已消费 | 33.19%→44.14% | .4619→.5500 | .3365→.4312 |
| WixQA：文章 | 20已消费 | 55%→67.5% | .4958→.5142 | .4939→.5330 |

数据出自[rag-three-dataset-final-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-three-dataset-final-2026-09-08.zh-CN.md.txt' | relative_url }})。这里是项目离线对照，不是论文榜单复现；不能把三行覆盖率平均成业务成功率。

## 8. Rewrite 怎么选，怎样防止改写越改越错

基础选择有：直接使用当前句；拼接最近历史；生成 standalone query；多查询扩展；HyDE 先生成假想答案再向量召回。当前句便宜但会丢指代；拼接保留信息但噪声大；standalone 更聚焦但可能补错主体或条件；多查询增加召回也增加成本/噪声；HyDE 的假想内容不是事实，在强约束政策场景尤其需要谨慎。

项目真实 Agent 路径应由拥有当前任务上下文的 Agent 生成完整 query，标 RESOLVED 后不强制再写一遍。HISTORY 模式由 QueryTransformer 处理；扩展默认关闭。判断好 query 不是更长，而是保持主体、问题目标、否定、假设、渠道、时间和未知项。“如果坏了能退吗”不能改为“用户产品已损坏申请退款”。缺失条件应仍是未知，不能编日期填字段。

复杂中文开发对照给两臂相同历史：一臂直接拼接，一臂完整查询＋原句融合，避免拿“故意丢历史”当弱基线。小库40题精排Top5完整覆盖从25/40到31/40，救回7、误伤1；但17片段、candidate20覆盖全库，改善主要在最终多证据排序。另一个短文100题集没有改写收益，因此不能宣传“rewrite 必然提升”。

few-shot 也做过无示例、固定、动态检索示例对照。项目报告出现条件污染和无依据 policy_date，候选未采用。学习点是示例帮助格式和思路，也可能把样例事实迁移到用户身上；必须检查完整工具参数，而不只是 query 字符串。

源码：[mcp/query_transformer.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/mcp/query_transformer.py)、[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/application/knowledge_retriever.py)。证据：[ecommerce-pure-rag-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-pure-rag-2026-09-08.zh-CN.md.txt' | relative_url }})、[ecommerce-complex-rag-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-complex-rag-2026-09-08.zh-CN.md.txt' | relative_url }})、[planning-evidence-selection-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__planning-evidence-selection-2026-09-08.zh-CN.md.txt' | relative_url }})。

## 9. 精排、去重、打包为什么仍会丢证据

Bi-encoder 可预计算文档向量，适合大范围召回；cross-encoder 联合读取 query 与 candidate，细粒度相关性通常更适合小候选池，但每对都需计算。Listwise LLM 同时看候选排序，能表达较复杂关系，成本、延迟和输出合法性也更难控制。项目有本地 `bge-reranker-v2-m3` 与 listwise 适配，某次实验必须明确使用哪条。[本地精排模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)。

单片段相关性高不等于证据集合充分。Top5 可能全是相近“退货期限”，却漏“质量例外”和“谁承担运费”。去重能减少重复，但过度去重也可能删掉同文不同必要条款；增加候选池也可能让相似高分片段挤占位置。ContextPacker 在5片段/2600预算下选取，必须衡量实际 pack 和 wire，而非把 rerank Top5 当模型最终看见的内容。

项目历史实验扩大20→80候选时，候选完整覆盖223→244，却使pack完整覆盖208→207；因此没有采用扩大池作为最终修复。父内重检索也出现救回3、误伤7而不采用。这些是特定已消费开发集的负结果，不能推导所有大K或父子检索无效，但能说明本项目没有只挑漂亮数字。

源码：[infrastructure/local_knowledge_reranker.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/local_knowledge_reranker.py)、[infrastructure/knowledge_retriever_adapters.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/infrastructure/knowledge_retriever_adapters.py)、[mcp/context_packer.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/mcp/context_packer.py)。历史台账：[rag-optimization-status.md]({{ '/assets/handbook/evidence/plans__rag-optimization-status.md.txt' | relative_url }})。

## 10. 三个外部数据集为什么选，怎么适配

| 数据 | 业务中要验证什么 | 参考答案/标签怎么用 | 项目最容易讲错的边界 |
|---|---|---|---|
| Doc2Dial | 多轮文档依据、必要条款及文档内位置 | dialogue grounding references 映射原文 span，再映射当前chunk | 命中文章不代表所有条款已覆盖；本地300开发用100文档库，不是全库官方榜单 |
| MTRAG | 历史依赖、指代、追问后的目标保持 | 按 collection 的 passage qrel；官方 rewrite 与真实Agent query 分开 | 用官方完整query的成绩不能证明自己Agent改写正确 |
| WixQA | 产品帮助中心、步骤问答、跨文章支持 | 文章ID及参考答案；项目文章召回与论文Context Recall分开 | article Recall不是LLM judge的答案信息覆盖，也不代表订单工具成功 |

Doc2Dial 的 grounding 对应文档来源，适合检查政策依据；MTRAG 更直接暴露多轮检索问题；WixQA 的真实产品支持资料适合文章与操作指引。选择是对能力切面的映射，不是“三个名字越多越高级”。三者都不能替代中文电商私有订单与审批测试。

导入必须保留原始ID、版本、来源和hash，重切块要映射回gold单位。MTRAG Cloud collection 的本地真实入口使用72,439 passages；WixQA 本地固定快照6,221文章，不把论文另一版本的数量强行改到本地产物上。模型不能读任务的gold；scorer才读取参考资料。

来源：[Doc2Dial](https://doc2dial.github.io/data.html)、[MTRAG](https://github.com/IBM/mt-rag-benchmark)、[WixQA 数据卡](https://huggingface.co/datasets/Wix/WixQA)。项目协议：[rag-official-benchmark-protocols-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-official-benchmark-protocols-2026-09-08.zh-CN.md.txt' | relative_url }})。

## 11. 中文电商自建集：按实验阶段解释结果

混合完整链路历史集包含政策知识、订单状态、真实Agent选择和生成核验。120模拟用例/60规则族中的80题，模型可见来源68→72题，自动Flash PASS61→76，但审计发现至少6→12个PASS有问题，不能称95%业务准确率。

短资料纯RAG100题只比较查询与检索，不运行业务写入。来源Recall已经100%，改写并没有带来更好的排名，说明这一任务难度不足以支持复杂政策泛化结论。

复杂集按三处必要证据构造120题/30族，40开发与80留存。最初小库只有17chunks，Top5完整覆盖62.5%→77.5%；扩到6287文档/11590chunks后，40开发题完整覆盖30%→32.5%，两臂都OK的39题均12题完整。不能把环境可用性改善写成改写语义收益。

后续E06贯通Metadata与来源生效时刻，完成120题双臂对照。其中80题完整可见覆盖32.5%→77.5%，候选完整82.5%→97.5%，错误范围来源73→0题；但仍有16题精排Top5丢失和2题超时。这80题已经消费为回归，不再叫锁定未运行。用其中实际证据生成答案后，严格完整且有据57/80（71.25%），参考与审阅并非独立人工，因此同样不是线上准确率。

Markdown标题切块是后续候选：40开发题在离线5秒SQL预算下，可见完整33→37题，但候选完整40→40，收益主要发生在片段边界及最终证据保留；存在局部条款退步和gold歧义。默认未切换，不能省略预算条件或写成全量采用。

最新PG原生FTS候选在另一批Wix20题上，精排打包文章Recall67.5%→60%，完整12→11题，未达到采用门槛。中文40回归题750ms预算下BM25全部不可用、原生40题可执行，原生wire完整33/40；wire与历史5秒预算结果逐份一致，说明本轮支持的是可用性证据，不能把0→82.5%写成排序质量提高。全局默认仍未切换，微调仍暂停。

报告：[docs/ecommerce-scoped-rag-2026-09-08.zh-CN.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/ecommerce-scoped-rag-2026-09-08.zh-CN.md)、[docs/ecommerce-answer-quality-2026-09-09.zh-CN.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/ecommerce-answer-quality-2026-09-09.zh-CN.md)、[docs/rag-header-retrieval-pair-2026-09-09.zh-CN.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/rag-header-retrieval-pair-2026-09-09.zh-CN.md)、[docs/rag-native-fts-acceptance-2026-09-09.zh-CN.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/rag-native-fts-acceptance-2026-09-09.zh-CN.md)。

上述是不同实验阶段，数据粒度、预算和执行环节不同，不能拼成同一个版本的端到端提升。

## 12. 指标怎么计算，面试要能举例

Recall@K 是 relevant 集合中被topK覆盖的比例；Precision@K 是返回集合中相关项比例。MRR@K 看第一条相关项位置，第一名相关就得1，即使另外两个必要条款没找齐。nDCG@K 根据排序位置折扣相关性，再除理想排序；相关性标签和IDCG候选全集必须固定。

完整必要证据覆盖率是逐题布尔指标：该题所有必需证据单元都进入指定阶段才为1。若一题需A、B、C，返回A、B，则单元Recall=2/3，但完整率=0。文章Recall若A、B在同文，甚至会掩盖C缺失，因此不能把粒度不同的指标互换。

业务通过率需定义必须执行的动作、合法参数、最终环境状态、用户约束和异常结果，不能只看回答字符串。Token摊销成本可以定义为两版各自总输入＋输出Token除成功任务数，分子应包含失败尝试、改写、精排、核验与修订；必须与“每请求平均Token”分开。

配对实验同题记录 old/new。成功→失败叫误伤，失败→成功叫救回，净提升=(救回-误伤)/题数。按对话/规则族split，统计置信区间时也应考虑族内相关；三种措辞不是三名独立用户。重复运行要记录seed与模型非确定性，不能把temperature0当严格可复现承诺。

## 13. 如何从 Trace 定位损失，而不是盲目调参

检查顺序：全库是否含gold → 当前chunk能否完整容纳gold → Dense/Lexical各路是否召回 → 并集是否有 → 融合截取是否丢 → 精排是否降到K外 → pack是否因预算丢 → ToolMessage是否序列化/安全处理后丢 → 生成是否使用 → 核验是否漏判。

如果gold不在语料，调reranker无效；如果候选有而Top5没有，优先看排序与集合覆盖；如果wire里完整而答案遗漏，问题在上下文使用/表达/核验，不能再称召回问题。某轮工具被错误隔离也会表现为“RAG无用”，必须从实际模型输入确认机制。

预注册要写 gap、假设、数据/split、固定变量、预算、指标、采用条件。示例：“固定20候选、5片段/2600tokens，比较.25/.75与.5/.5；采用需开发净收益、跨集无明显回退及独立留存通过”。若需改模型和chunk，应分阶段做，不然不知道是哪一个因素带来收益。

Trace 存 policy fingerprint、generation、source hash、实际query与候选排名；语义审阅用脱敏问题、实际可见证据、答案和缺失条件。报告应保留失败、环境中断和未执行项。实验闭环最终是改动是否在同条件下减少目标失败，并在未见样本保持，而不是调到某次PASS。

## 14. 可复现入口与安全的阅读顺序

先读脚本参数和manifest，再决定是否执行。下列是定位入口，本轮没有重跑这些付费或GPU实验：

```text
scripts/summarize_rag_three_datasets.py   已有三套报告的离线汇总
scripts/run_ecommerce_complex_dev.py     复杂中文开发双臂；会用模型/数据库
scripts/report_ecommerce_complex.py      对已保存复杂中文结果评分
scripts/run_context_compaction_eval.py   摘要模型与语义judge评测
scripts/run_tau3_full.py                 真实应用与官方环境交互
```

扩库、holdout、重训必须使用新的输出目录并遵守各自计划。当前reranker微调暂停；已有微调40条验收Top5 24/40→24/40，没有收益，不应编成成功优化。项目面试最有说服力的答法是：“我知道哪一级提高了，也知道它没有证明哪一级。”
