# BM25为什么没有走GIN：实际执行计划诊断

2026-09-09，基点1217b3e。当前完整Wix评测库只读执行生产BM25 SQL，模型/API调用0，不改生产SQL或数据库配置。原E12电商隔离库已删除，本次不是其历史执行计划的复原；现存Wix版本11,167个片段，包含旧版本的物理表共149,330行。与E12保留部分计划（估计1、实际3257）的机制一致。

## 已定位的因果链

对同一完整版本逐步加入谓词，EXPLAIN ANALYZE得到：

| 诊断谓词 | 估计输出行数 | 实际输出行数 |
|---|---:|---:|
| tenant/generation/scope/locale | 11120 | 11167 |
| 再关联source_id（诊断性省略revision_id） | 11120 | 11167 |
| 再关联revision_id（诊断性省略source_id） | 11120 | 11167 |
| 完整tenant/source_id/revision_id关联 | 2 | 11167 |
| 完整时效、地区、渠道、最新版本条件 | 1 | 11167 |

失真首先发生在多列来源版本关联，不是基础表行数估计错误。source_id与revision_id关联条件在数据上相关；联合估计将行数压低了数千倍。上述省略谓词仅用于定位估计变化，不可用于生产：其他数据可能存在重复版本标识，完整来源/租户/版本约束仍是合同。

在“只剩1行”的估计下，scoped与词项表之间采用嵌套循环加主键读取，看起来比全表范围的GIN检索更便宜，实际执行11167次主键回读并检查词项数组。真实cx01原始查询两次默认执行分别2627/2556ms；对应EXPLAIN执行2496/2664ms。后一计划matching阶段2351ms，保留的首次计划matching阶段2219ms。计划节点时间包含子节点，不能简单相加。

这提供了“来源版本多列关联严重低估 → 逐主键回读 → 大量词项数组匹配 → 超750ms”的直接证据。PostgreSQL按估计成本选择计划，并不因为索引存在就必然使用。[官方EXPLAIN说明](https://www.postgresql.org/docs/current/using-explain.html)

单纯增加多列统计也不能预先宣布能修复：官方说明扩展统计目前不用于表关联选择性估计。[CREATE STATISTICS限制](https://www.postgresql.org/docs/current/sql-createstatistics.html)

## GIN实际负责什么

当前`lexical_terms text[]`的GIN保存“词项 → 含该词项的行ID集合”，支持`&&`重叠查询，不会返回BM25排名，也不提供本SQL需要的完整重复词频数组、范围内DF或平均长度。[GIN结构与array_ops](https://www.postgresql.org/docs/current/gin.html)

示意：`退款 → [chunk2, chunk8]`使系统无需逐个片段查看是否包含“退款”。查到行ID后，仍要过滤适用来源、读取打分所需数据、计算分数。当前实现还在线展开匹配词项数组计算TF/DF，并扫描适用范围计算N/avgdl。

真实控制查询`chargeback`在诊断性禁止nested-loop的路径下，GIN节点耗时0.642ms，返回2167条跨历史版本的物理行；完整匹配阶段8.802ms，最终当前版本匹配160个片段。整条EXPLAIN仍277.750ms，说明GIN只加速其中一部分，不能将0.642ms当作端到端检索延迟。

## 为什么不能强制某条计划后宣布完成

| 查询 | 默认EXPLAIN ms | 诊断禁用nested-loop ms |
|---|---:|---:|
| cx01完整原句 | 2663.99 | 29446.87 |
| 不存在词 | 341.27 | 197.26 |
| chargeback | 494.41 | 277.75 |

cx01在首次禁用nested-loop时超过30秒；失败保留，第二次才在30秒内完成。替代计划对149330行做全表词项检查，实际返回14997条再关联范围，因此更慢。完成的三组对照均核对返回ID、分数及顺序完全相同；这不是性能稳定性负载测试。诊断开关仅作用于只读连接，连接已关闭。

## 成熟实现的分工与本项目建议

PostgreSQL原生全文检索可以用GIN + tsvector/tsquery筛选，再用原生排序函数；它不是本项目SQL实现的BM25。[PostgreSQL全文索引](https://www.postgresql.org/docs/current/textsearch-indexes.html)

如果明确需要BM25，可采用成熟检索实现，例如Lucene系：倒排迭代器直接提供文档词频，BM25使用索引/集合统计和文档长度归一化。Elasticsearch默认相似度为BM25。在线仍需计算相关分数，但不必像本项目一样重新展开词数组来恢复TF。[Lucene PostingsEnum](https://lucene.apache.org/core/9_12_2/core/org/apache/lucene/index/PostingsEnum.html)、[BM25Similarity](https://lucene.apache.org/core/9_12_2/core/org/apache/lucene/search/similarities/BM25Similarity.html)、[Elasticsearch similarity](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity)

建议先在现有owner范围修复候选访问路径与来源版本关联的估计问题，用同一完整语料和长/短/稀有/常见查询验证；不删除来源约束、不全局强制计划、不用放宽超时当修复。若继续保留自实现BM25，需要将重复TF/文档长度等索引期可维护数据从在线数组展开中移出；若选成熟BM25实现，必须另行验证权限、历史版本、撤回、分词及排名变化。其默认IDF通常不是每次业务过滤后的scope DF，不能当成无语义变化的直接替换。

当前完成的是根因诊断，性能修复未完成。下一项验收应覆盖完整真实语料、历史generation积累、复杂适用条件和原750ms预算，保留ID/分数/顺序等价检查与不利查询。

证据目录：`artifacts/eval/bm25-planner-diagnosis-2026-09-09/`。复现脚本：`scripts/diagnose_bm25_planner.py`与`scripts/diagnose_bm25_scope_estimates.py`；依赖现存只读评测库和本地Docker凭据，输出不包含凭据。
