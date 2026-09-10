# zhparser + pg_textsearch 中文 BM25 验收

## 结论

`zhparser 2.3 + SCWS 1.2.3 + pg_textsearch 1.4.0` 已在 PostgreSQL 18 与
pgvector 0.8.6 的隔离镜像中跑通，并通过预注册的中文复杂电商开发门槛。它解决了 E17
`simple` 配置接收单字/双字预切词后查询词项过多、40/40 超过 750ms 的问题。

本次 40 条查询的词级 lexeme 为 12--34，中位 22；70 次实际词法调用全部成功，词法
SQL p50 50.22ms、p95 53.74ms。完整检索至本地精排、打包和 ToolMessage 的 p50 为
353.67ms；历史同输入 `PG_FTS_ZH_V1` 运行的 p50 为 1188.78ms，约 3.36 倍。两次
延迟来自同机不同时段运行，应视为开发对照，不能代替并发压测。

质量方面，BM25 候选 Top-5 的 evidence-unit Recall 从 84.17% 提至 86.67%，nDCG@5
从 76.64% 提至 81.81%。完整证据 Top-5 均为 60%，存在三条救回与三条误伤。经过同一
本地 CrossEncoder 后，两臂最终完全相同：ToolMessage 完整证据 33/40（82.5%）、
evidence-unit Recall 94.17%、MRR@5 95.83%、nDCG@5 89.65%，错误适用来源为 0。

该 40 条是已经消费的模拟开发集。通过表示该方案成为中文 BM25 候选；没有切换生产，
也不构成线上答案准确率或新鲜泛化证明。

## 实际链路

索引输入使用原始 `retrieval_text`，由 `zhparser` 做中文词级解析：

```text
retrieval_text
    -> zhparser / SCWS
    -> pg_textsearch BM25 generation 部分索引

完整 query
    -> zhparser / SCWS
    -> BM25 lexical Top-20
    + 同一本地 BGE-M3 Dense Top-20
    -> RRF 0.5/0.5, k=10, fused Top-20
    -> bge-reranker-v2-m3
    -> Top-5 / 2600-token EvidencePack 与 ToolMessage
```

没有把现有 `lexical_document` 再送入 `zhparser`。该字段已经是单字/双字预切词串，重复
解析会把两种 tokenizer 的语义混在一起。BM25 index 使用 generation 与 tenant 的部分
谓词，查询仍保留数据库内 region、channel、product scope、effective time 等适用过滤。

自定义 text-search configuration 必须以 `public.chinese_zh` 传给 `pg_textsearch`；仅传
`chinese_zh` 时索引构建侧不能解析该配置。兼容性 smoke test 已保留这一边界。

## 固定实验合同

- 语料：6,287 文档，最终 generation 11,590 chunks；structure-aware 512/64。
- 问题：E17 相同 40 条复杂多条件电商开发题及缓存完整 query。
- 召回及后续预算：Dense 20、lexical 20、fused 20、final 5、2,600 tokens。
- 模型：本地 BGE-M3 与本地 `bge-reranker-v2-m3`；API 0，微调暂停。
- SQL 单语句预算：750ms。
- 权威过滤：tenant、generation、region、channel、product scope、effective time 不变。

索引构建 11,590 行耗时 1.43s，索引大小 5,390,336 bytes。原始知识导入仍会创建不可变
generation 并累计写入历史投影，其耗时没有计入在线检索数字，也没有由本实验修复。

## 采用判断

预注册门槛全部通过：40/40 可用、错误 scope 为 0、wire 完整证据不低于 33/40，最终
unit Recall 与 nDCG@5 无超过 2 个百分点的退步，且逐题排名、来源 offset、ToolMessage
和超时状态均已保存。

生产切换仍需：

1. 将组合镜像纳入 CI，并由 generation builder 创建和退役对应的中文 BM25 部分索引；
2. 为 `PG_TEXTSEARCH_ZHPARSER_BM25_V1` 建立独立 backend/policy fingerprint 和缓存身份；
3. 用未参与本次选择的新鲜中文题验证质量；
4. 在多租户授权过滤、更新/撤回和并发连接池负载下验证 p50/p95、结果数和零越权。

## 证据

- `evaluation/docker/pg_textsearch_zhparser.Dockerfile`
- `scripts/run_pg_textsearch_zhparser_ecommerce_acceptance.py`
- `scripts/score_pg_textsearch_ecommerce_acceptance.py`
- `scripts/report_pg_textsearch_zhparser_ecommerce.py`
- `artifacts/eval/pg-textsearch-zhparser-ecommerce40-2026-09-10/manifest.json`
- `artifacts/eval/pg-textsearch-zhparser-ecommerce40-2026-09-10/comparison.json`
- `artifacts/eval/pg-textsearch-zhparser-ecommerce40-2026-09-10/validation.json`
- `artifacts/eval/pg-textsearch-zhparser-ecommerce40-2026-09-10/pure-cases.jsonl.gz`
