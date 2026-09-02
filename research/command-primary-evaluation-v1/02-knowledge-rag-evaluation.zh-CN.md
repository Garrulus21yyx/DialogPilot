# Knowledge RAG 迁移与评测

状态：`DRAFT_FOR_IMPLEMENTATION`
目标：在真实 PostgreSQL FTS/pgvector、版本固定的多语言 Dense 模型和生产 EvidencePack 上重新建立可复现基线；保留既有 Chunk、CrossEncoder 与 Parent 实验作为历史证据。

## 1. 在线职责

Knowledge RAG 只负责静态或版本化公共知识：

- 产品政策；
- 服务范围；
- 流程说明；
- 一般时限；
- 帮助文档和公开规则。

它不负责用户订单、退款、账户当前状态，也不负责证明截图内容真实。实时业务事实必须来自业务 Tool/Authority Owner。

目标检索链：

```text
raw query + history
  ↓
raw/standalone query candidates
  ↓
PG lexical + BGE-M3 bi-encoder dense
  ↓
fusion / requirement-aware selection
  ↓
optional second-stage rerank
  ↓
bounded Parent/Window expansion
  ↓
dedupe / packing
  ↓
KnowledgeEvidencePack
```

## 2. 为什么旧 Chunk 基线测过还要再测

不是把旧实验原样再跑一遍。旧结果继续保留为 `HISTORICAL_BASELINE`，新实验的目的则是建立 `CURRENT_PRODUCTION_BASELINE`。

必须重建的原因：

1. 当前 PostgreSQL Dense 默认仍为 384 维 hash embedding；
2. 文档侧 Dense 输入使用中文预分词后的 `lexical_document`，查询侧使用原始 query，输入合同不对称；
3. 换成真实 BGE-M3 后，chunk 长度、overlap、标题上下文对相似度分布和 Candidate Recall 的影响会改变；
4. PG FTS、pgvector、过滤、stable ID、generation 与旧实验主链并不完全相同；
5. 旧实验主要回答“某组历史数据上哪种设置更好”，不能证明中英文、code-switch 和长文档下的新生产组合仍成立。

因此执行方式是：

- 不删除旧报告；
- 不把旧 heldout 重新用于调参；
- 使用当前生产 ingest/retrieval Owner 创建新 immutable generation；
- 只重测四个有明确先验的 Chunk 候选，不扩大笛卡尔积；
- 报告 old historical 与 new production 两行，不混成一条趋势。

已有 artifacts 位于 `artifacts/eval/doc2dial-rag-*`、`artifacts/eval/wixqa-rag-*` 和 `docs/assets/eval/`，可复用 case、指标、失败 witness 与历史结果。

## 3. 评测前必须完成的生产迁移

### 3.1 Dense/lexical 输入分离

```text
原始 Chunk 文本 → BGE-M3 document embedding
原始 Query       → 同一版本 BGE-M3 query embedding
中文分词文本      → PostgreSQL FTS lexical_document
英文原始/规范化词 → PostgreSQL English analyzer
```

Dense 绝不能继续使用 `lexical_document`。Query 和 Document 必须使用同一 model family、dimension、digest 与 preprocessing contract，并按模型建议区分 query/document prefix（如果所固定模型版本需要）。

### 3.2 Immutable generation

每个 generation 固定：

- corpus/source watermark；
- chunker/version；
- embedding model/digest/dimension；
- preprocessing version；
- lexical tokenizer/analyzer；
- distance metric/index parameters；
- stable chunk/source/span IDs；
- manifest checksum。

旧 hash generation 仅作 baseline，不允许改 metadata 冒充 BGE-M3。

### 3.3 Evidence gold

组件评分必须有 document/article/page/span/claim 中至少一种与数据集能力相符的 Gold。WixQA 只有文章相关性时只报告 Article Recall；不能冒充 Chunk Span Recall。

## 4. Bi-encoder 与 CrossEncoder 的职责

### 4.1 第一阶段固定使用 bi-encoder

BGE-M3 在本方案中是第一阶段 Dense Retriever。它与 PG FTS 共同提供高召回 Candidate union：

```text
FTS lexical
   +
BGE-M3 bi-encoder dense
   ↓
Top-20/40 candidates
```

第一阶段目标是 Evidence/All-evidence Recall，而不是生成最终 Top-5。

### 4.2 CrossEncoder 只是条件触发的二阶段候选

此前英文 MiniLM 实验有诊断价值：Query decomposition 改善 Candidate Recall，但 CrossEncoder 在并列集和普通长文上都出现 harmful case，margin 也无法可靠识别坏例。因此：

- 当前英文 MiniLM 不进入默认生产配置；
- 不继续围绕 12 条合成并列数据调 margin；
- 新 PG+BGE-M3 首轮只比较 rerank off 与当前 Flash listwise baseline；
- 只有 Candidate@20 已充分，而 Candidate→Top-5/packing 仍是主要损失时，才开启一个版本固定的客服域/多语言 CrossEncoder 候选；
- CrossEncoder 需要在自然中文、英文和 code-switch heldout 上通过 harmful/non-inferiority 门禁。

建议触发条件沿用总评测计划：自然多条件 Dev 至少 100 个 task、All-evidence Candidate Recall@20 `>= .95`、candidate 与 packed gap 至少 `.05`，且该 gap 解释至少 50% 的剩余 packed loss。否则记为 `NOT_APPLICABLE_TRIGGER_NOT_MET`。

## 5. 中英文与 code-switch

不能只报混合平均值。至少分别报告：

```text
zh
en
code-switch
```

并固定以下合同：

