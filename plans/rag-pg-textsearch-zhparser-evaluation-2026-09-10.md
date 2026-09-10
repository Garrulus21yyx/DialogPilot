# E18：zhparser + pg_textsearch 中文 BM25 对照

状态：development_gate_passed_not_production_adopted。

## 缺口与假设

E17 只验证了 `ascii-cjk-unigram-bigram-v1` 预切词配合
`pg_textsearch(text_config='simple')`。40 条复杂电商查询均超过 750ms，不能据此判断
中文词级 parser 配合 BM25 的效果。

假设：`zhparser` 将长中文查询压缩为较少的词级 lexeme 后，`pg_textsearch` 能在不改变
适用范围过滤的前提下完成 Top-K BM25 检索，并保持必要证据覆盖。

## 固定输入与变量

- 数据：E17 相同的 6,287 文档、40 条已消费复杂电商开发题及既有完整 query。
- 切块：`structure_aware`，512/64；文档、offset、metadata、generation 不变。
- Dense：相同本地 BGE-M3 文档与 query 表示。
- 召回：Dense 20、lexical 20、RRF 0.5/0.5、`k=10`、融合 20。
- 后续：相同本地 `bge-reranker-v2-m3`、Top-5、2,600-token pack/ToolMessage。
- 适用范围：tenant、generation、region、channel、product scope、effective time 均不变。
- SQL：单语句 750ms；模型 API 0；微调保持暂停。
- 唯一策略变量：`PG_FTS_ZH_V1` 与 `zhparser + pg_textsearch 1.4.0 BM25`。

## 指标与采用标准

记录 parser 词项数、lexical SQL p50/p95/timeout、候选/精排/wire 的 evidence-unit
Recall、完整证据覆盖、MRR、nDCG@5、错误 scope、索引构建时间及大小。

中文 BM25 仅在以下条件全部满足时成为可继续推进的候选：

1. 40/40 查询在 750ms 单语句预算内取得可用检索结果；
2. 错误 scope 为 0；
3. wire 完整证据覆盖不低于同输入的 `PG_FTS_ZH_V1` 33/40；
4. evidence-unit Recall 和 nDCG@5 不低于 FTS 超过 2 个百分点；
5. 产物保留每题排名、实际 ToolMessage、来源 offset 和超时状态。

该数据已用于开发，不构成新鲜验收。通过只表示进入新的 held-out/并发验收，不直接切换生产。

## 结果

40/40检索可用、错误scope 0。70次词法调用全部在750ms内，p50 50.22ms、p95
53.74ms；query lexeme 12--34、中位22。BM25索引11590行、构建1.43s、大小
5,390,336 bytes。

候选Top5完整证据60%持平，unit Recall 84.17%→86.67%，nDCG@5
76.64%→81.81%；三救回三误伤。经过固定CE/pack/wire后两臂相同：完整33/40、unit
Recall94.17%、MRR95.83%、nDCG89.65%。全检索到wire p50为353.67ms，历史同输入
FTS p50为1188.78ms（同机分时开发对照，非并发SLA证明）。API0。

预注册开发门槛通过。生产不切换；新鲜held-out、generation索引生命周期、CI镜像与
多租户并发/撤回验收保持开放。报告
`docs/rag-pg-textsearch-zhparser-acceptance-2026-09-10.zh-CN.md`。
