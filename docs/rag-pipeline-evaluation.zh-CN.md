---
layout: page
title: 客服 RAG 全链路评测
permalink: /rag-pipeline-evaluation/
---

# DialogPilot 客服 RAG 全链路评测

> 本页保存 Doc2Dial 上选择检索配置与 grounded v3 的历史实验依据。当前仓库随后增加了 public `SourceDocument`、持久 Sparse、完整 IndexManifest、EvidencePack、严格 rerank permutation 与 grounded v4 claim/conflict 合同；这些新增合同不能继承 v3 的模型分数。上线边界与待补证据见[客服 RAG 生产化审计](../customer-service-rag-production-audit/)。

## 1. 目标与边界

本评测把 source document 与 evidence span 作为权威事实，逐层区分：

```text
文档解析/Chunk → Query 变换 → BM25/Dense → RRF → Rerank
→ Context Packing → Generation/Citation
```

Chunk ID、检索排名、最终回答都是投影，不能替代原文 `document_id + [start_char, end_char)`。
配置只能在 Dev 上选择；Heldout 只报告，评测代码不会给出推荐配置。

## 2. 小规模真实客服数据

使用官方 Doc2Dial v1.0.1 的确定性子集：

- 100 篇文档；
- 300 个用户问题/客服回答 turn；
- 4 个服务领域均衡抽取；
- 488 个官方 grounding span；
- 相关文档之外补入确定性 distractor；
- 不在仓库提交第三方大语料，生成物位于 `artifacts/eval/`。

```bash
PYTHONPATH=. .venv/bin/python scripts/build_doc2dial_rag_subset.py \
  --output artifacts/eval/doc2dial-rag-mini-dev-v1 \
  --split dev --max-documents 100 --max-cases 300
```

## 3. Chunk 选择

先运行不依赖检索器的 evidence-preservation 评测：

```bash
PYTHONPATH=. .venv/bin/python -m evaluation.rag_chunk_ablation \
  artifacts/eval/doc2dial-rag-mini-dev-v1 --split dev \
  --output artifacts/eval/doc2dial-rag-mini-dev-v1/chunk-ablation.json
```

当前结果：

| 配置 | Gold span containment | fragmentation | chunks |
|---|---:|---:|---:|
| fixed 256/32 | 0.9918 | 0.0082 | 588 |
| fixed 384/48 | 0.9980 | 0.0020 | 399 |
| fixed 512/64 | 1.0000 | 0.0000 | 314 |

结构切分 512/64 与 fixed 512/64 在本子集的结果相同，因此按“质量相同时选择更简单策略”的成本顺序选择 fixed 512/64。该结论只适用于当前 Dev corpus；中文 FAQ、表格或 Markdown 仍需单独验证。

评测过程中发现并修复了旧 chunker 对全文 `strip()` 导致原始 evidence offset 漂移的问题。生产 KnowledgeBase 与评测现共用 `DocumentChunker`，并记录 source offsets；生产默认切换后 chunking version 升至 4。

## 4. 检索权重与 RRF K

同一 case 的 BM25 和 Dense 排名只采集一次，再离线重放以下权重比例：

```text
BM25/Dense = 1/0, 0.75/0.25, 0.5/0.5, 0.25/0.75, 0/1
RRF k      = 10, 30, 60（混合配置）
```

因此不同权重不会因为重复 embedding 或模型随机性而获得不同输入。权重只比较比例，因为 RRF 的公共倍数不改变排序。

```bash
PYTHONPATH=. .venv/bin/python -m evaluation.rag_retrieval_ablation \
  artifacts/eval/doc2dial-rag-mini-dev-v1 --split dev \
  --chunk-strategy fixed_tokens --chunk-max-tokens 512 \
  --chunk-overlap-tokens 64 --candidate-k 20 \
  --output artifacts/eval/doc2dial-rag-mini-dev-v1/retrieval-ablation-512-64.json
```

当前 Dev 结果：

| Chunk | 最佳融合 | Evidence Recall@20 | Document Recall@20 | MRR | nDCG@20 |
|---|---|---:|---:|---:|---:|
| 256/32 | BM25 .75 / Dense .25 / k=10 | 0.5622 | 0.7567 | 0.3533 | 0.4045 |
| 384/48 | BM25 .75 / Dense .25 / k=10 | 0.5944 | 0.7533 | 0.3439 | 0.4028 |
| 512/64 | BM25 .75 / Dense .25 / k=10 | **0.6244** | 0.7533 | **0.4054** | **0.4596** |

