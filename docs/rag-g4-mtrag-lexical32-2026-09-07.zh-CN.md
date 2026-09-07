# G4：MTRAG 完整领域语料上的查询表达对照

2026-09-07，起点 `b2794e7`。从已有dev分组中每域哈希选8个conversation，每个conversation哈希选1题，共32题。固定官方非空passage、领域内完整语料、正文检索、现有词法tokenizer、BM25 k1=1.2/b=.75及Top20。API 0次，embedding 0次，未生成答案。

| 官方查询表达 | Passage Recall@5 | Passage Recall@20 | MRR@20 | Binary nDCG@20 | Top20至少命中一个相关片段 |
|---|---:|---:|---:|---:|---:|
| lastturn：最后一句 | 20.57% | 33.07% | 0.2130 | 0.2142 | 16/32 |
| questions：历史用户问题表达 | 20.57% | 34.38% | 0.2095 | 0.2265 | 16/32 |
| rewrite：官方完整改写 | **28.91%** | **50.26%** | **0.3168** | **0.3329** | **21/32** |

完整改写相对lastturn：Recall@20净增17.19个百分点，8题提高、1题降低；MRR 7题提高、5题降低；nDCG 9题提高、4题降低。因此不能只报告均值而忽略误伤。历史问题拼接的Recall@20仅增1.30个百分点，MRR略降。

每领域Recall@20（各8题）：

| 领域 | lastturn | questions | rewrite |
|---|---:|---:|---:|
| ClapNQ | 29.17% | 37.50% | 50.00% |
| Cloud | 47.92% | 47.92% | 47.92% |
| FiQA | 16.67% | 19.79% | 41.67% |
| Govt | 38.54% | 32.29% | 61.46% |

Cloud没有Recall收益，不能外推为各领域均有效。原始qrels均为二值相关性，Recall是每题命中相关passage比例再宏平均，不是“所有必要答案证据齐全率”；nDCG按二值gain计算。MRR只关注首个相关片段，可能与Recall变化方向不同。无正分候选不会用任意零分文档填充Top20。

## 当前证据说明什么

本轮支持继续解决**聚焦的完整检索问题**，而不是机械堆入更多历史文字。完整改写后仍有11/32题的Top20完全没有相关片段，且平均只覆盖约一半标注passage；词法召回缺口仍在。

本轮rewrite由官方数据提供，不是现有Conversation Agent的实时输出，不证明线上改写已获得17个百分点提升。正文离线BM25复用现有tokenizer和公式，但不包含PG执行、标题表示、Dense、融合、精排、ToolMessage或答案，不能宣称生产端到端提升。原始官方passage未再切块，也不评价自定义chunking。

配对bootstrap仅描述本开发样本的不确定性：每题来自不同conversation，32组重采样10,000次，种子20260907，按case_id排序。rewrite−lastturn的95% percentile区间：Recall@20 +5.73至+30.21个百分点；MRR +0.0103至+0.2206；nDCG +0.0327至+0.2266。这是探索性开发估计，非封存验证；没有按领域分层重采样，域内只有8题。

## 成本、实现与验证

查询集合已知时，流式统计只保留涉及词项的posting，但文档数、完整词长与词项df均来自完整领域语料。这是同一BM25公式的充分统计，未删除干扰文档。生成语料对照现有bm25_matrix在浮点容差内一致；同分按ID稳定排序。使用float64，不能声称与生产PG排序逐位一致。

四域统计及评分总计约27.5秒，涵盖96个查询表达和366,438个非空片段。该时间含读取/分词/建统计，不是在线单次查询延迟；不用于宣称生产性能提升。

4项检查通过：生成语料公式对照、指标分母与排序、与gold无关的分组选择、96条实际结果的同案例/同域/同预算和重算审计。没有调用精排或核验模型。

```bash
PYTHONPATH=. .venv/bin/python scripts/run_mtrag_lexical_query_pair.py \
  --dataset /tmp/dialogpilot-mtrag-adapted-v2-20260907 \
  --corpora /tmp/dialogpilot-mtrag-corpora-20260907 \
  --per-domain 8 --output /tmp/mtrag-lexical-new-output
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_mtrag_lexical_pair.py
```

输入按前轮适配manifest的checksum校验；完整输入恢复方法见[适配报告](rag-g4-mtrag-adapter-2026-09-07.zh-CN.md)。产物：[汇总](../artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07/report.json)、[逐题排名](../artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07/results.json.gz)、[冻结选择](../artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07/selection.json)、[区间及代码身份](../artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07/uncertainty-and-provenance.json)。

下一项保持这32题与官方rewrite固定，补Dense分路，检查BM25漏掉的证据是否被向量找到；先核对可复用索引与本地编码吞吐，再决定完整语料编码预算。不能为省算力仅保留gold文档，也不继续根据32题调整query。动态融合要在两路结果均可复算后比较，当前生产权重不变。
