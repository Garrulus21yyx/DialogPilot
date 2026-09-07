# G3：实际 PG／Flash／知识工具的候选配对

2026-09-07，起点 `aa39569`。固定两组候选选择方式，不改生产默认。12条样本按冻结dev300的case_id SHA256排序选取，不按救回／误伤选择；保留raw查询，并通过RESOLVED入口关闭额外改写，仅隔离检索变量。这不代表raw查询已经完整表达会话意思。

## 实际结果

| 指标 | 原候选选择 | 两路交替选择 |
|---|---:|---:|
| 完整候选证据 | 10/12 | 10/12 |
| Flash精排Top5完整证据 | 10/12 | 10/12 |
| EvidencePack完整证据 | 10/12 | 10/12 |
| 实际ToolMessage完整证据 | 10/12 | 10/12 |
| MRR@5 | 0.7361 | 0.7361 |
| nDCG@5（项目口径） | 0.7609 | 0.7609 |
| 精排回退／检索失败 | 0／0 | 0／0 |

12条的候选成员集合都改变了，但完整覆盖没有产生救回或误伤。该样本只验证这一小组真实工具链边界，没有证明新策略更好，也不足以推翻dev300本地精排的净增益。不是封存验收或最终答案正确率。

100文档，512/64结构切块；BGE-M3 + PG BM25；每路20，候选20，固定.25/.75、RRF k10、2600-token／5片段预算。两组使用同一生产ResultReranker（deepseek-v4-flash）、KnowledgeRetriever、EvidenceValidator、knowledge_search handler及MCPToolManager。实验适配器只负责单查询、无metadata hint、时间点场景的候选选择；基线逐条和原PostgresKnowledgeCandidateSource排序核对。未修改生产候选合同，也未绕过证据来源复验。

基线多执行一次原owner对照，故summary里的请求延迟不能用来声称两路交替提升性能；这轮只评估质量与证据传递。

## 剩余两条与下一步

| 案例 | 冻结query | 实际失败位置 |
|---|---|---|
| doc2dial-dev-9d4920bea87d4f4cb977483d943c1ef3-10 | If the tag cannot be used? | 两组候选均缺完整证据 |
| doc2dial-dev-85a07edcac90068ee15c3769034af6f2-3 | yes | 两组候选均缺完整证据 |

这是上下文依赖表达的具体见证，尚不能仅凭短句断定检索失败全部由query造成。下一步转入原定G2：同一Context进入真实Conversation Agent，捕获模型可见上下文和实际工具查询，比较条件语义及候选覆盖。G3融合采用保持待验证，不继续依据已暴露样本手调配额，不恢复微调。

## 成本、审计与限制

计划24次调用、总上限28，实际24次Flash调用，无额外重试。供应商返回usage合计input_tokens=193852、output_tokens=3000、cache_read_input_tokens=14208，按返回字段分别报告，不推算未核实的费用。独立评测数据库已按脚本清理，生产库未修改。

3项测试通过：候选去重／配额／来源字段保持、UNAVAILABLE不变成部分成功、冻结抽样和24条结果审计。120个实际可见片段逐条与源文字符范围完全一致；全部精排结果保持候选ID置换。未测试HTTP鉴权、真实Agent规划、生成答案、并发负载或新鲜heldout。

[汇总](../artifacts/eval/rag-g3-flash-pair12-2026-09-07/summary.json)；同目录manifest、cases.jsonl.gz、api-calls.json.gz保留实际输入、输出和调用记录。[抽样清单](../artifacts/eval/rag-g3-flash-sample12-2026-09-07/manifest.json)记录冻结query checksum。

## 复现

先从G1 `input-datasets.json.gz` 中恢复 `doc2dial-rag-mini-dev-v1`；配置隔离评测 `TEST_DATABASE_URL` 及现有DeepSeek Flash profile、模型路径后运行：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_rag_membership_flash_pair.py --dataset /tmp/rag-g3-flash-dataset --queries artifacts/eval/rag-g3-flash-sample12-2026-09-07 --embedding /path/to/bge-m3 --reranker /path/to/bge-reranker-v2-m3 --output /tmp/g3-flash-pair --api-limit 28
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_rag_membership_flash_source.py
```

第一条命令会调用API，第二条只审计已保存结果。不能复用此前人工完整query的Flash排序替代本次raw查询的模型调用。
