## RAG 逐阶段评测与生产核查附录

### 1. 先拆开离线建库和在线回答

离线建库评测回答“知识是否被正确变成可检索投影”：

```text
SourceRevision
→ parse/normalize
→ 512/64 fixed-token chunks
→ canonical chunk spec
→ embedding + Chinese FTS projection
→ generation manifest
→ activate
```

在线回答评测回答“真实请求是否在正确时机检索并安全发布”：

```text
ChatApplication
→ RouteDecision
→ raw/standalone queries
→ dense + sparse recall
→ weighted RRF
→ rerank
→ context packing
→ EvidencePack
→ grounded generation or Agent context
→ CitationClaimGate/Coverage/Verifier
→ Publication
```

两者必须使用相同 SourceRevision、generation 和 manifest 身份。离线索引质量通过，不等于 `/chat` 的 route 会触发它；在线回答流畅，也不能证明 chunk、引用或权限正确。

### 2. 数据单位和 group-safe split

检索 case 至少包含：`case_id`、query、gold source revision、gold span/chunk、route、tenant/scope、标签来源、review 状态和 provenance。相同 source、模板变体或同一对话的相邻问题不能跨 dev/heldout，否则只是在测近重复记忆。

数据状态必须区分：

- `provisional`：规则或模型生成，只能做开发反馈；
- `reviewed`：人工检查过，但可能参与修复；
- `gold`：有明确标注规范与仲裁；
- `fresh-heldout`：未被实现和调参过程消费；
- `regression`：已知失败见证，不再是无偏准确率样本。

### 3. Chunk 评测

不要只比较 chunk 数。对 256/32、512/64、768/96 等候选记录：

- gold span 是否完整落在至少一个 chunk；
- 边界切断率和重复 token 比例；
- source metadata、字符 offset、revision/checksum 是否可回溯；
- Top-K 中为覆盖一个答案占用了多少上下文 token；
- 更新一个 source 时需要重建多少投影。

当前 512/64 是默认基线。若标题、列表、表格或条款在固定 token 边界被切坏，可比较结构感知 chunk 或 parent-child retrieval，但只有 heldout 指标证明收益才替换。

### 4. Retrieval 分层指标

Dense 与 FTS 先分别报告，再评估融合：

| 层 | 指标 | 诊断问题 |
|---|---|---|
| Candidate recall | Recall@5/10/20 | gold 是否进入候选 |
| Ranking | MRR、nDCG@K | gold 是否排得足够靠前 |
| Source | source exact / revision exact | 是否命中正确文档版本 |
| Span | overlap / containment | 是否覆盖正确依据 |
| Isolation | tenant/ACL violation | 是否跨主体泄漏 |
| Failure | no-evidence precision | 没有依据时是否克制 |
| Runtime | P50/P95、cache hit | 延迟和缓存是否可接受 |

weighted RRF 当前使用 `k=10`、vector 0.25、lexical 0.75。必须保留 dense-only、sparse-only、RRF 的同集对照；不能只公布选中的最好结果。

### 5. Query rewrite 评测

raw query 和 standalone query 都要保留。Standalone rewrite 的成功条件是消解代词与补全上下文，不是变得更长。评测至少检查：

- 关键实体、否定词、时间和用户限定是否保留；
- 是否引入原问题没有的订单号、政策名或结论；
- raw-only、standalone-only、双路融合的 Recall/MRR；
- rewrite provider 失败时 raw path 是否仍能工作；
- 输入历史是否只来自允许的 conversation range。

当前 raw/standalone 权重 0.25/0.75。对“它什么时候到”这类续问，standalone 往往增益；对错误码和明确标题，raw path 能防止改写漂移。

### 6. Rerank 合同

Reranker 接收稳定候选 ID，只能返回这些 ID 的排列和分数。验证重复、未知、遗漏、解析失败和 timeout。失败时走原 RRF 顺序或 typed degradation，不把候选正文交给模型后接受它凭空生成的引用。

报告要分清：Recall@candidate 不会被 rerank 提升；rerank 改善的是 MRR/nDCG 和最终 packing 命中。若 candidate 阶段没有 gold，升级 reranker 没有意义。

### 7. Packing 与上下文预算

默认从最多 20 个候选中选 top 5，并把知识 context 控制在约 2600 token。Packer 需要测试：

- required span 是否被截断；
- 同 source 冗余 chunk 是否挤走另一条必需证据；
- 多条件问题是否覆盖所有 fact requirement；
- citation ID 与实际进入 prompt 的片段是否一致；
- system、tool schema、Memory 和输出预留加入后是否仍低于 provider 上限。

“检索命中但回答错”常常是 packing 丢证据或 prompt 引用错位，不应全部归咎于生成模型。

### 8. Generation、Citation 与 Claim

生成层至少评分：citation validity、claim coverage、groundedness、correctness、abstention、语言和安全。确定性检查先于 LLM Judge：citation 是否存在、是否属于当前 generation、source revision/checksum 是否匹配、claim 是否引用实际 packed item，都能由代码证明。

LLM Judge 适合判断开放文本是否完整、自然或语义正确，但必须记录 judge model/prompt/version，并在人工子集上校准偏差。Judge `PASS` 不能覆盖 Authority、ACL、Coverage 或 Verifier 的 hard failure。

### 9. Route-aware RAG 测试

同一 Retriever 要在不同 route 中验证调用次数和角色：

