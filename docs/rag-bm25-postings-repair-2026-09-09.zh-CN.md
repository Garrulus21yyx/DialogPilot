# E14：BM25访问路径修复，整体性能验收仍开放

2026-09-09。基线SQL为 `3eee99f`，最终SQL文件SHA见manifest。修复位于 `PostgresHybridBackend._bm25`；不改变查询、切块、BM25公式、授权范围或750ms生产预算。模型/API调用为0。未部署。

## 根因与实现

E13证明来源ID+版本关联低估范围行数（估计1、实际11167），原计划对每个范围片段回读并测试长查询词数组。除此之外，打分后通过ordinal恢复candidate_id也可被规划成约10441×11167次比较。两者共同原因是把低估的范围关系重复作为访问驱动，而不是仅作为资格集合与统计总体。

修复保持以下合同：

1. scoped保留全部租户、版本、授权及适用条件，提供整个合法范围的N/avgdl。
2. term_hits独立产生同租户/generation的词项命中ID；查询词数组通过InitPlan一次生成，允许既有GIN合并posting集合。不是强制索引，也不规定过滤的物理先后顺序。
3. term_hits与scoped相交后才读取词数组并计算TF/DF；只有合法候选参与打分，Top-K在过滤之后。
4. ordinal保持窄聚合键，同时携带其唯一candidate_id，避免打分后再关联整个scope；并列按稳定candidate_id排序。
5. 词频匹配使用实际参数数组的ANY。此语句prepare=False，避免自动复用通用预备计划；不改变全局优化器设置。显式force_generic_plan仍会覆盖这一行为，不宣称该模式也被加速。

来源投影和Memory复用同一后端SQL；捕获SQL的三个已有诊断入口同步兼容execute参数。

## 固定结果配对

现存完整Wix评测库：11167个当前片段、物理149330行。它不是已删除的E12电商库。本轮固定来源版本、查询、scope、20候选，旧/新交替各3次；表中为SQL墙钟中位数毫秒。计时用30秒诊断预算取得完整旧结果，另以750ms单独检查新SQL，生产超时未调整。

| 查询 | 修改前ms | 修改后ms | 新SQL单次750ms检查 |
|---|---:|---:|---|
| original | 2255.2 | 260.8 | 通过 |
| rare | 310.9 | 2.5 | 通过 |
| chargeback | 377.6 | 177.0 | 通过 |
| common | 5125.1 | 555.5 | 通过 |
| fresh_shipping | 680.2 | 655.3 | 通过 |
| fresh_negation | 649.1 | 623.4 | 通过 |

以上6条，以及从既有Wix样本抽取的另外6条，**12/12的ID、浮点分数、排序完全一致**。追加6条的新SQL中位数为791.4、734.7、747.6、713.5、711.4、787.0ms；其中2条750ms检查仍超时。总计10/12通过该次预算检查，不是p95或并发SLA。补充题来自既有已消费数据，且候选方案迭代中已重跑，不称新鲜封存验收。

原长查询2255.2→260.8ms（约88.4%下降），常见词5125.1→555.5ms（约89.2%下降）。不能把延迟收益写成Recall或答案准确率收益。

## 剩余瓶颈与边界

补充查询05dc…的EXPLAIN总执行835ms：scoped约184ms；词项命中后仍有11150片段进入TF统计，相关子树约527ms（包含其上游时间，不能相加）。GIN的BitmapAnd约46ms，已不是主耗时。查询中常见词使几乎全库命中，当前在线展开词数组的成本依然存在。

因此本轮修复错误访问/恢复ID的计划放大，**没有关闭R09整体性能问题**。后续应针对持久化词频/文档长度与范围统计的owner合同设计优化，并在原电商语料重建后验收；不能直接改停用词或扩大超时冒充同语义修复。尚未做并发负载测试。

## 验证与失败方案

- 真实生产_bm25调用：prepare_threshold=0下、auto模式重复6次仍无自动预备BM25语句，输出一致。三查询auto约283/199/688ms；显式generic分别约673/200/958ms，仍保留该限制。
- 回归：test_bm25_sql_equivalence、test_hybrid_retrieval_backends、test_knowledge_applicability，最终结果见validation.json。已有性质覆盖多generation、多scope、标量BM25等价、查询词重复/排列与Top-K前缀；适用版本/撤回合同继续检查。
- 初始逐词JOIN被扁平化，长查询约10秒，拒绝；逐词LATERAL约400–550ms但多词英文退化，拒绝。
- v2携带ID消除二次关联，但文本分组有额外成本；v3–v4一次GIN合并继续改善长查询，部分英文仍超预算；v5窄ordinal分组改善，通用计划仍退化。
- v6改为IN子查询以改善generic稳定性，但追加6条全部超预算，拒绝。最终使用ANY与语句级prepare=False。失败报告和计划保留，不选择性删除。

## 可复现入口与证据

使用仓库虚拟环境，模块方式运行：

```bash
.venv/bin/python -m scripts.benchmark_bm25_postings_pair
.venv/bin/python -m scripts.validate_bm25_postings_queries
.venv/bin/python -m scripts.check_bm25_prepared_plan
```

前两个脚本输出目录必须尚不存在；复跑时改ROOT到新的独立目录，保留既有证据。需要现存评测数据库和基线提交。数据库只读；连接凭据从本地测试容器读取且不写报告。

主报告：artifacts/eval/bm25-postings-pair-final-2026-09-09；追加：bm25-postings-additional6-final-2026-09-09；同前缀v1–v6及smoke/lateral保存未采用结果。实现交付、固定结果等价和全范围性能验收分别记录。