在 512/64 下，推荐融合相对 BM25-only：

- Evidence Recall 差值 `+0.0606`，group-paired bootstrap 95% CI `[+0.0319, +0.0911]`；
- MRR 差值 `-0.0149`，95% CI `[-0.0415, +0.0121]`，不能证明有稳定退化；
- nDCG 差值 `+0.0035`，95% CI `[-0.0174, +0.0246]`，不能证明有稳定差异。

选择 `.75/.25` 的理由是 first-stage 的主要合同为证据覆盖，且它相对 BM25-only 的召回增益置信区间整体大于零。相对 `.5/.5, k=10`，Recall 差异不显著，但 MRR 与 nDCG 的差值置信区间整体大于零，因此选择更偏 BM25 的 `.75/.25`。`k=10` 与相同权重的 `k=30` Recall 一致，但 MRR 差值 `+0.0550`、95% CI `[+0.0354, +0.0771]`，所以不沿用默认 `k=60`。

三组检索曾并行执行，报告里的 wall-clock latency 受到资源竞争影响，当前不能作为跨 chunk 配置的验收证据；正式延迟比较必须单进程顺序重跑并记录机器环境。

## 5. Query Transformation

模型输出只捕获一次，权重通过离线重放选择。压力子集从 52 个 dialogue 中按四个服务域 round-robin，每个 dialogue 最多一个 case，并优先选择 history 最长的 turn，共 48 case。它用于暴露指代和省略问题，不代表线上流量比例。

```bash
set -a; source .env; set +a
PYTHONPATH=. .venv/bin/python -m evaluation.rag_query_capture \
  artifacts/eval/doc2dial-rag-mini-dev-v1 --max-cases 48 \
  --expansion-count 2 --concurrency 3 \
  --output artifacts/eval/doc2dial-rag-mini-dev-v1/query-transform-capture.json

PYTHONPATH=. .venv/bin/python -m evaluation.rag_query_ablation \
  artifacts/eval/doc2dial-rag-mini-dev-v1 \
  artifacts/eval/doc2dial-rag-mini-dev-v1/query-transform-capture.json \
  --output artifacts/eval/doc2dial-rag-mini-dev-v1/query-ablation-report.json
```

每个策略比较 raw query mass `0.75 / 0.50 / 0.25`。剩余权重平均分给生成 query；普通 query 内部继续使用已选出的 BM25/Dense `.75/.25`，HyDE 只进入 Dense 且永远不能作为回答证据。安全门槛在看质量前检查：保留 raw、实体保留 ≥ .95、否定保留 ≥ .95、虚构实体 ≤ .05、相对 Raw 的 harmful case ≤ 10%。

| Query 策略 | Raw mass | Recall@20 | MRR | Harmful | 否定保留 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| Raw | 1.00 | 0.6667 | 0.3662 | 0 | 1.0000 | baseline |
| Standalone | 0.75 | 0.7292 | 0.3843 | 0 | 0.9583 | eligible |
| Standalone | 0.50 | 0.7500 | 0.4243 | 0 | 0.9583 | eligible |
| **Standalone** | **0.25** | **0.7708** | **0.4458** | **0** | **0.9583** | **selected** |
| Multi-query | 0.50 | 0.7708 | 0.4590 | 0.0208 | 0.9167 | reject |
| HyDE | 0.75 | 0.7708 | 0.3713 | 0.0208 | 0.9514 | eligible, lower MRR |
| All | 0.50 | 0.7917 | 0.4207 | 0.0208 | 0.9271 | reject |

推荐配置相对 Raw：Recall `+0.1042`，95% CI `[+0.0208,+0.1875]`；MRR `+0.0797`，CI `[+0.0196,+0.1477]`；nDCG `+0.0841`，CI `[+0.0262,+0.1505]`。所以 `.25/.75` 不是经验权重，而是通过安全约束后在 Dev 上三项质量均有正向区间的配置。Multi-query/All 的更高点估计不能覆盖否定词风险。

## 6. Rerank

`ResultReranker` 使用 stable chunk ID 返回完整 permutation；模型返回 JSON 对象或裸 ID 数组都在同一 typed boundary 校验，未知 ID 被拒绝，失败保留 first-stage 顺序。固定 Query 配置后，从 20 个候选 listwise 重排到 5 个：

