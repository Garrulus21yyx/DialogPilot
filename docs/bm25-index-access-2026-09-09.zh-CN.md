# BM25倒排访问优化（2026-09-09）

已在 `PostgresHybridBackend._bm25` 接入既有 `lexical_terms` GIN索引的查询词匹配条件。原实现先展开整个适用语料的词项；现在保留全scope的N和平均长度统计，只展开包含查询词且属于该scope的片段。没有新增检索系统、调整750ms、扩大TopK或改BM25参数。

## 已证实的收益

隔离数据库，12,000个合成片段，其中10,800属于请求租户；每片段约300词，混入空片段及低频中英文目标。旧/新SQL各交替运行6次，默认规划器，不强制索引。两版返回的ID、分数及顺序逐项完全相同。

| 查询 | 原中位耗时 | 优化后中位耗时 | 解释 |
|---|---:|---:|---|
| 低频英文rare | 316.18ms | 16.49ms | 下降94.8% |
| 中文退款 | 321.82ms | 21.12ms | 下降93.4% |
| 不存在的词absent | 326.80ms | 12.14ms | 无需展开无关词项 |
| 高频common | 518.88ms | 539.16ms | 约慢3.9%，几乎全匹配时筛选无收益 |

rare执行计划实际使用terms_gin，逐文档unnest次数10,800→106。common由规划器选择扫描，未通过强制GIN掩盖高命中率成本。完整EXPLAIN ANALYZE/BUFFERS JSON保存在对应产物目录。

这是合成SQL工作负载的实测，不是原电商80题超时复测，也不是并发p95或线上SLA证明；原隔离评测库已删除。没有重新生成答案，不能声称71.25%答案准确率因此提高。

## 正确性和生命周期

- N/avgdl仍包含全部适用文档，包括不匹配文档和空文档；只缩小TF计算范围。
- DF从匹配且已授权的全部片段计算，LIMIT只在评分以后应用。
- candidate_id恢复后按score降序、ID升序，稳定并列顺序不变。
- matching与scoped关联，沿用原tenant/generation/地区/渠道/时间/权限等筛选条件。
- 导入已经生成lexical_terms并维护GIN，无需新增迁移或重新embedding。新版本通过新generation投影，旧generation不能混入当前评分。
- 相同SQL快照内计算统计和匹配，未新增跨请求统计缓存，因此没有额外失效协议。

增强的标量oracle测试覆盖3个generation（原始/改写内容/移除部分来源）、每个3种scope、6种查询，合计54组合；每组验证独立公式、状态、查询词顺序不变、Top3等于全排序前缀。既有数据库后端和投影测试一同执行，**11项全部通过（14.91秒）**。初次补测错误地尝试UPDATE不可变投影，被数据库拒绝；已修正测试为实际支持的新generation路径，没有放宽不可变保护。

## 边界和后续

这次最小修复解决“有倒排索引却仍先展开全scope词项”的访问缺口。全scope长度统计仍按查询计算，匹配文档的TF仍从词项数组计算；没有宣称完成预计算TF/DF架构。如果代表性真实负载仍超时，再基于执行计划考虑预存长度/词频与适用scope统计成本，不先引入复杂统计缓存。

生产配置及错误语义不变：单路异常仍按原合同返回UNAVAILABLE。允许单路部分成功属于另外的合同改动，本次没有静默降级。

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_bm25_index_access.py \
  --baseline f1ae53b --output artifacts/eval/bm25-index-access-new
# 测试需要TEST_DATABASE_URL，fixture自动创建并清理隔离数据库：
.venv/bin/pytest -q tests/test_bm25_sql_equivalence.py \
 tests/test_hybrid_retrieval_backends.py tests/test_postgres_retrieval_projection.py
```

性能脚本可使用TEST_DATABASE_URL，或本地既有测试容器的连接配置；凭据不写入报告。脚本创建随机独立数据库并在finally删除，不写业务库。产物：`artifacts/eval/bm25-index-access-2026-09-09/`。本次模型/API调用0。
