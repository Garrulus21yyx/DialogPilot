# Knowledge RAG 迁移与评测

状态：`DEV_CHUNK_FUSION_QUERY_PACKING_SELECTED — HELDOUT_NOT_RUN`
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

历史基线仍需在当前实现上重建的原因：

1. 历史报告产生时，PostgreSQL Dense 仍是 384 维 hash baseline；
2. 历史报告产生时，文档 Dense 与查询输入合同不对称；该实现缺口现已修复，但旧分数不会因此自动成为新分数；
3. 真实 BGE-M3 下，chunk 长度、overlap、标题上下文对相似度分布和 Candidate Recall 的影响会改变；
4. 当前 PG FTS、pgvector、过滤、stable ID、generation 与旧实验主链并不完全相同；
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

截至 2026-09-03，代码已完成 document/query provider 对称、完整 profile 持久化、本地权重 SHA-256 校验和显式 generation rebuild。开发 PostgreSQL 已激活一个包含 6 个默认文档 chunk 的 1024 维 BGE-M3 generation；中文退款查询和英文配送查询均在真实 pgvector/FTS candidate 路径得到正确 Top-1。它是链路冒烟证据，不是 heldout 质量分数。正式评测仍需用冻结语料重新建 generation，并在 manifest 中记录 provider/profile/generation。

容器评测使用 `production-semantic` target，并通过 `docker-compose.semantic.yml` 只读挂载固定模型目录；默认 `production` 镜像仍不安装本地 ML 依赖。两条路径不能混用，也不能在模型加载失败时回退 hash。

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

截至 2026-09-03，这一触发条件尚未满足：当前 Query cohort 只有 48 条，
不是至少 100 条的自然多条件集合，而且所选配置的 All-evidence Recall@20
为 `.8333`。计划使用的 `BAAI/bge-reranker-v2-m3` 本地目录也只有一个
revision 引用，没有 config、tokenizer 或权重，无法离线校验身份和运行。
因此本阶段不接 CrossEncoder；完整缓存的英文 MiniLM 继续仅作历史诊断。

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

当前仓库数据边界已经核清：现有公开 RAG Gold 只能诚实支持英文；中文和
code-switch 只有项目合成/临时 smoke，适合测 Trigger、Evidence consumption
和业务权威边界，不能报告自然分布检索质量。MTRAG 尚未落盘。若最终需要
自然中文或 code-switch 成绩，必须另取并冻结对应公开数据，不能用旧 intent
或短脚本文档代替。

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

现有 300 条 Doc2Dial Dev 可零模型调用地按 Gold 文档长度聚合：

| 长度桶 | cases | All-evidence@20 | Evidence R@20 | MRR@20 |
|---|---:|---:|---:|---:|
| short `<4000` chars | 92 | `65/92=.7065` | `.7065` | `.4436` |
| medium `4000–7999` | 137 | `94/137=.6861` | `.6886` | `.4466` |
| long `>=8000` | 71 | `57/71=.8028` | `.8028` | `.4380` |

这是 consumed Dev 的 candidate 诊断；它没有显示 long slice 特别退化，因此
不为“再看一次长文”重跑旧父子实验。正式英文 heldout 可从本地官方
Doc2Dial test archive 中排除已消费 conversation 后机械冻结，无需人工 Gold。

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

E2E case 声明预期调用：