| 配置 | Recall@5 | MRR@5 | nDCG@5 | Harmful vs no-rerank |
|---|---:|---:|---:|---:|
| no rerank | 0.5938 | 0.4330 | 0.4764 | — |
| LLM listwise | **0.7500** | **0.5903** | **0.6543** | 0.0208 |

三项 delta 的 95% CI 分别为 `[+0.0417,+0.2812]`、`[+0.0653,+0.2569]`、`[+0.0841,+0.2823]`。第一版只接受 JSON object，产生 6/48 typed fallback；确认供应商返回裸 ID array 后扩展解析合同并只重试失败 case，最终失败 0/48。门槛为 harmful ≤ 5%、typed fallback ≤ 1%，当前通过。

## 7. Context Packing

`ContextPacker` 严格执行 token/chunk 上限，并可根据同文档 source-offset overlap 去重。选择合同不是加权分：必须先保住 reranked Top-5 的 evidence recall `0.75`，再选择 token 更少者。

| 配置 | Evidence Recall | 平均 tokens | 平均 chunks |
|---|---:|---:|---:|
| Top-5 / 1200 | 0.6458 | 1091.7 | 2.98 |
| Top-3 / 1800 | 0.7083 | 1404.2 | 3.00 |
| Top-5 / 1800 | 0.7083 | 1695.6 | 4.15 |
| **Top-5 / 2600** | **0.7500** | **2296.7** | **4.96** |
| Dedup50 Top-5 / 2600 | 0.7500 | 2302.9 | 4.96 |

只有 2600 token 配置满足 evidence-preservation 合同；50% overlap 去重没有收益，因此选普通 Top-5 / 2600。

## 8. Generation 与 Citation

`GroundedAnswerGenerator` 只允许引用已打包 chunk ID，非拒答必须有引用，矛盾/非法输出至多修复一次后 fail closed。英文问题使用英文控制提示，中文问题使用中文提示，避免 system prompt 泄漏回答语言。

最终 v3（48 case）：

| 指标 | 结果 | Gate |
|---|---:|---:|
| Generator / Judge failure | 0 / 0 | ≤1% / ≤5% |
| Language match | 1.0000 | ≥.95 |
| Evidence citation recall（context 有 gold 时） | 0.9167 | ≥.90 |
| Judge grounded | 0.9792 | ≥.95 |
| Judge citation relevance | 1.0000 | ≥.90 |
| Judge correct / complete | 0.7917 / 0.7917 | correct ≥.75 |
| Abstention | 0.1042 | diagnostic |

`gold_evidence_citation_precision_when_available=0.6944` 只作 diagnostic：Doc2Dial 给的是客服回复使用的 grounding span，不是“所有可支持回答的 chunk”穷举集合，不能把额外有效引用直接判错。Judge 仍是次级指标，正式上线前需要从这 48 条中人工盲审一部分，计算与 Judge 的一致性；当前不能把模型自评当成唯一闭环。

V1/V2 报告保留了失败演进：V1 的中文提示导致跨语言 token-F1 异常；V2 虽要求跟随语言，language match 仍只有 .4375；V3 按 query language 路由模板后达到 1.0。这个过程没有改变上游上下文或降低 gate。

## 9. 为什么不使用单一总分

本实验使用 constraint-first + lexicographic selection，而不是人为写一个 `0.4*Recall + 0.3*Faithfulness + ...`：

1. 否定词丢失、非法引用、生成失败属于不可补偿的安全约束，不能用 Recall 抵消；
2. Retrieval 先最大化 evidence coverage，再比较 MRR/nDCG；
3. Context 必须保住已获得的 evidence，再最小化 token；
4. Generation 分别报告 grounded、correct、citation、failure 和 abstention。

权重只有 RRF 与 query mass，均通过固定捕获、离线网格和 paired bootstrap 选择；随后冻结到 Doc2Dial test split 报告，真实脱敏客服流量仍需复验。

## 10. 验证

```bash
PYTHONPATH=. .venv/bin/pytest \
  tests/test_rag_pipeline_evaluation.py \
  tests/test_knowledge_base_retrieval.py \
  tests/test_retrieval_ablation.py \
  tests/test_layered_eval_dataset.py -q
```

## 11. 冻结 Test 报告与生产接入

Dev 选择结束后，使用 Doc2Dial 官方 test split 构建 40 文档、48 case 的确定性子集。配置不再选择；`select_configuration()` 在 `heldout` 上始终返回 `recommended=null`。其中 Query/Rerank/Generation 为避免同一 dialogue 重复，只覆盖 9 个 group，因此这里只是小样本反证，不把它包装成高置信生产结论。

