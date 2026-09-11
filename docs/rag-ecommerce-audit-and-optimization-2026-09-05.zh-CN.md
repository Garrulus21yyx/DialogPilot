# 智能电商客服 RAG：现状审计与量化优化方案

审计日期：2026-09-05。范围：当前工作区的知识库导入、检索、排序、证据和答案边界，以及历史评测产物。状态：**审计与实验设计；效果改善尚未实现或验收**。本轮不修改业务实现，不运行付费模型，不推送用户改动。

## 1. 结论先行

优先级是：**修复生产入口的对话上下文传递 → 统一召回与精排所理解的问题 → 修复精排证据可见性 → 验证有界文档内 child 检索 → 在真实入口做同协议端到端评测**。

当前不是缺少某篇论文推荐的组件，而是不同组件、运行入口和评测实验没有完全共享同一份语义输入和验收合同。增加 overlap、HyDE、更多层级或更大模型，不能自动补上这些边界。

现有证据只能说明公共英文客服检索的局部能力。尚无足够证据声明“中文电商客服答案正确率达到某个水平”。

## 2. 哪些没有 push

首次 fetch 后，分支 `feat/customer-service-target-architecture` 的 HEAD 为 `2a2030e`，与 upstream 差异为 0/0，当时有 43 个已跟踪文件修改。审计期间另一个工作流完成了框架 Agent 统一提交，随后再次 fetch，HEAD 为 `fa1c21b572ef3468450dcbbe1f07f2d56e915544`，仍为 0/0。**没有积压的未推送 commit；主要未推送内容是未提交的工作区改动。** 本任务没有执行提交或推送。

最终复核 HEAD 又前进到 `18aa67a`，ahead/behind 仍为 0/0，已跟踪文件修改数为 41。工作区在并行变化，文件数量是时间点快照，不应当作永久状态。完整路径及暂存/未暂存状态见 `artifacts/audit/rag-ecommerce-2026-09-05/git-status.txt`。

| 改动组 | 主要文件 | 核查结论 |
|---|---|---|
| RAG 检索策略与查询扩展 | `core/rag_policy.py`、`application/knowledge_retriever.py`、`infrastructure/knowledge_retriever_adapters.py` | 工作区改动；Raw/Standalone/有界扩展及权重、缓存身份相关迁移 |
| Metadata 路由与 BM25 | `application/hybrid_retrieval.py`、`infrastructure/hybrid_retrieval_backend.py`、`infrastructure/postgres_knowledge_retriever.py` | 工作区改动；语义 scope 加权路由与 global 回退、真实 BM25 路径 |
| 来源、上下文检索文本及投影 | `application/knowledge_source.py`、`infrastructure/postgres_knowledge_source.py`、`infrastructure/postgres_knowledge_store.py`、`infrastructure/postgres_retrieval_projection.py` | 工作区改动；source metadata revision、dense/lexical 表示一致性 |
| 必须配套的数据库迁移 | `migrations/versions/20260903_0030_knowledge_contextual_bm25.py`、`20260903_0031_knowledge_source_metadata_revision.py` | 未跟踪；不能只提交调用新字段的代码而漏掉迁移 |
| 生产链对齐评测 | `evaluation/postgres_rag_production_heldout_eval.py`、`evaluation/rag_production_heldout_artifacts.py`、两个 production heldout 运行/汇总脚本及测试 | 未跟踪；156 条报告和 runner 未随当前远端完整发布 |
| 本地实验副产物 | 大量 `artifacts/eval/`、`data/training/`、分类实验、临时候选、`docs/_site/` | 有大量未跟踪内容；需要分清可复现输入、报告、训练缓存和生成站点，不适合整目录无差别提交 |
| 框架 Agent 统一 | `fa1c21b` 对应运行器、middleware、测试等 | 审计期间已提交并推送，不再计入未推送清单 |

本地 BGE-M3 三路检索评估实现及引用的 v2 heldout 报告已有跟踪记录；它们存在于仓库，不代表已接入默认在线 PostgreSQL 三路召回。发布时应按“来源/索引迁移 + 检索 owner/调用方 + 测试 + 可复现实验记录”组织可独立审核的完整变更。