```text
Knowledge invocation:
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

当前 Chunk 入口是 `scripts/run_postgres_rag_eval.py`。它只负责第一段可归因实验：

```text
SourceDocument ingest
→ production chunker
→ PG FTS + pgvector
→ weighted RRF candidate@20
```

它强制使用独立 `EVAL_DATABASE_URL`、显式 pinned BGE-M3，并一次只接受一个预声明 Chunk profile。产物固定为 `manifest.json`、`predictions.jsonl` 和 `report.json`；manifest 将 standalone rewrite、rerank、parent expansion、packing、generation 和 judge 明确标记为 `not_run`。后续阶段在本轮候选配置冻结后，分别增加 selection/packing 和 grounded-generation runner，不能把未执行阶段写成已测。

2026-09-03 的第一条真实 smoke 使用 Doc2Dial heldout 40 篇文档和 48 条 raw query。`structure-aware 256/32` 产生 292 chunks；48/48 检索状态为 OK，system failure 为 0。candidate@20 的 Evidence Recall 为 `.4896`、Document Recall `.6042`、All-evidence Recall `23/48=.4792`、MRR `.2718`、nDCG `.3249`、检索 P95 `140.64ms`。这只证明第一组真实 candidate 链可运行，不是四组 Chunk 选择结论。

随后用同一 heldout checksum、BGE-M3 profile 和 candidate policy 完成了四组可比诊断：

| Chunk profile | chunks | Evidence R@20 | Document R@20 | All-evidence@20 | MRR | nDCG | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| structure 256/32 | 292 | `.4896` | `.6875` | `23/48` | `.2729` | `.3259` | `143.08ms` |
| structure 384/48 | 198 | `.5521` | `.7500` | `26/48` | `.3261` | `.3779` | `143.49ms` |
| structure 512/64 | 153 | `.5000` | `.6875` | `24/48` | `.3499` | `.3843` | `138.68ms` |
| fixed 512/64 | 153 | `.5417` | `.7083` | `26/48` | `.3508` | `.3945` | `139.26ms` |

这四组统一标记为 `DIAGNOSTIC_ONLY`：structure 384/48 的 Recall 更高，
fixed 512/64 的 MRR/nDCG 更高，且数据已被查看。因此不从这些数字
选 winner。正式选择必须在 Doc2Dial Dev 按预声明的 All-evidence Recall@20
→ Evidence Recall@20 字典序完成，然后才能冻结 Chunk 并进入融合调参。

2026-09-03 的 designated Dev/current-pipeline selection 已按这一规则运行。
四组共享 corpus SHA `89a8828b…281`、cases SHA `f0e282d4…363`、BGE profile
`9cae2189…824` 和 candidate policy `945b2231…10a`，每组均为 300/300
predictions、system failure 0：

| Chunk profile | chunks | All-evidence@20 | Evidence R@20 | Document R@20 | MRR | nDCG | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| structure 256/32 | 589 | `131/300=.4367` | `.4456` | `.6067` | `.2717` | `.3144` | `159.62ms` |
| structure 384/48 | 399 | `136/300=.4533` | `.4567` | `.6067` | `.2738` | `.3178` | `147.56ms` |
| structure 512/64 | 314 | `138/300=.4600` | `.4633` | `.5667` | `.2993` | `.3399` | `168.76ms` |
| fixed 512/64 | 314 | **`140/300=.4667`** | **`.4700`** | `.5767` | **`.3064`** | **`.3470`** | `564.18ms` |

按预声明字典序，`fixed 512/64` 在第一个指标已胜出，因此成为
下一阶段融合调参的唯一 Chunk 候选。这不是线上默认切换；还需要融合、
query、selection/packing 与 frozen heldout/E2E 门禁。该组 P95 的单次异常不改变
已声明的质量选择顺序，但在 SLO 结论前必须独立复测。

固定该 Chunk 后，`scripts/run_postgres_rag_fusion_eval.py` 对同一 300 条 Dev
query 各执行一次 raw lexical/dense Top-40 捕获，先将未融合排名持久化，再从
该 artifact 离线重放 5 组权重 × 3 个 RRF k。300 条均为 `OK`，系统失败为
`0`，capture P95 为 `147.73ms`。按同一预声明质量顺序选出：

```text
Lexical/Dense = 0/1
All-evidence Recall@20 = 216/300 = .7200
Evidence Recall@20 = .7211
MRR@20 = .4437
nDCG@20 = .5078
```

Dense-only 下 `k=10/30/60` 完全同分；报告中的 `k=10` 只是稳定
`config_id` tie-break，不能解释成 k=10 优于其他值。本次只冻结下一阶段的
`fixed-512-64 + source_k=40 + candidate_k=20 + dense-only` Dev 候选；没有运行
standalone rewrite、rerank、Parent expansion、packing、generation 或 judge，
也没有改变生产默认配置。

随后 `scripts/run_postgres_rag_query_eval.py` 复用了已经生成并校验身份的
48 条 Dev standalone artifact，在新的 `fixed-512-64 + BGE-M3 + PG HNSW`
generation 上分别捕获 Raw/Standalone Dense Top-40，再从落盘的
`predictions.jsonl` 离线重放四组权重。本轮没有再次调用 rewrite 模型；
25 条为有效改写，23 条与 raw 相同并按生产规则退回 raw。48/48 检索为
`OK`，系统失败为 0：

| Raw/Standalone | All-evidence@20 | Evidence R@20 | MRR@20 | nDCG@20 |
|---|---:|---:|---:|---:|
| 1/0 | `36/48=.7500` | `.7569` | `.3729` | `.4626` |
| .5/.5 | `40/48=.8333` | `.8333` | `.4255` | `.5192` |
| .25/.75 | `40/48=.8333` | `.8333` | `.4668` | `.5516` |
| 0/1 | **`40/48=.8333`** | **`.8333`** | **`.4706`** | **`.5545`** |

按预声明字典序选择 `0/1`。这表示“有有效 standalone 时使用 standalone；
改写失败、为空或与原句相同时使用 raw”，不是无条件删除 raw。三组配置在
两个 Recall 主指标上打平，`0/1` 只凭 MRR/nDCG 胜出。该 48 条 cohort 已被
查看、不同于 fusion 的 300 条 Dev，也不是 heldout；它只冻结下一阶段的
Query 候选。capture P95 为 `227.48ms`，artifact SHA-256 为
`aba106ad…d9f04`。线上与离线现共用同一个 RRF 排序 Owner，生产默认参数
仍未切换。

`scripts/run_rag_selection_eval.py` 随后从上述 Query 三件 artifact 恢复
candidate 正文并校验 source checksum/revision/span，使用同一
`fuse_rankings()`、真实 `ContextPacker` 和 `EvidencePack` 重放
`K=3/5/8 × budget=1800/2600`。本轮没有 PostgreSQL、Embedding、rewrite、
rerank、Parent、generation 或 judge 调用。48 条 viewed Dev 结果为：

| K / budget | Pre-pack All-evidence | Packed All-evidence | mean / P95 tokens |
|---|---:|---:|---:|
| 3 / 1800 | `25/48=.5208` | `.5208` | `1385 / 1536` |
| 3 / 2600 | `25/48=.5208` | `.5208` | `1385 / 1536` |
| 5 / 1800 | `31/48=.6458` | `30/48=.6250` | `1667 / 1796` |
| 5 / 2600 | `31/48=.6458` | **`31/48=.6458`** | `2281 / 2560` |
| 8 / 1800 | `36/48=.7500` | `30/48=.6250` | `1692 / 1796` |
| 8 / 2600 | `36/48=.7500` | `31/48=.6458` | `2505 / 2588` |

按 Packed All-evidence → Packed Evidence → mean tokens → config ID 选择
`Top-5 / 2600`。其 packing harmful 为 0、packing loss 为 0；相对
Candidate@20 的 `.8333`，Top-5 选择本身损失 `18.75pp`。Top-8 虽在打包前
多找回 5 条，但 2600 token 预算将收益全部抹掉。因此当前下一瓶颈是
Candidate→Top-5 选择，不是 Parent，也不是 2600-budget packing。该结果仍是
`VIEWED_DEV_SELECTION_PACKING`，`promotion_allowed=false`。

下一步是在冻结的、conversation-isolated 英文 heldout 上复测完整候选与
packing baseline；中文/code-switch 先完成项目 Trigger/Consumption 合同。
只有前述 reranker 或 Parent 触发条件成立，才分别开启对应实验。

实际调用必须从 retriever result、Stage 与 E2E audit 读取；Evaluator adapter 不得临时建立另一套内存检索链拿分。

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
