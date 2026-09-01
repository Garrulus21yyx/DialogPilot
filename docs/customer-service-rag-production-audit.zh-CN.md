---
layout: page
title: 客服 RAG 生产化审计
permalink: /customer-service-rag-production-audit/
---

# DialogPilot 客服 RAG 生产化审计

## 1. 结论先行

这次审计不再把“检索 Recall 提升”写成“生产级 RAG 已完成”。客服 RAG 的闭合对象是一次用户可见回答，完整因果链是：

```text
公共政策来源
→ 最小解析合同
→ Chunk + 来源坐标
→ Dense/Sparse 派生索引 + Manifest
→ Raw/Standalone + public scope
→ 候选融合 + 严格重排
→ Evidence Pack + 预算装配
→ claims/conflicts/abstained
→ 纯知识直接发布；混合业务事实由工具补充后校验
→ 分层评测、Shadow、回滚
```

当前仓库已经闭合“小型、公共、txt/md/json 客服知识库”的代码合同；仍没有完成真实流量 Shadow、人工 Judge 校准、多副本共享 Sparse、政策更新/删除事务和企业多租户隔离。因此准确说法是：**生产边界已显式化并由回归测试覆盖，但外部生产验证仍未完成。**

## 2. 客服范围：必要、暂缓与非目标

### 本项目必须具备

- 公共政策与用户私有事实分离。退款规则、配送说明、故障排查进入 RAG；“我的订单现在到哪”“退款是否到账”只能由认证业务工具查询。
- 错误码、订单格式、期限、金额、否定条件既要语义召回，也要精确词召回。
- 每条证据能回到稳定来源、checksum 和原文 `[start,end)`；查询改写不能成为证据。
- 资料不足或政策冲突时有类型地拒答，不能让模型自行挑一个版本。
- 最终用户可见的纯知识答案必须就是通过 citation 合同的 GroundedAnswer，不能让后续 Agent 无约束改写。

### 暂缓

- PDF、HTML、DOCX、表格与 OCR 的结构化 ParseResult；当前导入格式只有 txt/md/JSON。
- 语义压缩、跨 chunk 自动合并、法规时态推理。
- 多副本共享 Sparse 服务与零停机增量重建；当前部署合同仍是单应用写者。

### 明确非目标

- 文档级复杂 ACL。当前 collection 只存统一公开客服政策：写入需 `admin`，查询需 `knowledge:read` 或聊天能力，所有 chunk 固定 `scope=public`。
- 订单、账户、退款进度等用户私有事实进入公共 RAG。

`allowed_public` 只是一次固定 scope decision 的审计记录，不冒充 tenant ABAC。

## 3. Owner 与闭合合同

| 阶段 | 权威 Owner | 当前合同 | 状态与剩余边界 |
|---|---|---|---|
| Source | `SourceDocument` | `source_id/title/content/source_type/checksum`；缺省 ID 内容寻址；checksum 必须匹配完整 UTF-8 原文 | 已实现。还没有逻辑文档的 update/delete/effective-time 状态机 |
| Parse | HTTP 上传边界 | 只接受 UTF-8 txt/md/JSON；非法编码、扩展名、JSON shape 类型化拒绝 | 当前范围已闭合；PDF/HTML/OCR 暂缓，不引入空壳 ParseResult |
| Chunk | `DocumentChunker` | fixed 512/64、原文 `[start,end)`、chunk v4 | 已有 Dev 依据；仍是估算 Token，不是真实生成 tokenizer |
| Scope | public collection contract | 每个 chunk 固定 `scope=public`；Dense 和 Sparse 召回前都过滤 | 当前范围已闭合；多租户/内部资料不得混入此索引 |
| Dense | Chroma collection | 显式 `ONNXMiniLM_L6_V2 / all-MiniLM-L6-v2 / 384d`；模型名进入合同 | 配置已固定；尚未保存模型权重文件 digest，也未做新中文客服集比较 |
| Sparse | `PersistentBM25Index` | SQLite posting list、DF、文档长度、中文单字+二元词、英文/编号；Chroma chunk 是权威，Sparse 可重建 | 已消除每次查询 `collection.get()` 全库扫描。启动/导入重建仍是单写者合同 |
| Manifest | `KnowledgeBase.index_manifest` | source/parser/chunker/dense/sparse/corpus fingerprint 与构建时间；非空旧索引不兼容即 fail closed | 已实现；Embedding 权重 digest、跨服务签名 Manifest 未实现 |
| Query | `QueryTransformer` | Raw 必保留；有历史才生成 Standalone；失败退回 Raw；Prompt version 进入 trace | 已实现；`/search` 无历史时 Raw-only 是有意合同，不伪称发生改写 |
| Fusion | `KnowledgeBase` | Raw/Standalone × BM25/Dense 在同一 stable chunk-ID weighted RRF 空间融合 | 已实现；权重只由冻结 Bundle 解释，不能在线随意调 |
| Rerank | `ResultReranker` | 模型必须返回候选集合的完整、唯一、精确排列；任一未知/重复/遗漏使整次重排回退 | 已收紧，不再静默过滤未知 ID 后补齐 |
| Packing | `ContextPacker` | Top-5/2600、source overlap 去重、预算超限记录 drop reason | 已实现；这是证据选择，不冒充语义压缩 |
| Evidence | `EvidencePack` | chunk/source/checksum/span/score/rank/source-ranks/scope decision/Manifest/query/rerank/packing trace | 已实现；公开 API 不返回全文，只返回公共来源坐标与排名 |
| Generation | `GroundedAnswerGenerator` v4 | `answer + claims[].citations + conflicts[].citations + abstained + reason`；引用只允许 Evidence Pack IDs | 已实现；claim entailment 的离线 Judge 仍需人工校准 |
| Publication | API + `AnswerVerifier` | 纯公共知识且无业务工具时，最终候选就是 GroundedAnswer（包括有类型的冲突/证据不足拒答）；混合政策与实时事实时由 Agent 合成并带 Evidence 进入 Verifier | 关闭了纯知识二次漂移；混合回答仍依赖模型 claim 判断，不能声称确定性逐 claim 证明 |