| Route | 前置 Retriever | 最终候选 |
|---|---:|---|
| `knowledge_qa` | 1 次 | grounded answer 可以是 final candidate |
| `mixed` | 1 次 | evidence-only，Agent/Reducer 结合业务 receipt |
| `agent_task` | 0 次 | 只有声明 Knowledge Authority 的 task 可调 knowledge tool |
| `multi_domain` | 0 次 | task 级按需调用，不能全局重复 |
| `clarify/handoff/out_of_scope` | 0 次 | 策略终态或工单路径 |

测试既要断言“应该调用”，也要断言“禁止调用”。否则所有请求都预检索也可能让答案测试通过，却浪费延迟并混淆 Authority。

### 10. ServiceEpisode 与 Knowledge 隔离

两者可复用 pgvector/FTS/RRF 实现，但评测集和安全门禁必须分开：

- Knowledge 是企业公共或受 ACL 保护的 source revision；
- ServiceEpisode 是已解决用户服务经历，绑定 tenant/user/case/revision；
- 权重、retention、deletion fence、generation 与 cache namespace 独立；
- 任一跨用户 episode 命中都是 hard failure，不能被平均 Recall 抵消。

### 11. 多模态 RAG 核查

上传、OCR/VLM 感知和知识检索是三段。测试分别确认：

1. asset identity、MIME、checksum、tenant/user/turn binding；
2. OCR 的 page/bbox/text/provider/version 与 VLM observation 的 normalized bbox/confidence；
3. OCR 文本是否作为不可信 query data 进入 KnowledgeRetriever；
4. 最终 citation 是否仍指向知识 source revision，而 media observation 指向 asset artifact；
5. VLM 不得把视觉猜测升级为退款资格、账户状态或故障根因。

图片错误码可以由 OCR 产生 query，再检索维修文档；商品外观可以帮助确定候选，但型号确认仍要 SKU/业务 Owner 或人工。

### 12. 缓存与 generation 切换

cache key 应包含 tenant/scope/ACL/deletion epoch、query hash、manifest/generation、embedding/rewrite/rerank/policy 版本。需要故障注入验证：

- Redis 全失效时答案语义不变；
- active generation 切换后旧 cache 不命中；
- source retract/deletion fence 前移后旧 evidence 不复用；
- 并发 cold query 可以 single-flight，但 waiter 超时不改变权威结果；
- corrupted cache 不能绕过 Evidence validator。

### 13. PostgreSQL 生产核查

`postgresql+pgvector+pg_fts` 的 readiness 不只看能否 SELECT：

- extension、index、operator 与中文分词配置是否符合 migration；
- source revision、chunk spec、projection receipt 和 generation manifest 是否可对账；
- active generation 是否唯一；
- query plan、连接池、锁等待和 P95/P99 是否在目标内；
- rebuild 能否从 SourceRevision 恢复而不依赖旧索引；
- backup/restore 后 revision、manifest 和 chunk checksum 是否一致。

当前本地 384 维 feature hashing 是可复现基线，不代表生产语义质量。替换 embedding 时必须新建 generation，不能在旧向量字段中静默混用维度或模型。

### 14. 失败注入矩阵

| 故障 | 预期结果 |
|---|---|
| rewrite timeout | raw query 继续，记录 degraded |
| dense 不可用 | sparse 可独立工作；证据标明路径 |
| sparse 不可用 | dense 可独立工作 |
| 两路不可用 | `UNAVAILABLE`，不是 `NO_EVIDENCE` |
| rerank 非法排列 | 原顺序/typed degradation |
| manifest 冲突 | `CONFLICT`，禁止发布旧证据 |
| Evidence checksum 错 | `INVALID_CONTRACT` |
| 无 gold evidence | abstain/clarify，不用模型常识补齐 |
| Verifier timeout | `UNKNOWN`，安全降级或 handoff |
| Redis 失效 | 旁路缓存，同语义重算 |

### 15. 报告应该怎样写

每份报告固定记录 commit SHA、dataset checksum、split/provenance、review 状态、Bundle、模型矩阵、retrieval policy、generation manifest、seed、运行时间、scope limit 和失败 case。结果表同时给分母、绝对数与置信区间；小样本 pilot 明确写 pilot。

不能只写“准确率 90%”：至少区分 route exact、Recall@K、MRR/nDCG、citation validity、claim coverage、groundedness、abstention、hard safety failures、P95 latency 与 cost proxy。生产结论必须来自 fresh human-reviewed heldout，回归集只证明已知问题未复发。

### 16. 高频追问

**为什么 lexical 权重大？** 当前客服语料有大量精确规则名、错误码和业务短语，本地实验选择该基线；它不是普适结论，必须在 fresh heldout 重估。

**为什么不能只用向量库？** 精确词和版本号可能被稠密语义稀释；同时只有一种召回路径会形成单点失败。混合召回提供互补证据。

**为什么不能把 Memory 和 Knowledge 放一张表？** 权威、ACL、删除、retention 和用途不同；共享 backend 不等于共享 corpus。

**没有证据时怎样答？** 返回 NO_EVIDENCE 并澄清、拒答或 handoff；不能用模型参数知识伪造 citation。

**如何证明新 embedding 更好？** 在相同 revision、chunk、candidate budget 和 fresh group-safe heldout 上比较 Recall/MRR、延迟、成本与安全隔离，并至少重复运行；只有增益超过门槛才激活新 generation。

**线上指标下降先查哪里？** 按 route → query rewrite → candidate recall → fusion/rerank → packing → claim/citation → verifier → delivery 分层定位，不先改一个全局阈值。