## 3. 当前链路到底做了什么

| 阶段 | 当前实现与 owner | 能力边界 |
|---|---|---|
| 导入 parsing | `mcp/source_document.py`，API knowledge upload | 仅 text/markdown/json 的规范文本；没有完整 PDF/HTML/OCR/表格解析链 |
| 切分 | `mcp/document_chunker.py` | 默认 structure-aware 512/64；字符区间可追溯；token 是估算值；末段寻找句号/空白等切点，Markdown 标题作为 section path，并非严格的章节/表格原子切分 |
| Metadata | `SourceRevision`、`KnowledgeChunkProjection` | 有 source/revision/checksum、locale/product/region 等底层字段；公开导入 DTO 不提供逐文档商业适用范围，直接导入的 region 固定 local、product 是 store 级配置 |
| 检索文本 | `application/knowledge_retrieval_text.py` | 标题、section path、正文及可用产品/地区形成派生检索表示；原文与 offsets 仍是证据权威 |
| 向量化 | `infrastructure/dense_embedding_factory.py`、`bge_m3_embedding.py` | 支持本地 BGE-M3；未显式配置时 factory 默认 hash baseline，不能仅凭安装模型判断实际服务正在用 BGE-M3；需读取运行实例 manifest 验证 |
| 在线 query | `KnowledgeRetriever`、`QueryTransformer` | Raw + 可用 Standalone + 最多 2 个扩展；Standalone 接受最多最近 8 条字符串历史；当前主要依赖模型补全，缺确定性带角色最近问答投影 |
| 首阶段召回 | `PostgresKnowledgeCandidateSource` / `PostgresHybridBackend` | Dense + BM25；可带 source_type/region 提示的附加路由；不是本地实验的 Dense + learned sparse + BM25 |
| 粗排/融合 | `mcp/rank_fusion.py` | 加权 RRF，默认 k=10；dense/lexical=.25/.75，raw/standalone/expansion=.2/.6/.2；每路及最终候选有 Top-20 截断 |
| 精排 | `mcp/result_reranker.py` | LLM listwise，候选短 ID 的严格排列；输入每条正文前 1,200 字符；目前从 retriever 收到 raw query |
| Packing | `mcp/context_packer.py` / `EvidencePack` | Top-5，约 2,600 token 预算；当前没有生产 parent 内 child 二次检索或动态层级扩展 |
| 答案 | `GroundedAnswerGenerator` 与 Target Agent/`ResponseAssembler` | 独立 grounded 模块支持 answered/insufficient/conflicting 和引用 ID 校验；Target 是另一条候选答案/组合链，不能用前者的测试替后者证明语义忠实 |

“rrd”在本次追踪中没有对应的独立算法 owner。若指多路排名融合，这里的准确名称是 **RRF**；它聚合名次，不判断证据是否真的支持答案。这里还存在两种 parsing：离线来源解析，以及在线模型输出 JSON/工具调用的结构解析。两者要分开验收。

## 4. 根因模型与需要修复的正向合同

### 4.1 对话语义没有沿生产链完整传递

证据链：`TargetConversationManager.execute_prepared` 的 execution context 把历史放在 `recent_relevant_turns`；`TargetToolExecutor` 传给 ToolManager 的是 `trusted_context`；`api/main.py::_knowledge_tool_handler` 读取 `context['query_history']`，默认空。已追踪路径中没有对应桥接。直接执行知识工具时，不能依赖 Agent 恰好把问题改写完整来弥补这一缺口。

此外，`KnowledgeRetriever.retrieve` 已构造 standalone 与 variants，但调用 reranker 时仍传 `request.query`。因此即使召回补全了“不是”对应的对象，精排仍可能只看到“不是”。本地实验的 `deterministic_query(user_history)` 又明确使用 `history[::2]`，这是另一处同类语义丢失；旧测试甚至要求排除 agent 文本。

正向合同：对话上下文 owner 提供有界、带角色/顺序的真实对话片段；Query owner 产生统一的 raw、resolved intent、约束和变体；召回与精排共享 resolved intent，同时保留 raw。否定、SKU、用户纠正、话题切换要保真。模型改写失败时保留确定性上下文投影。历史助理文字用于解释指代，不能成为政策证据。

