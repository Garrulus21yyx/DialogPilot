# 100题：原始上下文→Flash文本→检索→精排→工具可见证据

2026-09-08，基点b35fc07。按用户要求完成此前固定100题的完整纯查询检索链。前20题在system、历史角色、原文和当前输入逐项一致时复用，新增80次Flash，无重试或换题。模型、prompt、512输出预算保持原样。

## 本轮实际范围

官方Doc2Dial完整原始历史（user/agent角色从test archive核实）＋当前消息 → Flash NONE纯查询任务 → 生成文本 → BGE-M3 Dense与BM25各20 → 等权RRF(k10)保留20 → 本地BGE精排 → 最多5块/2600 tokens → 实际EvidencePack/ToolMessage序列化。

固定官方488篇、1469个512/64结构切块。没有让Flash选择电商工具，没有走生产最近8消息Loader或跨会话memory，没有调用答案生成器。这里“完整链”限上下文给模型到检索最终证据，不是电商Agent或最终问答全链。

## 同100题的结果

| 完整证据覆盖 | 原句 | Flash生成文本 | 人工完整query |
|---|---:|---:|---:|
| Dense20 |49%|91%|98%|
| BM25 20 |61%|95%|97%|
| 并集≤40 |66%|96%|100%|
| 融合20 |65%|96%|98%|
| 精排前5 |58%|92%|96%|
| 最终工具可见 |58%|92%|96%|

| 最终排序指标 | 原句 | Flash生成文本 | 人工完整query |
|---|---:|---:|---:|
| MRR |0.4313|0.7187|0.7697|
| nDCG |0.4711|0.7698|0.8205|

Flash相对原句救回36题、误伤2题，净增34点。100题未删任何失败，输入/协议无失败。集合覆盖的定位：4题两路并集就不完整；其余4题进入候选但未在前5完整保留；pack没有额外损失。人工96与Flash92的差不能简单称为4题生成失败，因为各自可能救回不同题。

## 高Recall不能掩盖输出合同错误

提示要求一条检索问题，但Flash20条输出了答案型文字。这20条拿去检索全部获得完整证据；其余80条输出中72条获得完整证据。不能因20条答案检索成功就称其query合格，也不能称其他80条全部语义合格。

例如：
- 用户追问某时期无保险事故罚金，Flash直接输出金额；该金额没有经过本轮检索验证。
- 用户说曾结婚，Flash写“从未结婚的残疾成年人可以网上申请SSI”，既是答案又改变否定条件。
- 用户最新问Medicare医疗服务信息，Flash返回旧的残疾福利付款时间问题，本例候选未找到所需证据。
- “how can I get more information?”没有补齐Social Security主题，仍作为短句进入后端，候选缺失。

`semantic-review.json`记录明确发现的答案型与语义错误，不是独立全量标注。当前`single_query=100`在旧评测器中的含义只是“有一个非空字符串”，不表示语义正确；报告在这里明确称生成文本。不能将92%用作“query正确率”或“最终答案正确率”。

这批数据已被人工、Agent和其他检索诊断消费；不是新的封存验收。本次不按结果改写prompt或替换模型答案，没有生产采用或部署。

## 费用、检查和复现

模型推理共100份记录，复用20、新增80。新增调用input_tokens27007/output_tokens2235；本地精排2000对。来源上下文、复用输入、生成文本与评分原文、各层集合和pack/wire逐例审计通过，源码diff检查通过。

目录：`artifacts/eval/rag-pure-query100-2026-09-08/`

- captures.jsonl：完整模型输入与输出；前20有reused标记。
- retrieval.json：每题query、两路排名、完整CE顺序、最终片段、原句/人工/Flash对照。
- scores.json.gz：query、候选、分数和原文hash。
- report.json/paired.json：分层指标及救回误伤。
- semantic-review.json：明确输出问题。
- audit.json：逐例审计与新增调用tokens。

执行：`PYTHONPATH=. .venv/bin/python scripts/run_rag_pure_query100.py`；检索：`PYTHONPATH=. HF_HUB_OFFLINE=1 .venv/bin/python scripts/evaluate_rag_agent_context20.py --output artifacts/eval/rag-pure-query100-2026-09-08`；只复核不调用模型：`PYTHONPATH=. HF_HUB_OFFLINE=1 .venv/bin/python scripts/audit_rag_pure_query100.py`。运行入口拒绝覆盖已有记录。

本步已完成。下一步若要接生产，须先验证query合同：输出检索表达，保留本轮主题和假设/否定；不能靠检索分数把答案型输出放行。这里未增加额外Agent、核验模型或调整检索权重。
