# 本地精排部分参数微调：首轮结果

2026-09-07。结论：本轮未验证出精排收益，保留现有生产默认。外部 API 调用 0；不是生产入口评测，也不是中文电商验收。

## 固定实验合同

- 模型：本地 `BAAI/bge-reranker-v2-m3`；只更新第 22、23 编码层（零起算）和分类头，其他参数冻结。不是全参数微调。
- 数据：Doc2Dial v1.0.1 官方 **train**，80 条训练、20 条开发、40 条验收，每个来源组最多一条。采用首个 user turn 原文，不进行上下文改写，也不使用后续 agent 答案作为查询。
- 切块：structure-aware 256/32；query + 标题 + 正文最多 768 模型 tokens，超预算案例排除，不截去证据。此配置为显存受限的训练试验，不等同生产 512/64。
- 候选：Python BM25 Top-20，自然召回，不注入 gold；开发和验收使用同一冻结候选池比较原模型与微调模型。训练负例检索仅使用训练来源组。
- 正例：完整包含至少一个官方标注 span 的片段；完整证据指标另要求覆盖全部 spans。负例为 BM25 靠前、无 gold 重叠的未标注片段，属于**弱监督**，不是人工核实的负例。
- 训练：固定 1 轮、80 步、学习率 1e-5，每步一个正例和一个困难负例，pairwise cross-entropy，FP16 autocast、梯度缩放及裁剪；关闭 dropout。seed=71。
- 开发选择规则预定为 nDCG@5 严格提高；本轮未达标。验收结果不用于再次选学习率或轮数。

## 验收结果

| 指标 | 原始 BGE | 微调 BGE | 变化 |
|---|---:|---:|---:|
| 完整候选证据 R@20 | 25/40，62.5% | 25/40，62.5% | 0 |
| 完整证据 R@5 | 24/40，60.0% | 24/40，60.0% | 0 |
| MRR@5 | 0.45625 | 0.45625 | 0 |
| nDCG@5 | 0.48807 | 0.48807 | 0 |

开发集也没有提升：两模型完整证据 R@5 均为 16/20，MRR@5 均为 0.63333，nDCG@5 均为 0.67619。验收完整证据救回 0、误伤 0。

MRR 使用首个包含标注证据的片段位置；nDCG 使用二值片段相关性，其理想排序分母来自语料中所有 gold-containing chunks，而非只从召回池计算。它与先前生产报告的项目文档去重 nDCG 口径不同，不能横向混比。

15 条验收问题在候选阶段已经缺少完整证据；原精排保住了候选完整的 25 条中的 24 条。因此，完整覆盖上的可救回空间只有 1 条。不能把这轮零收益推导为“微调无效”，也不能据此继续盲目增加训练量。本轮的实际决策是**不推广这个 checkpoint**。

## 隔离审计与首轮无效记录

第一轮只按整篇正文分组，训练与验收文档不同，但移除标题后发现 **1 个验收正例与训练片段正文完全重复**。该轮 MRR 的微小变化不作为泛化收益，记录保存在 `pilot_invalid/`。

第二轮在数据构建处修复分组：共享任意规范化正文片段的来源文档通过连通分量归入同组，再划分训练、开发、验收；排除首轮已经观察过的文档组参与新开发／验收。训练参数不变。

新一轮审计结果：

- 三个 split 的来源组交集为 0。
- 开发／验收正例与训练 pair 的规范化正文完全重复数为 0。
- 验收正例与训练 pair 的词级 5-shingle Jaccard ≥ 0.8 筛查命中数为 0；这是近重复筛查，不证明没有语义改写重复。
- 36 个参数张量发生变化，均属于最后两层和分类头；所有其他权重不变。因此零指标收益不是“训练根本没有更新”。

原始与微调权重都可能具有预训练数据暴露；这里的隔离指本次领域微调的数据隔离，不声称对基础模型全新。

## 成本和产物

本轮新分组训练循环约 2.33 秒，CUDA 峰值分配约 2.524 GiB。这个时间**仅是优化器训练循环**，不包括数据构建、模型加载、原模型及微调模型推理评测、保存 checkpoint；不代表整场实验只花了两秒，也不是线上延迟。

结果目录：`artifacts/eval/rag-reranker-finetune-2026-09-07/`，含两轮数据快照、原始排名、指标、来源 SHA256、分组及权重审计。模型权重不提交 Git，保存在：

`/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3-doc2dial-pilot-20260907`

复现第二轮（输出目录必须不存在，先将保留的第一轮 data.json.gz 解压到工作目录）：

```bash
gzip -dc artifacts/eval/rag-reranker-finetune-2026-09-07/pilot_invalid/data.json.gz > /tmp/reranker-prior-data.json
PYTHONPATH=. .venv/bin/python scripts/run_rag_reranker_finetune.py \
  --raw /path/to/doc2dial/data \
  --model /path/to/bge-reranker-v2-m3 \
  --exclude-eval /tmp/reranker-prior-data.json \
  --output /tmp/reranker-fresh-reproduction
```

原始资料与许可：[Doc2Dial v1.0.1](https://doc2dial.github.io/file/doc2dial_v1.0.1.zip)，CC-BY-3.0。训练方式参考 [BGE 官方微调说明](https://github.com/FlagOpen/FlagEmbedding/blob/master/examples/finetune/reranker/README.md)；本脚本直接使用 Transformers，实现有界的部分参数训练，没有调用官方训练器。

后续应回到完整查询和混合召回，提高并确认候选覆盖，再用确有排序困难、经过审查的训练负例评估微调。当前结果不支持替换 Flash，也不能与之前 20 条已编写完整查询的生产入口实验混为一组。