修复面包括目标入口、知识工具适配、query/rerank port、缓存 key、版本指纹、离线 evaluator 和验收测试；不要只在评测器拼一个更长 query。

### 4.2 检索命中与精排看见证据是不同边界

症状是 reranker 净收益很弱；可确认机制是输入正文统一 `[:1200]`，可能隐藏 chunk 后半段。**尚不能将既有全部精排失败归因于截断。**

本轮新做了纯本地静态诊断（无推理调用）：对所有来源 chunk，比较 gold span 在完整 chunk 中与任意 chunk 的前 1,200 字符中的可见性。

| 已消费数据 | Gold span 数 | 完整 chunk 包含 | 任一 1,200 字符前缀包含 |
|---|---:|---:|---:|
| Doc2Dial mini-dev，300 cases | 488 | 488 | 372 |
| 本地 60-case 历史 heldout | 109 | 109 | 78 |

这证明“全文 containment 100%”不足以证明精排输入无损；分别有 116 和 31 个 span 无法在任意完整前缀里呈现。但这是**语料可见性诊断，不是候选 Top-20 上的实际损失率，也不是新 reranker 得分**；本地 CrossEncoder 实验也不能直接套用 LLM 的 1,200 字符截断归因。

正向合同：rerank projection 的预算以实际模型 tokenizer 计算，显式记录截断与可见区间；支持全文有界输入，或在长 child 中生成保留来源映射的多个局部窗口，并按 child 聚合结果。query-resolved 与 full-text/窗口分别消融，确认哪个机制产生收益。原文不能被摘要替代为事实权威。

### 4.3 Parent-child 需要解决定位，不应机械扩大答案上下文

60 条本地实验的 4 个 Candidate miss 中，3 条正确文档已经排第 1，但证据 child 排很后；这支持“命中文档后局部重检索”的候选假设。156 条另一协议中，31 个 candidate miss 里 25 个连正确文档也没达到该边界，另外 6 个属于文档命中但完整证据未命中。parent-child 不能单独解释或修复全部 31 个失败。

最小候选设计：保留全局 child 路由；从全局命中聚合少数 document/section，再在相同授权 scope/revision 内检索 children；候选按稳定 ID 去重后精排。Dev 起始预算可设 parent<=3、每 parent<=4 个补充 child、总精排池<=32；这些是**待选实验参数**，须与 flat-32、flat-20 同时比较，并补充固定时延/成本比较，不能把更大预算的收益全部算到层级上。

先使用已有文档关系，不先造摘要树。最终默认仍返回少量 child；只有指代、表格标题、局部前提缺失时补 section/window。Parent 不一定有用：短 FAQ 常为一个 chunk；无条件升父级会占预算并挤掉其他问题的证据。历史 hierarchy 实验已记录这种反例，不能直接恢复曾被拒绝的配置。

### 4.4 Metadata 下层支持与真实可用数据仍有距离

真实电商需要来源、店铺/平台、地区、语言、商品类目/SKU 适用范围、有效期、版本、公开性等。现在 schema 有部分字段不代表导入端、query 消费端和过滤端已完成它们的全链路。

正向合同：来源导入边界接收并校验商家提供的适用范围与版本；revision owner 管理有效性及替代关系；索引与证据读回都受同一版本/授权约束。已验证的店铺权限、地区和有效版本必须过滤；模型推测的主题/类别只能是软路由，缺失必要地区时明确询问或返回条件化政策。特别要区分格式 `source_type=markdown` 与业务 `policy_type=refund`。

不能把推测字段写入来源事实，也不能用 global fallback 绕过真实授权或适用性约束。当前 public-only 范围应保留，直到显式实现私有知识合同。

### 4.5 工具成功和引用格式成功不能代替答案正确

`GroundedAnswerGenerator` 的结构校验确认引用 ID 属于输入、状态组合合法，但不自动证明文本 entailment。Target 的 `ResponseAssembler` 还存在单结果直接透传分支。最终需要按真实入口验证：每条政策断言被对应版本的证据支持、所有独立问题被回答、无证据时不编造、业务操作结果来自工具回执。

