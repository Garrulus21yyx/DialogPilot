# pg_textsearch BM25 扩展验收（2026-09-09）

## 结论

`pg_textsearch 1.4.0` 解决了现有自写 BM25 SQL 的主要在线计算成本，但本轮只通过了英文公共语料验收，没有通过中文复杂电商的 750ms 查询合同。因此不做全局替换，也不把扩展写成已经上线。

最终边界是按 `RetrievalGeneration.lexical_ranker` 选择词法实现：英文公共知识库可继续推进 `pg_textsearch`；中文复杂电商当前保留已验证的 `PG_FTS_ZH_V1` 候选。两者必须使用不同 backend/policy fingerprint，不能把同一个 `bm25` route 标签混用。

## 固定条件

- PostgreSQL 18、pgvector 0.8.6、pg_textsearch 1.4.0；本地组合镜像已验证两种扩展及 HNSW/BM25 查询可共存。
- WixQA：6,221 篇文章、11,167 个固定片段，本地 BGE-M3 Dense、Dense/BM25 各20、RRF `.5/.5, k=10`、融合20、本地 `bge-reranker-v2-m3`、最终5片段/2,600 token。
- 新100题由50条 expert-written 与50条 simulated 构成，按固定 SHA256 顺序选择，并排除之前40题。没有外部 API；生成100个本地 query embedding，复用全部文档向量。
- 中文电商：6,287篇模拟文档、40条复杂多条件问题，复用现有完整 query、结构切块、Dense、CrossEncoder、打包与 ToolMessage 入口，单条数据库语句预算750ms。失败保留在分母。

## WixQA 新100题结果

| 指标 | 现有自写 BM25 | pg_textsearch | 差值 |
|---|---:|---:|---:|
| 词法查询中位耗时 | 788.17ms | 2.70ms | -99.66%（约292倍） |
| 词法文章 Recall@20 | 66.67% | 61.67% | -5.00pp |
| 融合文章 Recall@20 | 83.00% | 83.00% | 0 |
| 融合完整文章覆盖 | 78/100 | 77/100 | -1题 |
| 融合 nDCG@5 | 49.08% | 45.76% | -3.32pp |
| CE/打包文章 Recall@5 | 69.00% | 68.00% | -1.00pp |
| CE/打包完整文章覆盖 | 62/100 | 61/100 | -1题 |
| CE/打包 MRR | 56.48% | 56.03% | -0.45pp |
| CE/打包 nDCG@5 | 57.21% | 56.48% | -0.73pp |

按预注册门槛，最终 Recall/完整覆盖下降1个百分点，没有超过2个百分点上限；速度显著超过“中位降低20%”门槛。扩展在英文候选层通过，但不能声称排序完全等价。此前另一批20题的最终 Recall@5 从67.5%降至62.5%，只有一题发生成功/失败变化，说明20题估计波动较大，也是补做新100题的原因。

该失败题中，正确 DNS 文章并未从扩展词法列表消失；一个正确 sibling 由现有 BM25 第10移至扩展第13，经过 RRF Top-20 边界后未进入 CrossEncoder 前5。不能用针对该题的权重或 Top-K 特判修复。

## 过滤与索引审计

在隔离库把 Wix 片段复制成10个租户、111,670行：

- 目标租户占10%时，BM25 Top-K 扫描过滤183行后返回20条，执行约1.0ms；0条跨租户结果。
- 不存在的租户由 `(tenant_id, generation_id)` B-tree 直接判空，约0.07ms。
- pg_textsearch 文档明确说明非索引条件可能成为 Top-K 后过滤并导致不足K。因此租户、generation 和适用性仍由数据库 WHERE 条件权威执行；不能先取未授权 Top-K 再由应用层过滤。

## 中文复杂电商失败与根因

三种查询形态均按40/40保留失败：

1. 全表 BM25 索引：历史 generation 累计约149k片段，长中文 unigram/bigram query 超过64词项缓存，加上版本/地区/渠道过滤，40/40超过750ms。
2. 最终 generation partial BM25 索引：缩小了 BM25 统计与 postings 范围，但规划器仍把复杂适用性 `EXISTS` 放在 Top-K 扫描后，40/40超时。
3. generation partial index + scope-first materialized CTE：先确定适用片段再逐行使用扩展 BM25 评分，仍40/40超时；完整检索中位约1,016ms。

同一中文40题的 `PG_FTS_ZH_V1` 已有结果为40/40可用，ToolMessage 完整证据覆盖82.5%、evidence unit Recall 94.17%、nDCG@5 89.65%，且0条错误 scope。该结果来自已消费模拟开发集，不等于新鲜线上准确率，但足以否决“为了 BM25 名称而全局替换已经可用的中文词法候选”。

## 所有权与后续接线

成熟扩展不等于直接给现有表加一个全局索引。正确所有权是：

1. generation builder 创建不可变 generation 时，同时创建该代 HNSW 与词法索引，并把实际 `lexical_ranker` 写入注册表。
2. backend 只执行注册表声明的实现；未知扩展、索引缺失或 fingerprint 不一致返回 typed `INVALID_CONTRACT`，不能静默回退成另一种排名。
3. scope owner 在 SQL 中执行租户、版本、撤回、地区、渠道、商品与生效时间约束；结果返回后继续做来源复验。
4. 英文 `PG_TEXTSEARCH_BM25_V1` 与中文 `PG_FTS_ZH_V1` 分别验收、分别缓存和记录 trace。动态选择来自语料/索引合同，不由 Agent 临时决定。

本轮不修改生产默认。要推进英文 provider，还需把组合数据库镜像纳入 CI、实现 generation 索引生命周期、删除/退役清理、并在真实授权过滤下验证不足K和并发负载。中文若继续追求 BM25，需要验证中文专用 parser（如 zhparser）或减少 query term algebra，并使用新的冻结开发/验收划分；不能在当前40题上继续后验删词。

## 证据

- `artifacts/eval/pg-textsearch-wix40-2026-09-09/`
- `artifacts/eval/pg-textsearch-wix-fresh100-2026-09-09/`
- `artifacts/eval/pg-textsearch-ecommerce40-v3-2026-09-09/`
- `evaluation/docker/pg_textsearch.Dockerfile`
- `scripts/evaluate_pg_textsearch_wix.py`
- `scripts/evaluate_pg_textsearch_wix_fresh.py`
- `scripts/run_pg_textsearch_ecommerce_acceptance.py`
- `scripts/score_pg_textsearch_ecommerce_acceptance.py`

资料依据：pg_textsearch 1.4.0 官方 README/发行包；其 PostgreSQL License、PG17/18预编译包、BM25 Top-K、partial index、过滤行为和中文 parser 说明均在上游项目及发行说明中核对。
