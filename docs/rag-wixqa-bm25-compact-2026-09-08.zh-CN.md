# BM25紧凑中间记录与跨问题验证

剩余family setting问题：原计划77025词频行，每行携带长candidate ID，导致2489临时写块。scoped为每行生成查询内部ordinal，以整数做词频/评分聚合；TopK前恢复candidate_id，仍按score DESC,candidate_id排序。ordinal不持久化、不对外返回、不参与相关性或权限判断，来源与适用范围仍由原scoped谓词决定。

该见证EXPLAIN ANALYZE从929ms/2489临时写块变为811ms/980块。EXPLAIN不是正常执行延迟；不把单次计划耗时当SLA或稳定性能估计。原文档词频、范围平均长度/df及最终ID/score与旧语句核对一致。

原750ms数据库配置与3秒source预算下，开发20一次全部成功，Dense/BM25 Top20顺序均与离线相同。另一次固定heldout20（已消费离线权重评测，未参与本次SQL调优）同样20成功、全部两路顺序一致。两批合计40/40，没有靠扩大超时、换query或缩小6221篇语料完成。此前9/20、18/20、19/20各轮失败保留；不能把40/40当线上可靠性或答案正确率。

tests/test_bm25_sql_equivalence.py和test_hybrid_retrieval_backends.py共7项真实PG通过，包含生成语料的标量公式、重复/乱序查询词、scope隔离和无命中。开发运行与测试短暂并发，不作严谨延迟统计。整个阶段API0、新embedding0。

下一步恢复公共真实Conversation Agent→knowledge_search→Flash答案小批验证，直接使用已保留全量PG库。生产权重仍.25，.5只作对照候选。代表性并发压力、长query和更大库的性能未验，整体RAG尚未关闭。
