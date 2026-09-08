# WixQA BM25执行计划与等价优化

固定第一条超时开发问题，EXPLAIN ANALYZE使用单独诊断连接30秒限制，生产750ms/整体3秒未变。基线约1001ms：全scope11167行、逐片段词项展开、全局词频聚合及宽来源投影重复参与排序/物化，临时写2149 blocks。具体计划见before.json。不是外部API成本或query生成错误。

保留的相干改动在PostgresHybridBackend._bm25：scoped只保留评分所需candidate_id/lexical_terms/dl；每片段先聚合匹配词频再输出，保持原scoped总文档数/平均长度和df；评分完成先按同样score/ID截TopK，随后按唯一candidate_id主键读取来源元数据。候选来自已过滤scope，来源读取不会扩大权限。公式、词法分词、权重、阈值、索引与数据不变。

实验记录：数组计数产生坏连接计划，固定题约18031ms，拒绝；局部聚合约822ms，提前TopK约783ms，进一步延后来源读取约896ms。单次EXPLAIN存在波动及计时开销，不能将这些差值称稳定延迟收益。所有变体固定题Top20 ID和分数一致。保留全部失败方案产物。

原预算真实分路20题：基线9成功/11失败；局部聚合+提前TopK18成功/2失败；最终窄投影+延后来源读取19成功/1失败。每轮成功查询两路Top20顺序全部与离线相同。不是统计显著的吞吐测量、不是答案收益，仍有一题POSTGRES_UNAVAILABLE，性能项继续开放；不重跑到20/20或调高默认超时。

验证：tests/test_bm25_sql_equivalence.py和test_hybrid_retrieval_backends.py真实PG共7通过，含生成语料、不同scope、重复查询词、查询词顺序、空/未命中、BM25独立标量公式与权限隔离。下一步针对剩余SQL成本读取计划，需覆盖完整封存查询与负载后再签发性能结论；暂不付费生成。
