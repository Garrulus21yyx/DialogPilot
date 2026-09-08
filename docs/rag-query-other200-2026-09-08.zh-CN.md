# MTRAG与WixQA各100题：查询生成至精排证据补测

2026-09-08，起点95f42f1。补齐用户要求的另两套100题：给模型实际原始输入，生成查询文本，再走冻结的检索、精排和工具证据输出。全部样本保留，不按结果换题或重试，不改生产。

## 输入与运行范围

- MTRAG：此前固定100题、42个会话，使用官方reference.jsonl的input（历史user/agent和当前问句），不把官方rewrite、contexts或targets传给模型。与同题官方rewrite检索基线比较。历史助手原文按标准对话输入保留，可能本身存在事实错误。
- WixQA：此前固定100题、100个文章关联组，原始问题，没有编造多轮历史。与原问题检索基线比较。
- 模型：deepseek-v4-flash，NONE，512输出tokens，和Doc2Dial纯query实验相同提示，每题一次，共200次。提供完整数据历史，不走电商工具选择、生产最近8条Loader或跨会话记忆。
- 后端：MTRAG使用原领域全库366438段；Wix6221篇11167块。既有向量缓存，Dense/BM25各20、等权RRF k10融合20、本地BGE CE、最终5块2600 tokens、真实ContextPacker/EvidencePack/ToolMessage。没有最终答案生成。

## 最终结果

| 数据集/指标 | 固定原基线 | Flash生成文本 |
|---|---:|---:|
| MTRAG片段Recall@5 |45.00%|48.31%|
| MTRAG MRR@5 |0.5405|0.5382|
| MTRAG nDCG@5 |0.4375|0.4458|
| WixQA文章Recall@5 |70.83%|66.83%|
| WixQA MRR@5 |0.5390|0.5112|
| WixQA nDCG@5 |0.5615|0.5284|

MTRAG改善19题、退步15题，宏平均Recall净+3.31点；以42个会话聚类bootstrap20,000次，95%区间[-2.17,+9.02]点。Wix改善3题、退步9题，净-4点，文章组bootstrap区间[-10,+2]点。两个区间均跨0，不声称稳定提升或显著退步。本批已消费，不是全新验收。

## 分层覆盖（宏平均Recall）

| 阶段 | MTRAG官方query | MTRAG Flash | Wix原query | Wix Flash |
|---|---:|---:|---:|---:|
| Dense20 |60.47%|69.22%|82.50%|79.50%|
| BM25 20 |34.59%|43.22%|57.50%|59.67%|
| 两路并集≤40 |66.49%|72.61%|90.00%|85.50%|
| 融合20 |58.59%|66.39%|79.67%|80.50%|
| CE前5 |45.34%|48.65%|70.83%|66.83%|
| 实际工具可见 |45.00%|48.31%|70.83%|66.83%|

MTRAG候选改善并没有全部传到最终前5；Wix融合20覆盖略增但最终退步，不能仅看候选数字采用。不同单位不能平均成整体RAG准确率。

## 输出合同仍有失败

200份输出均为非空字符串且无API/截断协议失败；这不代表200条query语义合格。

MTRAG明确案例：长城防御对象和德国停止援助的问题被直接写成一大段答案，而非检索问题。Wix至少3题返回了完整system提示词本身（expertwritten:167、expertwritten:197、simulated:2），另有一题直接解释订阅/取消订阅原因。均原样保留和评分，没有偷偷换回原query以提高结果。

因此不能把48.31%或66.83%叫查询正确率。semantic-witnesses.json只记录明确失败，不是全量语义标签；未评估这些答案型输出的事实正确性。

## 采用结论

- MTRAG：候选及最终Recall有开发收益，但排序/语义未一致改善，置信区间也不支持稳定增益；本轮不替换生产或官方基线。后续应先处理输出语义合同，再以未调参数据确认。
- WixQA：这版一律改写未改善最终指标；保留原问题基线，不采用。未来如验证按需规范化，必须保留原问题，并单独评估是否值得增加模型调用，不能直接把它当作已证实方案。

本步是补测交付完成，不是RAG整体质量关闭。不恢复微调，不调整未授权业务流程。

## 费用与审计

新模型调用200；MTRAG输入58722/输出3210 tokens，Wix输入14964/输出2155。新增本地CE4000对，无文档重新向量化。全部官方input来源、模型实际请求、query评分原文hash、候选顺序、pack/wire与旧基线记录核对通过。源码diff检查通过。

产物目录：`artifacts/eval/rag-query-other200-2026-09-08/`。inputs和captures保存原始输入及每次请求/输出；各数据子目录cases/scores/report保留分路排名、20候选、精排顺序、最终证据与指标；summary含分组区间；audit含来源校验与tokens。

复现入口：

- `PYTHONPATH=. .venv/bin/python scripts/run_rag_other_query200.py`
- `PYTHONPATH=. HF_HUB_OFFLINE=1 .venv/bin/python scripts/evaluate_rag_other_query200.py MTRAG`（或WixQA）
- `PYTHONPATH=. HF_HUB_OFFLINE=1 .venv/bin/python scripts/audit_rag_other_query200.py`
- `PYTHONPATH=. .venv/bin/python scripts/summarize_rag_other_query200.py`

原有检索runner仅增加可选cases输入，默认读取既有冻结selection的行为不变；没有新增生产检索分支。生成与检索入口拒绝覆盖已有结果。