金额、库存、订单状态、物流进度和退款执行结果应由业务 API 提供；静态 RAG 回答政策解释与操作指引。二者组合时保留来源类型和时间，不能用知识文档声称“你已经退款成功”。

## 5. 量化证据不能混用

| 历史协议 | Candidate 完整证据 | 最终 Top-5/packed | 精排净变化 | 未执行部分 |
|---|---:|---:|---|---|
| 本地 BGE-M3 Dense+Sparse+BM25，60 条官方 test | 56/60 = 93.3% | 51/60 = 85.0% | CrossEncoder 救 2、伤 2 | 真实客服入口、答案生成 |
| PostgreSQL + rewrite/expansion + Dense/BM25 + LLM，156 条 | 125/156 = 80.1% | 102/156 = 65.4% | 无精排101→精排102；救13、伤12 | parent/window、generation、judge |

156 条的 31 个候选失败、23 个 Candidate→Top-5 完整证据损失、0 个 packing 损失来自已验证 SHA-256 的历史报告。468/468 模型调用成功仅说明该次运行可用，不说明答案正确；此前 HTTP 402 的 fallback 批次已从有效汇总排除。

两套样本、query、召回后端及排序模型都不同，93.3% 与 80.1% 不是公平胜负。60 条上 1 个样本就是 1.67 个百分点，三路较两路多救 1 条不是稳定泛化证明。现有 heldout 均已被分析，只能作冻结回归或诊断，不能再次充当“未见数据”。

## 6. 没有企业语料时怎么评

分三层，分别报告，避免混成一个“电商准确率”。

