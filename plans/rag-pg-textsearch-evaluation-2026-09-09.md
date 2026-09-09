# E17 成熟 BM25 扩展评测：pg_textsearch

状态：evaluated_not_adopted_globally。起点以运行时 HEAD 为准；E16 已拒绝全局切换 `ts_rank_cd`。

目标是验证成熟的 PostgreSQL BM25 索引能否同时保留 BM25 质量并解决自写 SQL 的在线词频统计成本。本轮不修改现有测试／开发数据库容器，不改变生产默认，不调整 query、chunk、embedding、RRF、精排或打包参数。

候选筛选：优先 `pg_textsearch 1.4.0`，因为官方支持 PostgreSQL 17/18、提供预编译 Linux 包、使用 PostgreSQL License，并通过 BM25 索引执行 Top-K。ParadeDB `pg_search` 与 VectorChord-BM25 均需额外评估 AGPL／商业许可；VectorChord 文档还注明主要测试英文。本轮不并行安装多个扩展。

固定实验：在隔离 PostgreSQL 18 + pgvector + pg_textsearch 容器中导入与 E16 相同的 Wix 11167 片段；保留相同 `lexical_document` 文本、查询、20候选及来源映射。先测扩展安装、索引、过滤和分数稳定性，再做 Wix dev20 诊断和另一批20验收。开发阶段可确定接线，不调 k1/b（使用1.2/.75）。验收质量门槛：精排/打包完整覆盖不得低于当前 BM25，Recall@5下降不超过2个百分点；750ms超时不多于当前BM25且查询中位至少下降20%。中文需要可复现的同tokenizer合同；PG `simple` 不会自动中文分词，因此以已预分词 `lexical_document` 建索引，不能将不同分词收益归于扩展。

指标：词法和融合文章 Recall/MRR/nDCG、CE/pack 5、救回/误伤、3次交替SQL中位与经验p95、750ms检查、索引构建时间/大小。API 0，现有向量和CE分数能复用则复用。失败和许可证/部署限制保留。

结果：组合镜像验证pgvector 0.8.6与pg_textsearch 1.4.0共存。Wix新100题中位词法788.17→2.70ms；融合Recall@20同为83%，CE/pack Recall@5 69→68%、完整覆盖62→61、nDCG 57.21→56.48，英文候选通过预注册质量/性能门槛但非排序等价。10租户111670行过滤审计0越权。

中文复杂电商40题三种形态均40/40超过750ms：全表索引；final-generation partial索引但适用性后过滤；partial索引加scope-first materialization。最后形态完整检索中位约1016ms。按停止条件不再删词/放宽超时/后验调参；不全局采用。已有PG_FTS_ZH_V1同40题40/40可用、wire完整82.5%，因此后续按generation lexical contract分别推进英文pg_textsearch与中文FTS，生产默认本轮不改。完整报告 `docs/rag-pg-textsearch-acceptance-2026-09-09.zh-CN.md`。

退出：评测、失败保留、组合镜像、过滤审计、采用决定和相干交付已完成。英文provider的生产接线、CI镜像、索引退役/重建和新鲜授权负载验收属于后续实现，不把本轮评测写成已上线。