- Dense 输入始终是原始文本，不先做中文分词；
- Lexical 路径按语言选择 tokenizer/analyzer；
- 订单号、错误码、型号、金额和日期做 exact-match slice；
- Query rewrite 必须保留原语言实体、否定、比较和作用域；
- 每个语言 slice 单独报告 Candidate、Reranked 和 Packed Recall；
- 多语言 reranker 的结论不能由英文 MiniLM 外推。

## 6. 长文档与父子 Chunk

长文档至少按原文长度、section 数或页面数分桶，例如 short/medium/long；阈值写入 manifest 后固定。报告：

- Evidence Containment；
- Boundary Fragmentation；
- Candidate Recall@K；
- All-evidence Recall@K；
- rerank/packing loss；
- 索引放大率和重复率；
- Token/P95。

父子结构只负责“命中 anchor 后补局部上下文”：

```text
Parent section
  ↓ generate
Child retrieval chunks
  ↓ retrieve/rerank
Selected child
  ↓ bounded expansion
Parent/window context
  ↓ dedupe/pack
```

不要在 Candidate 阶段展开所有 parent，否则会挤占候选多样性。

Parent/Window 仅在至少 30 个 loss witness 中，“正确 anchor 已命中但局部边界不足”占剩余 evidence miss 至少 50% 时重开。多个独立 requirement 尚未分别召回时，Parent expansion 不适用。

## 7. 调参顺序

每一步冻结上一步，禁止全参数笛卡尔积。

### 7.1 Chunk

```text
structure-aware 256/32
structure-aware 384/48
structure-aware 512/64
fixed 512/64
```

旧 `512/64` 是新生产实验的 baseline 候选，不是自动胜者。

### 7.2 Candidate/Fusion

各路一次 capture Top-40，再离线重放：

```text
Lexical/Dense = 1/0, .75/.25, .5/.5, .25/.75, 0/1
RRF k = 10, 30, 60
candidate_k = 10, 20, 40
```

主目标为 All-evidence Recall@20，MRR/nDCG、filter/provenance、P95 与 harmful slice 为门禁。

### 7.3 Query

```text
Raw/Standalone = 1/0, .5/.5, .25/.75, 0/1
```

Standalone 需要单独通过实体、数字、时间、否定、比较条件和领域范围保真测试。将“查 DP1234 退款状态”改写为“退款政策”必须判失败。

### 7.4 Rerank/Packing

```text
rerank round 1 = off, Flash listwise
final_k = 3, 5, 8
context budget = 1800, 2600
```

CrossEncoder 与 Parent/Window 只在各自触发条件成立后作为第二轮单候选实验。

## 8. 分层评测指标

### Query Transformation

- Entity/Number/Time/Negation Preservation；
- Reference Resolution；
- Scope Drift；
- Unsupported Rewrite。

### Candidate Retrieval

- Document/Article Recall@K；
- Evidence Span Recall@K；
- All-evidence Recall@K；
- MRR/nDCG；
- Candidate Coverage。

### Selection/Packing

- Candidate Recall@20；
- Reranked Recall@5；
- Packed Evidence Recall；
- Required Claim Coverage；
- harmful case count；
- context tokens/P95。

### Grounded Generation

- Claim Support；
- Citation Precision/Recall；
- Unsupported Claim Rate；
- Correct Abstention；
- Conflict Handling。

检索评测以 Evidence ID/source span 为主，不能用 LLM 参数知识猜中的答案掩盖 evidence miss。

## 9. Trigger 与 Consumption

E2E case 声明：

```text
Knowledge CapabilityDecision:
  REQUIRED | FORBIDDEN | NOT_APPLICABLE
```

示例：

| 输入 | Knowledge | 其他能力 |
|---|---|---|
| “退款通常多久到账？” | REQUIRED | 无业务工具 |
| “查 DP1234 退款到哪了” | FORBIDDEN | Business Tool REQUIRED |
| “为什么我的退款超过政策时限？” | REQUIRED | Business Tool REQUIRED |
| “谢谢” | FORBIDDEN | 无 |

报告 Knowledge Invocation Precision/Recall、Forbidden Invocation Count，以及召回正确后下游是否真正消费。

## 10. 数据与运行角色

- Doc2Dial Dev：官方 span，用于 Chunk containment/fragmentation；
- MTRAG conversation Dev/heldout：连续 query 与 standalone；
- WixQA：外部文章级检索；
- 自然多条件客服 Dev：联合覆盖与 reranker 触发；
- 80 条合成合同：Knowledge 调用与业务权威边界；
- 历史 `artifacts/eval/`：baseline/regression，不能重新称为 fresh test。

确定性 Chunk、fusion 与 capture/replay 默认一次；随机 LLM rewrite/rerank/generation 才在关键切片重复。

## 11. Runner 与产物

组件入口：`run_postgres_rag_eval.py`。

真实路径必须是：

```text
SourceRevision ingest
→ PG FTS/pgvector
→ fusion/rerank/packing
→ KnowledgeEvidencePack
```

输出 manifest、query/candidate capture、case results、evidence packs、CapabilityTrace 和 report。Evaluator adapter 不得临时建立另一套内存检索链拿分。

## 12. 通过条件

- 输入对称与真实 BGE-M3 generation 合同通过；
- Dev 上按预声明顺序选择并冻结；
- heldout All-evidence/Packed Recall 对 baseline 非劣或改善；
- zh/en/code-switch 与长文档 slice 无不可接受退化；
- Citation/unsupported claim 门禁通过；
- Trigger/Forbidden Invocation 通过；
- 新配置只有在 paired E2E task success 非劣后进入生产 Bundle。

## 13. 相关文档

- [M0–M4 总迁移计划](./00-m0-m4-migration-master-plan.zh-CN.md)
- [E2E 与成绩汇总](./05-e2e-evaluation-and-scorecard.zh-CN.md)