1. **公共客服回归**：保留 Doc2Dial 检查多轮省略与精确 span；WixQA 补真实企业帮助中心和多文档问答。WixQA 官方提供 6,221 篇知识库、200 条 expertwritten、200 条 simulated 和 6,221 条 synthetic；优先以人工题作独立报告，合成题用于开发。其标注主要是 article_ids，不能伪称 passage/span 标注。[WixQA 数据卡](https://huggingface.co/datasets/Wix/WixQA)
2. **中文电商合同压力集**：先冻结一个明确标为模拟的店铺政策快照，覆盖退货、配送、保修、发票、优惠适用性；基于来源生成候选 QA，再由独立复核核对文档/证据、例外条件、否定和不可回答。保留 100–150 条 Dev、至少 200 条 sealed test 的起步规模；若没有人工核查条件，只能声称弱监督工程验收，不能声称真实商家上线质量。
3. **商品检索与业务工具分项**：Amazon ESCI 有商品相关性标签（Exact/Substitute/Complement/Irrelevant），适合 SKU/商品搜索模块，不是政策证据 benchmark。[官方 ESCI 仓库](https://github.com/amazon-science/esci-data) 动态业务通过受控工具 fixture/沙箱校验状态、权限、时间和回执，另报 API 任务成功率。

`hhdhh/ecommerce-sop-rag` 在本次 GitHub 树核验的 main `632c2624a1083c68199ca62d589e103ab78c9763` 根目录仍没有 docs/ 或 LICENSE。缓存会话对其模板重复、评测缺失的更详细判断，本轮未逐项重跑，不能升级为新测事实。它可以作为导入压力语料来源候选，但不能直接采用 README 数字作 heldout 成绩。[仓库](https://github.com/hhdhh/ecommerce-sop-rag)

切分单位至少是 conversation；模拟政策近重复、同模板改写题和同一政策版本族需分组防泄漏。共享检索 corpus 本身是允许的；若声称未见政策泛化，要把 policy family 也分组。WixQA 当前 subset builder 按 article ID 集合分组，不保证共享某一 article 的不同集合不跨 split，因此不能把它描述成 document-disjoint。

Span 不是全部检索评测的必要条件：文档级 qrel 可以测文档召回；child 选择需补 passage qrel 或真实 span/可验证答案证据。不可把“命中了 gold 文档的任意一段”当成“完整回答证据被命中”。

## 7. 从离线到答案的最小实验矩阵

所有组用同一数据、corpus、真实 embedding profile、原文证据与生产 owner。每次先冻结配置，再验收；Gold 只进入 scorer。基线重跑使用当前真实入口，并记录请求历史/过滤参数，避免 evaluator 自动提供线上拿不到的信息。

| 实验 | 只改变什么 | 主要观测 | 进入下一步的依据 |
|---|---|---|---|
| E0 对齐基线 | 当前 PostgreSQL 链；保留 raw-only 和现状 rewrite 基线 | 所有阶段召回、实际 query 输入、模型调用与 fallback、端到端结果 | fixture 与服务入口输入一致；hash baseline 不冒充真实向量 |
| E1 对话与query合同 | 带角色最近问答投影；精排 raw/resolved 对照；LLM rewrite 开/关 | 省略、否定、切话题、编号保持；R@20/5；改写有害率 | 原始意图保真，确定性回退可用，复杂度收益可解释 |
| E2 精排可见性 | 现状前缀 vs tokenizer 有界全文/窗口；同池同模型 | 候选证据可见率、Top-5 rescues/harms、成本 | 可见性与选择收益分别报告；不把解析成功当排序正确 |
| E3 局部child检索 | flat-20/flat-32 vs parent<=3、额外child<=12、union<=32 | 文档召回、正确文档条件下child召回、Recall@预算、P95 | 同池大小及同成本对照，不能以parent扩张掩盖预算变化 |
| E4 召回与精排模型 | Dense+BM25 vs 加 learned sparse；no-rerank vs BGE CrossEncoder vs 当前 LLM | R@20/5、nDCG、paired rescues/harms、增量时延 | Dev 选择；独立新样本确认；不默认三路或精排一定更好 |
| E5 离线分块与metadata | 结构切分 384/48、512/64；政策条款/表格原子块；正确scope与缺失scope | containment、表格关系、版本/地区误召回、索引大小 | 来源追溯与预算性质成立；中文电商分层收益明确 |
| E6 Packing/Generation | 冻结最佳检索；独立需求覆盖、必要窗口、结构化答案 | 证据保留、断言支持、问题完整性、拒答/澄清、工具事实、端到端延迟 | 真正用户可见答案通过；不只看召回或Judge均值 |

避免全排列。先 E0–E2 确认语义/证据边界，再选择 E3/E4；E5 必须在新电商语料暴露离线缺口时执行。复杂多需求问题才拆至多 3 个 requirement，并保留每需求证据来源；多个 gold spans 不必然等于多个独立需求。

每轮保存：代码 commit 加工作区 diff hash、dataset/corpus checksum、模型权重及 tokenizer revision、query/policy/index generation fingerprint、随机种子、硬件、原始逐例输出、评分器版本。性能拆为一次性建索引、冷启动、warm 单请求、并发；模型 API 失败与质量失败单独统计。

## 8. 度量与验收门槛

先锁定下列定义。完整证据 Recall 是逐 case 所需证据全部覆盖的比例；span 平均覆盖率另列。MRR/nDCG 不能替代多证据完整性。每阶段记录 entering/visible/selected/packed 的差异，再计算答案表现。

| 层 | 核心指标 |
|---|---|
| 解析/切分 | 可追溯率、字符区间一致性、条款与表格关系保留率、gold containment、tokenizer截断率 |
| Query | 角色顺序、否定/SKU/数值保留、纠正生效、rewrite harm、fallback次数 |
| Retrieval | all-evidence R@20/预算、文档召回、条件child召回、重复率、授权/有效版本错误 |
| Rerank | visible-evidence recall、all-evidence R@5、rescues/harms、排列校验失败率 |
| Packing | 已选证据丢失率、独立需求覆盖、冗余与token预算 |
| Generation | supported-claim precision、答案完整性、不可答/冲突识别、引用正确性、动态事实来自工具的比例 |
| 运行 | 各阶段 P50/P95、端到端 P95、超时/降级率、每问费用、吞吐、索引内存和构建成本 |

**以下为建议目标，不是已达成绩或论文保证**：在冻结中文电商测试上，候选完整证据召回争取>=95%，最终Top-5>=90%，可答题答案完整率>=85%，受支持事实断言>=98%；范围/版本/引用ID合同错误在验收集中为0。约200条仍不足以证明1%的稀有错误上界，必须附样本数和置信区间。

单个优化是否接受用同样本配对比较：报告 delta、bootstrap 95% CI（按 conversation/政策族聚类）、救回/误伤；小样本二元结局可用 exact McNemar。预先约定实际有效收益（如>=3pp）、关键分层非劣容忍度（如-2pp）及延迟预算。若区间仍跨过重要退化或收益界限，记为证据不足，不能宣称稳定提升。预算相同时也可接受召回非劣而成本显著下降的方案。

性质/状态测试包括：全部合法来源片段可回溯；任意否定/编号变换不丢约束；候选ID唯一、范围过滤在每路及局部扩展保持；更新版本后缓存不返回旧政策；精排输出必须为候选排列；任何预算下不伪造证据；生成中的未知引用、无证据、冲突与业务操作状态有明确类型结果。已消费反例只是见证，不能替代这些验收。

## 9. HNSW、层级树和后续优化的边界

代码会创建 generation 级 cosine HNSW 索引（默认 m=16、ef_construction=64），查询是否实际走它仍需运行数据库 `EXPLAIN (ANALYZE, BUFFERS)`。有索引定义不等于每条查询都使用；当前本轮未测活动服务执行计划。HNSW 的多层图用于近似向量检索，与 document/section/child 语义层级不同。[pgvector 官方说明](https://github.com/pgvector/pgvector#hnsw)

在1,469个chunk上优先测 exact 检索质量；数据扩大后再用相同过滤条件和query测 ANN 相对 exact 的 recall、P95和吞吐，单独控制 ef_search/过滤策略。HNSW 不能补丢失的问句，也不能让错误语义向量更准确。[pgvector 过滤与迭代扫描](https://github.com/pgvector/pgvector#filtering)

BGE-M3 官方提供 dense、sparse 与 multi-vector 能力，框架仍要显式持久化和检索相应输出；仅用 dense provider 不会自动启用另两路。[BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3) 标题/章节前缀的当前表示可借鉴 contextual retrieval，但与模型为每段生成解释性上下文不同，不能挪用其公开增益数字。[Anthropic Contextual Retrieval，2024-09-19](https://www.anthropic.com/engineering/contextual-retrieval)

先保持 flat structure-aware 作为默认。文档内多段主题相似、父文档易命中而child难命中时尝试两级检索。只有大量长手册、明确章节结构、多粒度问答且两级方案已受限时，才评估完整 hierarchy。当前证据不要求引入图数据库、摘要树、新检索平台或无约束 HyDE。

## 10. 本轮验证与限制

- 相关现有测试：53 passed，16 skipped；跳过项依赖未配置的测试 PostgreSQL。首次测试命令包含不存在的单文件名，未执行测试；纠正为实际测试模块后得到上述结果。
- 校验两个有效 production heldout component 的 predictions/report 及 combined report 共5个 SHA-256，全部一致。
- 重新加载 Dev 与历史60-case数据，执行数据checksum/来源span校验及精排前缀可见性诊断；无外部推理调用。
- 结果记录：`artifacts/audit/rag-ecommerce-2026-09-05/diagnostics.json`、`tests.txt`、`git-status.txt`。
- 独立新上下文只读审查确认四项边界事实；额外指出 query 变更必须同步 rerank 缓存身份，截断预算变更须覆盖通用 ToolManager 消费者，历史映射须覆盖 execute/resume。详见同目录 `independent-review.md`。
- 未验证实际运行服务使用的embedding/generation、HNSW执行计划、新优化后的准确率、中文真实商家质量及最终Target答案忠实率。
- 引用会话读取工具不可用，本轮使用用户提供的缓存内容与本地可核验文件；缺失会话部分未作已验证事实。

历史多次重开体现的共同问题是语义输入与投影/验收边界不一致；这包括架构接线缺口、开放的电商metadata合同、评测入口差异与历史文档口径。HTTP 402 是独立环境问题，不能与模型质量混为一谈。此次完成审计不恢复“RAG效果已闭环”状态；实施后仍需冻结配置的新样本和独立复核。
