# E10 BM25已有倒排索引接线

用户授权优化SQL。已有GIN lexical_terms，但原SQL在全scope词项展开前未过滤匹配。修复owner为PostgresHybridBackend._bm25；保持适用scope N/avgdl、匹配文档DF、TF、分数与ID稳定排序，所有匹配文档在LIMIT之前参与统计。复用generated lexical_terms及索引，导入/更新/删除已有投影维护不新增数据权威。

实验：隔离PG，旧/新同语料同查询同scope；标量oracle与查询词排列、空/不匹配、多租户过滤，性能用较大背景语料比较EXPLAIN及多次耗时。API0，模型0。只在分数/身份/范围一致且访问工作减少时采用，不通过改timeout掩盖失败。历史80答案不重跑，不声称答案准确率提高。

结果：复用GIN完成匹配访问接线；12k合成语料旧/新结果精确一致，rare中位316.18→16.49ms/unnest10800→106，中文321.82→21.12ms；高频common518.88→539.16ms，公开保留负面成本。无API、无部署，原80题不重跑。oracle扩展3代版本/3scope/6query并检查TopK和排列；初次测试错误使用UPDATE不可变投影后修正为新generation，未改保护。报告与计划/执行产物精确路径提交推送。
