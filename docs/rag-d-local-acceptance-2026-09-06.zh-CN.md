# RAG D：200 条本地检索验收与来源格式修复

状态：已完成这一轮预定检索对照；**未证明本地精排有稳定提升，整个 RAG 目标仍未完成。** 外部推理调用 0。

## 样本和冻结规则

审计仓库与 `/tmp` 中 16 份可发现的 Doc2Dial `cases.jsonl`，排除 403 个已出现 group。冻结另外 200 个官方 test conversation，每个领域 50 条，与排除清单交集为零。这里的“未消费”限于审计记录，不能证明远端或未留记录的历史从未使用过。

剩余符合条件的样本没有长文档。四个领域的 short/medium/long 数量分别为 DMV12/38/0、SSA40/10/0、StudentAid47/3/0、VA25/25/0。语料仍保留全部 488 篇文档。因此这份验收不能覆盖长文档问题，也不是中文电商验收。

冻结提交 `9b71289` 保存数据、排除审计、模型文件身份与配置。查询 history、512/64 结构切块、flat、Dense0.25/BM250.75、各路20/融合20/输出5均预定。只比较相同候选池的无精排与本地完整输入 CrossEncoder，不扫描其他策略。

## 向量化前发现的实际导入缺陷

第一次执行在切块阶段失败，模型推理尚未开始。一篇纯文本中以 `* * *` 开头的520估算token段落，被识别成不可拆分的 Markdown 列表。

症状是预算超限；共享机制是 `SourceDocument.source_type` 已有 text/markdown/json，但进入切块 owner 时没有传递，于是纯文本也被赋予 Markdown 语法。修复是贯通格式，而不是给该文档加例外或提高预算：

- 生产导入按权威来源类型调用 DocumentChunker；text/json 的内容保持字面含义，markdown 保留表格、列表和代码围栏保护。
- 标题路径提取遵守同样的来源类型。原文字节、位置和 token 上限不变。
- 通用评测导入、切块、检索文本、证据恢复和工具消息同步传递格式。
- 索引 chunk schema 更新为 `knowledge-direct-ingest-v4-source-format`，旧索引需要按新身份重建，不能把旧投影误认成新实现。
- 真正超限的 Markdown 仍返回有类型的导入失败；失败导入不改变已有 active generation。

保留了失败记录及验收协议补充。补充发生于第二次模型推理开始之前；检索参数、样本和模型不变。修复后全部488篇文档产生1469个chunk，340个gold spans完整包含率100%，字符覆盖、来源偏移与512token预算检查通过。

## 结果与预先约定的判定

主要指标为模型实际 ToolMessage 的完整证据覆盖。成功规则为净增益为正，且精确双侧 McNemar p<0.05，并通过候选池相同、原文证据、包含率及零外部调用检查。判定器从实际工具消息与原文标注重新计算覆盖，不直接信任报告中的布尔值。

| 阶段 | 无精排 | 本地精排 |
|---|---:|---:|
| 候选完整证据 | 183/200 | 183/200 |
| 工具消息完整证据 | 169/200（84.5%） | 171/200（85.5%） |

精排救回8条、误伤6条，净增1个百分点；精确双侧p=0.790527。判定为 `EVIDENCE_QUALITY_NOT_DEMONSTRATED`，不能发布为稳定收益。开发集的42→44/56与本次169→171/200不是同一分布，不应合并样本或直接比较百分比。

本次仍是本地检索组件回放：BGE-M3、Python BM25、精确向量排序、CrossEncoder、生产打包与序列化。没有运行真实 Conversation Agent、生产 listwise reranker、最终生成或业务工具路由。

## 复现与证据

数据、协议、失败记录和判定在 `artifacts/eval/rag-d-acceptance-2026-09-06/`。运行：

```bash
.venv/bin/python scripts/run_rag_provider_free.py \
  --dataset artifacts/eval/rag-d-acceptance-2026-09-06/dataset \
  --acceptance-contract artifacts/eval/rag-d-acceptance-2026-09-06/acceptance-contract.json \
  --model /path/to/local/bge-m3 \
  --reranker /path/to/local/bge-reranker-v2-m3 \
  --cache /path/to/local/cache --synthetic-cases 0 \
  --output /path/to/new-output
```

模型身份与冻结协议必须一致；输出目录必须不存在。已保存逐例排序、工具消息、scores及checksum；gzip文件解压后可对照运行manifest。`scripts/decide_rag_acceptance.py` 从固定目录结构复核判定，输出必须为新文件。

## 后续约束

200条从本次执行后视为已消费，只用于回归或明确标记的失败分析，不再作为新鲜测试集调参。下一阶段补真实查询表达、生产入口与证据使用的验证，继续优先本地执行。全链路生成和中文电商语义验收仍是开放项；当前没有依据改成更复杂的默认检索策略。
