# G4：完整语料的 Dense 与同预算融合结果

2026-09-08。沿用冻结32个开发conversation、官方rewrite表达和四域完整非空语料；未触碰heldout查询。BGE-M3本地完成366,438片段编码，耗时约31.4分钟，API0。五组融合仅重放分路排名，不重新编码或生成答案。

## 同预算结果

每路Top20、最终Top20、RRF k=10，复用生产 `fuse_rankings`。表中权重为Dense占比。

| 方案 | Recall@5 | Recall@20 | MRR@20 | nDCG@20 | 至少一个相关片段 |
|---|---:|---:|---:|---:|---:|
| BM25，0 | 28.91% | 50.26% | .3168 | .3329 | 21/32 |
| 当前权重，.25 | 31.77% | 50.26% | .3445 | .3505 | 21/32 |
| 均衡，.5 | 38.28% | **63.02%** | .4099 | .4284 | **25/32** |
| 偏Dense，.75 | 45.57% | 60.00% | **.4978** | **.4787** | **25/32** |
| Dense，1 | **46.09%** | 60.00% | .4871 | .4667 | **25/32** |

均衡相对当前权重，Recall@20 **+12.76个百分点，10题提高、1题降低**；MRR提高12题/降低4题，nDCG提高15题/降低3题。这里“提高”按相关passage比例计，不等于完整答案救回。对至少命中一个相关片段的指标，净增加4题。

候选覆盖最佳的是.5，前排MRR/nDCG最佳的是.75；尚未精排，不能只挑一个数字宣布最优。32题已用于开发选择，不是独立验收。

| 领域（各8题） | 当前.25 R@20 | .5 R@20 | .75 R@20 | Dense R@20 |
|---|---:|---:|---:|---:|
| ClapNQ | 50.00% | 72.92% | 64.58% | 64.58% |
| Cloud | 47.92% | 61.46% | 55.21% | 55.21% |
| FiQA | 41.67% | 54.17% | 59.79% | 59.79% |
| Govt | 61.46% | 63.54% | 60.42% | 60.42% |

不能用每域只有8题的局部最优直接配置动态路由权重。

## 已复现的融合缺口与剩余失败

32/32题中，当前.25的候选集合与纯BM25完全一致；Dense只改变了其中顺序。该现象与此前G1的候选资格代数一致：每路20、k10时，Dense独有第1的贡献.25/11仍低于BM25第20的.75/30。当前配置不能指望Dense独有证据进入20候选。

两路合并最多40个候选的诊断Recall为72.24%，高于均衡20的63.02%；它是**更大预算的诊断参考**，不计为权重收益，也不等于完整语料的召回上限。还有4题两路Top20都未命中。

[逐例剩余失败](../artifacts/eval/rag-g4-mtrag-hybrid32-2026-09-08/remaining-failures.json)记录4条双路miss和1条均衡误伤。部分gold在Dense第27/55/79，另有100以内未见者；未见不能写成“语料没有”。均衡误伤例gold在Dense第38，BM25已召回但在融合中被替换。

需收紧“完整查询”的用语：这里始终使用的是**官方rewrite字段**，不是已人工证明完整的查询。失败中仍有“any other famous poets”“having hard time finding web chat”等上下文依赖或含糊表达。保留官方字段用于可比基准，但不能把所有剩余miss归因于embedding、也不能称已测得完美query下的上限。下一轮真实Agent查询验证仍需与其看到的原会话一起评估，不读gold改写。

## 输入与缓存审计

全部366,438行的来源顺序/正文hash、向量文件SHA、float32×1024、有限值和单位范数通过。每域完整计数与source manifest一致，0重用表示本轮首次编码；缓存已保存供后续复用。

各域实际最大token长度为ClapNQ743、Cloud5627、FiQA606、Govt1326，均在8192以内。没有默认截成512或1024。成本探针最大573只描述256样本，不代表全量；完整编码检查避免了该误推。

原等待句柄跨轮失效后，检查原PID已结束、progress=COMPLETE且结果存在，直接审计结果；没有重启全量编码。

18项相关测试通过，包含缓存故障注入、同query/同域/同budget校验、分片TopK等价性和实际32题指标重算。审计证明编码产物合同，不是模型语义正确性证明。没有PG查询、ANN、精排、打包、ToolMessage或答案生成，不能写成线上回答准确率提升。

## 复现与下一步

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_mtrag_dense_shards.py \
  --cache /tmp/dialogpilot-mtrag-dense-full-20260907 \
  --corpora /tmp/dialogpilot-mtrag-corpora-20260907 \
  --manifest artifacts/eval/rag-g4-mtrag-adapter-2026-09-07/manifest.json \
  --output /tmp/mtrag-dense-audit-new
PYTHONPATH=. .venv/bin/python scripts/replay_mtrag_hybrid_weights.py \
  --dense artifacts/eval/rag-g4-mtrag-dense-complete-2026-09-08 \
  --lexical artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07 \
  --output /tmp/mtrag-hybrid-new
```

小型排名与身份已提交，融合重放不依赖大向量缓存。大缓存全审计需要保留本地向量与原始zip。

产物：[Dense](../artifacts/eval/rag-g4-mtrag-dense-complete-2026-09-08/report.json)、[全缓存审计](../artifacts/eval/rag-g4-mtrag-dense-audit-2026-09-08/report.json)、[融合表](../artifacts/eval/rag-g4-mtrag-hybrid32-2026-09-08/report.json)。

下一项固定当前.25/.5/.75三组候选，复用同一批问题做本地CrossEncoder精排Top5对照，按query/passage唯一pair缓存以控制成本。不微调、不改生产权重。另保留4条双路miss的查询语义及标注适配问题；精排无法救回未进入候选的证据。最终仍需真实工具可见与答案验证、独立分组验收。