## 4. Sparse 为什么是派生投影

旧实现每次查询都读取全部 Chroma chunk 并重新 tokenize。现在导入或启动时一次性构建：

```text
term → (chunk_id, term_frequency)
chunk_id → token_count
term → document_frequency
```

查询只读取 query term 对应的 posting 并计算 BM25，再按命中 ID 从 Chroma hydration。Chroma 中的 public chunk 仍是唯一语料事实；Sparse 保存同一 corpus fingerprint，损坏或漂移时从 Chroma 重建。导入若已写 Chroma 但 Sparse 同步失败，KnowledgeBase 会把 Sparse 标记为 not-ready，所有需要 lexical 的查询 fail closed 并进入现有降级，绝不混合“新 Dense + 旧 Sparse”。这样避免引入两份权威语料，同时让在线复杂度从“扫描全部 chunk”降为“读取命中 posting + Top-K hydration”。

当前 SQLite sidecar 适合该仓库的单应用写者。若变成多个 API 副本并行写，必须迁移到共享搜索服务或使用不可变 generation + 原子 active pointer，不能让每个副本维护不同本地 Sparse。

## 5. Evidence Pack 与最终回答

一次被装入生成上下文的证据至少保存：

```json
{
  "chunk_id": "refund-policy::chunk-3",
  "source_ref": {
    "source_id": "refund-policy",
    "start_char": 1024,
    "end_char": 1870,
    "source_type": "markdown",
    "checksum": "sha256...",
    "scope": "public"
  },
  "score": 0.081,
  "rank": 1,
  "source_ranks": {"standalone:bm25": 1, "raw:vector": 4},
  "scope_decision": "allowed_public"
}
```

Pack 还记录 index manifest fingerprint、Raw/Standalone variants、rewrite/rerank Prompt version、模型合同错误以及 packing drop。这样一次错误可以判断发生在解析、召回、重排、预算还是生成，而不是只看到最终“答错”。

生成 v4 不只返回 citation 列表。非拒答必须有 `claims`，每个 claim 文本必须原样出现在 answer，所有 claim citation 的并集必须与顶层 citations 完全一致。`conflicts` 至少引用两个输入 chunk，出现冲突时只能 `abstained=true, reason=conflicting_evidence`。

## 6. 小规模客服数据怎么选

保留两套互补公开数据，而不是寻找一个万能数据集：