```bash
PYTHONPATH=. .venv/bin/python scripts/build_doc2dial_rag_subset.py \
  --output artifacts/eval/doc2dial-rag-mini-heldout-v1 \
  --split heldout --max-documents 40 --max-cases 48

PYTHONPATH=. .venv/bin/python -m evaluation.rag_retrieval_ablation \
  artifacts/eval/doc2dial-rag-mini-heldout-v1 --split heldout \
  --chunk-strategy fixed_tokens --chunk-max-tokens 512 \
  --chunk-overlap-tokens 64 --candidate-k 20 \
  --output artifacts/eval/doc2dial-rag-mini-heldout-v1/retrieval-heldout.json

# Query capture、query ablation、rerank 与 Dev 命令相同，只把 dataset/output 换为 heldout。
PYTHONPATH=. .venv/bin/python -m evaluation.rag_packing_ablation \
  artifacts/eval/doc2dial-rag-mini-heldout-v1 \
  artifacts/eval/doc2dial-rag-mini-heldout-v1/rerank-ablation-report.json \
  --fixed-config top5-2600 \
  --output artifacts/eval/doc2dial-rag-mini-heldout-v1/packing-ablation-report.json
```

冻结结果：

机器可读摘要及完整报告 SHA-256 见 [`docs/data/rag-heldout-summary-2026-09-01.json`](../data/rag-heldout-summary-2026-09-01.json)。

| 阶段 | Test 结果 |
|---|---|
| First stage（48 case） | BM25 .75/Dense .25/k=10 evidence Recall@20 `.6875`，MRR `.4711`；只报告，不重选 |
| Query（9 groups） | Raw 与 Standalone-raw25 Recall@20 均 `.6667`；MRR `.3648→.4537`；harmful `0` |
| Rerank（9 groups） | Recall@5 均 `.6667`；MRR `.4537→.6111`；harmful `0`，typed failure `0/9` |
| Packing | 冻结 Top-5/2600 保持 evidence recall `.6667`，平均 5 chunks / 2321 estimated tokens |
| Generation（9 groups） | generator/Judge failure `0/0`，language `1.0`，evidence citation recall `1.0`，Judge grounded `1.0`、correct `.7778` |

首次报告中 `know` 被旧 evaluator 以子串方式误识别为否定词 `no`。修复为英文词边界后，用同一模型 capture 离线重放，Standalone negation preservation 为 `1.0`；没有重调 Prompt 或权重。

仓库保留该冻结检索配置：`KnowledgeBase` fixed 512/64、BM25 .75/Dense .25/k=10；`MCPToolManager` Raw .25/Standalone .75、20→5；`/chat` 使用 Top-5/2600。之后的生产边界修复将在线全量 BM25 换为持久 posting index，增加 source/index contract 和 EvidencePack，并把生成输出收紧为 grounded v4。`agent-v1` 仅在仍是精确旧默认时原子迁移到内容寻址的 `agent-v2-rag-*`，自定义 Active 指针不会被覆盖；非空索引缺少任一 source/chunk/dense/sparse/scope 合同都会 fail closed，要求从权威原文重导。

当前状态是 **retrieval baseline integrated; v4 fail-closed but empirically unusable**。2026-09-01 的 48 条 Dev 真模型三路实验中，grounded v4 失败/拒答率为 `85.42%–89.58%`；父子 Chunk 虽将 Recall@20 `.8333→.9167`，但 multi-condition completeness `.8261→.7826`，harmful context `4.17%`。两个扩展候选均未过 Dev，因此保留 512/64 且不打开 untouched Heldout。详细见[客服 RAG 生产化审计](../customer-service-rag-production-audit/)。

随后增加的长文档结构预检不改变这一默认：`>=8000` 字符的 Doc2Dial span-Gold slice 中，父子方案把 packed evidence recall `.5278→.5833` 且 harmful `0`，但 multi-condition 不升、两次本地检索 P95 增长约 `6%–35%`；WixQA article-Gold 的 multi-article completeness `.7667→.7000`。这支持“长文档条件化扩展”的后续实验，不支持全库切换。脱敏结果见[长文档摘要 JSON](../assets/eval/rag-long-document-dev-v1.json)。

全仓验证结果与提交信息见计划文件中的 verification record。
