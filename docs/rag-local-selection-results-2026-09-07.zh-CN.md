# RAG 分层实测：融合、父子、上下文扩展与精排

2026-09-07。状态：开发筛选完成，外部验收及完整 Agent 链路仍未完成。原始数据与脚本已保留，不将开发集结果当上线准确率。

## 本轮主要结果

同一 Doc2Dial 开发集 300 问题、100 文档、314 个 structure-aware 512/64 child；raw query、本地 BGE-M3 exact dense 与 Python BM25；每路 40、融合候选 20、最终最多 5 片段，生产 TokenEstimator 估算 2,600 token 上限。这个估算器不是 Flash tokenizer。

| 顺序 | 方案 | 打包后完整证据 | 相对前一步救回/误伤 | MRR@5 | nDCG@5* |
|---|---|---:|---:|---:|---:|
| 1 | Dense/BM25=.25/.75，RRF k=10 | 182/300，60.67% | — | .4736 | .5078 |
| 2 | Dense/BM25=.5/.5，RRF k=10 | 192/300，64.00% | 13/3 | .4839 | .5239 |
| 3 | 同候选池＋本地 BGE-reranker-v2-m3 全文精排 | **208/300，69.33%** | 20/4 | **.5840** | **.6117** |

累计救回 29、误伤 3，完整证据净增 **8.67 个百分点**。本地 6,000 个 query-child 精排对，最大输入 583 tokenizer tokens，未用 1,200 字符截断，外部 API 0。权重是在本开发集选择，以上不是未见集提升。

完整证据要求每个 gold span 都被至少一个所选片段完整包含。MRR/nDCG 在打包前 Top-5 计算；不能与打包完整证据混称。*沿用项目指标：相关项必须覆盖 gold span，并按来源文档去重；不是标准 passage-level qrel nDCG。Doc2Dial 单文档问题中它主要反映首个含证据片段的位置。不能直接对比公开排行榜。

## 已比较的其他方法

| 实验 | 对照结果 | 本轮决策 |
|---|---|---|
| PG 固定 512/64 vs structure 512/64 | 完整候选均 207/300 | 结构切分未产生完整证据净增益 |
| structure 256/32、384/48 | 完整候选 189、203/300 | 不因为切块更小就替换 |
| 全局 child＋最多 3 个 parent 内补检索 | flat 207，parent 203；救 3 伤 7 | 不推广此补检索配置 |
| 15 组固定融合参数 | 候选20从 211 到 223/300 | 保留 .5/.5、k=10 作为待外测候选 |
| 按编号/短句/自然问法动态调权 | 会话分组五折：固定 218，动态 218；救 1 伤 1 | 动态未胜出 |
| 精排后扩相邻 child 窗口 | 同打包预算 208→202 | 本配置挤占其他证据，不启用 |
| 精排后扩整个 parent，超预算退回 child | 同预算 208→201 | 本配置不启用 |

前两类 PG/parent 实验每路20，本轮融合实验每路40，不能跨表把不同预算数字直接当收益。动态调权只测试轻量类别选择，并未测试所有学习式路由；分组五折只用于动态与固定的比较。

父子定位与取回父级上下文是不同策略。此次邻居扩展使用相邻 child，借鉴窗口思想，并非逐句建立索引的 sentence-window 实现。主流框架确实提供 [sentence-window](https://docs.llamaindex.ai/en/v0.10.22/examples/node_postprocessor/MetadataReplacementDemo/) 与 [auto-merging](https://docs.llamaindex.ai/en/v0.10.17/examples/retrievers/auto_merging_retriever.html)；本地精排采用 [BAAI 官方 reranker](https://huggingface.co/BAAI/bge-reranker-v2-m3)。这些是方法依据，不是该策略必然优于简单方案的保证。

## 20 条 Flash 实际生成对照

调用前按会话哈希选20组，每组取最大历史长度的一个问题，基线和候选各一次，共40次；模型为配置中的 deepseek-v4-flash、reasoning none。使用现行生产 compose provider 与 renderer，传入历史和打包证据；未运行 Conversation Agent 路由、业务工具或 verifier，因此准确名称是 composition replay。

| 指标 | 基线 | 候选 |
|---|---:|---:|
| 打包完整证据 | 14/20 | 16/20 |
| 合法结构化草稿 | 18/20 | 18/20 |
| 协议失败 | 2/20 | 2/20 |

不能把18/20称为答案正确率。四次失败均捕获到 submit_composed_response 的空参数 `{}`，stop_reason=tool_use；捕获并不能确定空参数由模型还是供应商适配产生。没有靠重试删除这些失败。

逐例阅读显示证据收益有转移，也有阻断：

- cases.jsonl 顺序第18条（索引17），私人贷款问题：基线偏到联邦贷款债务服务；候选回答比较联邦与私人贷款、检查条款，覆盖参考需要。
- 第19条（索引18），拒绝 hearing waiver：基线协议失败；候选明确须举行 hearing，但回答偏长。
- 第17条（索引16），遗属保障：候选已取得 gold 片段，却在结构化输出失败，收益没有传到用户。
- 第8条（索引7），ED回复后的利息问题：两组均未覆盖参考中的利息资本化后果，仍有查询/证据问题。
- 第20条（索引19），历史问2019、证据解释2018：候选省掉年份，暴露适用时间可能被泛化的风险。

以上是样例诊断，不是独立人工标注的准确率。部分 gold 只有标题或半句，精确 span 未命中也可能找到等价证据，因此不能用 span recall 直接替代答案评分。

## 验证和复现

运行时使用仓库根目录及 `PYTHONPATH=.`。本地分层命令：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_rag_local_selection_stages.py \
  --dataset artifacts/eval/doc2dial-rag-mini-dev-v1 \
  --scores artifacts/eval/rag-local-parent-pair-v2-2026-09-07/scores.npz \
  --reranker /home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3 \
  --output /tmp/rag-selection-reproduction
PYTHONPATH=. .venv/bin/python scripts/verify_rag_selection_artifacts.py
```

`run_rag_selected_composition_pair.py` 接受相同 dataset、上述 stages 目录与新的 output 目录；该脚本会实际调用40次 API。现有压缩捕获可离线读取，无需重新付费。模型/数据摘要见 manifest；缓存身份、固定投影顺序来自上一轮 parent 对照，不能替换同形状的其他分数矩阵。

验证重算 2,700 个来源边界/预算/完整证据组合、所有配对救回误伤、会话折隔离与模型输入原文一致性。细节见 artifacts/eval/rag-local-selection-stages-2026-09-07/validation.json。

## 尚未完成的验收

本轮没有改变线上默认配置。MTRAG/WixQA已经锁数据身份，但尚未外测；query rewrite、并行 PG 性能、权限/版本过滤及实际 ToolMessage/最终发布的全链路也没有被这些结果证明。

下一阶段以现有候选继续：先定位 composition 空参数边界并补测时间条件保持；再做同入口的 query 上下文实验；冻结策略后运行 MTRAG/WixQA 的已封存数据。生成仍只选少量样本，检索评估复用本地缓存。不得把这些未完成项写成“RAG全链路优化完成”。