1. [Doc2Dial](https://doc2dial.github.io/)：有长对话、用户/客服 turn 和官方 grounding span，适合 Chunk containment、Raw/Standalone、多轮指代和 stage attribution。它偏政府服务，不代表电商客服分布。
2. [WixQA](https://huggingface.co/datasets/Wix/WixQA)：公开 MIT 客服 KB 快照，包含 200 条真实用户问题+专家多步回答、200 条专家校验模拟问答、6,221 条单文档合成问答以及对应 6,221 篇 Help Center 文档。它更适合公共客服政策、multi-article completeness 和最终回答质量。

下一轮不需要把 6,221 篇全部塞入模型评测。建议冻结一个约 100 case 的 mini suite：

| Slice | 规模 | 目的 |
|---|---:|---|
| Doc2Dial 既有 Dev | 300 retrieval / 48 model | 继续固定 chunk、query、fusion、rerank 参数 |
| WixQA ExpertWritten | 20 | 真实问题、多步骤、多文档完整性 |
| WixQA Simulated | 20 | 客服表达、简洁回答、对话蒸馏分布 |
| WixQA Synthetic | 20 | 单文档精确召回与 KB 覆盖 |
| 项目客服对抗集 | 40 | 中文退款/配送/登录、错误码、否定、缺资料、冲突、私有订单必须走工具、Prompt injection |

WixQA 按 `article_ids` 分组切 Dev/Heldout，不能让同一文章的改写同时出现在两边。公开集只能校准通用客服能力；上线前还必须用脱敏真实搜索日志按意图占比分层抽样，并由客服专家标注。

仓库提供可直接下载并构建现有 `RagDataset` 合同的命令：

```bash
PYTHONPATH=. .venv/bin/python scripts/build_wixqa_rag_subset.py \
  --split dev --cases-per-config 20 --max-documents 120 \
  --output artifacts/eval/wixqa-rag-mini-dev-v1

PYTHONPATH=. .venv/bin/python scripts/build_wixqa_rag_subset.py \
  --split heldout --cases-per-config 20 --max-documents 120 \
  --output artifacts/eval/wixqa-rag-mini-heldout-v1
```

Builder 会记录官方 URL、MIT license、2024-12-02 KB snapshot、选择规则和 checksum。WixQA 只提供 article IDs，因此适合 document-level Recall 与 answer completeness；它不能替代 Doc2Dial 的精确字符 span 来评价 chunk containment。

## 7. 分层指标与选择依据

不使用一个人为加权总分。先过不可补偿硬门禁，再在可行集里按质量、延迟、成本做 Pareto 选择。

| 层 | 指标 | 硬门禁示例 |
|---|---|---|
| Parse/Source | checksum、稳定 ID、非法编码、重复导入结果 | 坐标/checksum 错误为 0；未知格式 fail closed |
| Chunk | containment、fragmentation、index amplification | 官方 evidence containment 不退化；无 source gap |
| Sparse/Dense | Recall@20、MRR、nDCG、exact-code slice、P95 | 错误码/编号 slice 不低于 lexical baseline；查询不得 full-corpus get |
| Query | entity/negation retention、hallucinated entity、harmful rate | Raw 必保留；实体/否定门槛先于 Recall |
| Rerank | Recall/MRR@5、exact permutation failure、harmful | 未知/遗漏/重复 ID 100% typed fallback |
| Packing | evidence retention、Token、drop attribution | 入选 evidence 不丢 provenance；预算绝不超限 |
| Generation | claim support/completeness、citation validity、conflict/abstention | 非法引用与格式失败为 0；冲突不得给确定结论 |
| Route/Tool | public KB vs private tool exact route | 私有订单/账户事实进入公共 RAG 为 0 |
| E2E | task success、人工正确性、P95、cost、escalation precision | 安全/隐私/错误操作为 0；Judge 先与人工校准 |

当前 `.75/.25`、`.25/.75` 等权重来自既有 Doc2Dial Dev 的候选比较、实体/否定硬门禁和 dialogue-group paired bootstrap，不是主观赋分。换到 WixQA/中文客服集后，权重只能作为冻结 baseline；若 Heldout 退化，不允许在 Heldout 上反向调参。

## 8. 发布门禁

代码合并、评测通过和生产验证是三个状态：

```text
IMPLEMENTED
→ REGRESSION_PASS
→ FRESH_HELDOUT_PASS
→ HUMAN_CALIBRATED
→ SHADOW_PASS
→ CANARY 5% → 25% → ACTIVE
```

本次能证明前两项；现有小型 Doc2Dial test 只能作为已消费冻结反例，不能替代新的 WixQA group-safe Heldout、人工 blind review 和真实流量 Shadow。Shadow 至少监控：每层 P50/P95、Sparse/Dense 候选数、fallback、abstention、conflict、citation invalid、私有事实误路由、人工转接率和用户负反馈。

## 9. 与当前主流实践的关系

- [Chroma 官方文档](https://docs.trychroma.com/docs/querying-collections/metadata-filtering)明确支持在 `get/query` 前用 `where` 做 metadata filter；当前 public scope 在 Dense 与 hydration 两侧都执行。
- [Chroma embedding 配置](https://docs.trychroma.com/docs/collections/configure)说明 embedding function 会影响索引构建并应随 collection 配置持久化；本项目因此不再把“服务端默认”当成未记录事实。
- [RAGChecker（NeurIPS 2024）](https://proceedings.neurips.cc/paper_files/paper/2024/file/27245589131d17368cccdfa990cbf16e-Paper-Datasets_and_Benchmarks_Track.pdf)用 claim-level entailment区分 retrieval 与 generation 错误；本项目采用同样的分层诊断思想，但没有把模型 Judge 包装成确定性证明。
- [τ²-bench](https://github.com/sierra-research/tau2-bench)强调客服 Agent 的多轮用户—工具交互与任务状态；因此本项目把公共知识回答和订单/退款业务工具的正确路由列为独立 E2E 门禁。

这些资料用于校准工程合同，不表示 DialogPilot 已复现其完整框架或达到其榜单结果。
