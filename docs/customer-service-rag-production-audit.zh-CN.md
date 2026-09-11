---
layout: page
title: 客服 RAG 生产化审计
permalink: /customer-service-rag-production-audit/
---

# 客服 RAG 生产化审计：从来源事实到可发布回答

> 审计对象是当前分支的单一 PostgreSQL RAG 主链。本文中的“生产化”指生产责任与合同审计，不代表已有生产流量、SLA、Shadow/Canary、人工签署平台或生产发布结论。

## 1. 结论先行

当前链路已关闭“检索到文本就算 RAG 成功”的主要责任缺口：

```text
SourceRevision
→ active generation
→ structure-aware chunk
→ pgvector + PostgreSQL Chinese FTS
→ weighted RRF
→ optional rerank / ContextPacker
→ EvidencePack
→ FactRequirement / Coverage
→ grounded answer / Verifier
→ PostgreSQL publication
```

可本地复现的能力包括唯一 SourceRevision 生命周期、稳定 chunk identity、混合检索、Evidence provenance、Coverage 和 fail-closed 发布。尚不能据此宣称生产就绪：冻结公开测试、80 条合成合同真实 E2E、生产数据治理、真实负载容量、Collector/告警、生产 RPO/RTO 和组织级发布治理仍未闭合。

## 2. 审计范围与非目标

本轮审计覆盖：

- 文本 Knowledge 的 ingest、索引、检索、packing、生成与发布；
- 公共知识与用户私有业务事实的 authority 隔离；
- 数据身份、缓存、索引 generation 和 provenance；
- 失败语义、可观测性、评测与本地恢复证据；
- 多模态观察如何进入 Evidence，而不越权成为业务事实。

不把以下项目写成已实现：

- 生产流量或真实客服 SLA；
- 线上 Shadow/Canary/promotion/rollback；
- 生产级内容审核组织、双盲签署或值班体系；
- 任意格式复杂文档的完整解析；
- Knowledge Graph 或 GraphRAG；
- 模型生成内容成为权威订单/退款/账户事实。

## 3. Owner 与正向合同

| 关注点 | Owner | 正向合同 |
|---|---|---|
| 原文版本 | PostgreSQL SourceRevision | checksum、scope、状态和 revision 可追溯 |
| 可服务索引 | active generation | 同一语料只有一个在线 generation；不完整索引失败关闭 |
| Chunk | chunker + stable identity | 当前 PG baseline 为 structure-aware 512/64；字符范围回指 revision |
| Dense / Sparse | pgvector / PostgreSQL FTS | 都是同一原文的派生投影，可重建 |
| Query variants | query policy | Raw 与 Standalone 带固定权重和版本 |
| Fusion | weighted RRF | rank 输入、权重和 `k` 可复现 |
| Rerank | structured permutation adapter | 短别名输出后映射回 stable chunk id；非法排列 typed failure |
| Context budget | ContextPacker | 在最终拼接文本上计费，保留 provenance |
| Evidence | EvidenceReceipt / EvidencePack | source revision、checksum、span、rank、manifest 不丢失 |
| Requirement | AuthorityPolicy / CoverageGate | 合法 authority 才能满足对应事实需求 |
| Answer | grounded generator + Verifier | 证据不足拒答；UNKNOWN 失败关闭 |
| Publication | PostgreSQL publication | 先持久化唯一回答，再返回/投影 |

## 4. 为什么 PostgreSQL 是唯一 Knowledge Owner

当前分支已经删除 Chroma Knowledge 与旧 sparse index。选择 PostgreSQL + pgvector + 中文 FTS 的目标不是声称它在所有规模下最快，而是关闭本项目中的双重事实：

- SourceRevision、chunk 和 active generation 在一个事务平台上有唯一身份；
- Dense 与 sparse 都是可重建投影，不再各自携带一套模糊元数据；
- publication、conversation 和 evidence 引用同一稳定 revision；
- 空库安装可由 Alembic 完整建立，不依赖隐藏的本地索引目录。

若未来容量或延迟要求需要外部检索服务，迁移边界应是 `HybridRetrievalBackend`，而不是让调用者同时查询两个 Owner。

## 5. 当前检索配置与实验解释

当前本地默认：

| 参数 | 值 | 口径 |
|---|---:|---|
| Chunk | 512 tokens | structure-aware |
| Overlap | 64 tokens | 相邻 chunk |
| Dense weight | 0.25 | weighted RRF 输入 |
| Lexical weight | 0.75 | PostgreSQL Chinese FTS |
| RRF k | 10 | fusion 常量 |
| Candidate | 20 | rerank/selection 前 |
| Packed | 5 | 回答上下文 |
| Context budget | 2600 estimated tokens | packing 上限 |
| Local embedding | 384 dimensions | deterministic feature hashing；仅为本地占位 |

这些参数是历史 Doc2Dial Dev 选择与当前本地可复现目标的组合，不是当前 PostgreSQL+BGE-M3 的联合最优。历史实验选择的是 fixed `512/64`，当前 PG ingest 实际使用 structure-aware `512/64`；二者必须分别标注。当前文档侧 hash embedding 输入是预分词 `lexical_document`，查询侧输入是原始 query，切真实 BGE-M3 前必须在 ingest Owner 修成“原始 Chunk → dense、预分词文本 → FTS”，并由 provider 正确发布 model/dimension/digest/preprocessing metadata。

`dialogpilot-500-v1` 的旧 25 文档 fixture 曾选择 BM25-only，只是历史 development baseline，不能覆盖 Doc2Dial 更完整链路的选型，更不能把两个数据集的数字拼成一个总分。

## 6. Evidence 与 authority 隔离

Evidence 需要同时回答：内容是什么、来自哪里、哪个版本产生、由谁观察、可支持哪类 claim。

Knowledge Evidence 可支持政策、说明、公开流程；业务工具 receipt 可支持订单、退款和账户的实时状态；Memory Evidence 只支持带来源的历史事实；Media Observation 只描述 OCR 或视觉观察。

以下转换必须拒绝：

- 用公共退款政策回答“我的退款已经到账”；
- 用截图里的文字断言订单后台已处理；
- 用相似历史 ServiceEpisode 覆盖本次工具查询结果；
- 用 LLM rerank 或生成文本反向修改 SourceRevision；
- citation 指向 chunk id，却无法回到 revision/checksum/span。

CoverageGate 根据 FactRequirement 检查 authority，而不是只检查 `evidence_count > 0`。

## 7. Query、Rerank 与 Packing 的边界

Raw query 保留原始措辞；Standalone query 消解对话指代。两者进入同一检索内核并以固定权重融合，不能让改写结果覆盖用户原话。

Rerank 只改变候选排列。当前局部 PydanticAI ToolOutput 约束返回 permutation，使用短别名降低模型输出负担，再映射回稳定 chunk id。缺项、重复项、越界别名或模型故障都是 typed failure。

Packing 的权威是最终拼接文本，不是各 section 的估算和。HTML 转义、分隔符、历史和当前问题都必须计费；当前轮次本身无法装入时显式拒绝。

## 8. Generation、Citation 与发布

grounded generator 的输出代数包含：

- `answered`：在证据内回答；
- `insufficient_evidence`：证据不足；
- `conflicting_evidence`：证据冲突。

结构化输出成功不等于答案正确。Claim 必须关联 Evidence，Coverage 必须完整，Verifier 必须 `PASS`。解析错误、校验器故障或来源无法验证都进入 `UNKNOWN`，不能用默认分数或模板回答伪装成功。

回答发布后仍需区分 publication 与 delivery。数据库成功写入回答，不等于连接器已送达或用户已读。

## 9. 缓存、索引和重建

缓存 key 至少绑定 corpus、query、retrieval config、generation 和 scope。旧 generation 的命中不能服务新 SourceRevision。

当前单路径要求：

1. ingest 产生新 SourceRevision；
2. projection 构建 dense/sparse chunk 事实；
3. generation 只有在完整性检查通过后成为 ACTIVE；
4. reader 只读取唯一 ACTIVE generation；
5. 非空索引缺少 source/chunk/vector/lexical/scope 任一合同就失败关闭；
6. 重建从权威 SourceRevision 开始，不从缓存或回答反推原文。

## 10. 多模态与复杂文档

在线附件由 `/assets/upload` 持久化并完成安全扫描。Agent 决定 L0/L1/L2；Tesseract 和 DeepSeek Vision 产生带 asset checksum、page/bbox、producer/model/version 的派生观察。

这已覆盖截图 OCR 和显式启用的视觉理解本地链路，但不等于复杂 PDF 摄取已完成。表格、版面、扫描 PDF、父子结构和跨页证据仍应通过独立 ingest pipeline 与评测门禁进入 SourceRevision，不能把在线 VLM 观察当作稳定知识库原文。

## 11. 分层评测证据

| 层 | 当前证据 | 能证明 | 不能证明 |
|---|---|---|---|
| 500 条 fixture | 100 Retrieval + 其他三层 | 数据合同和历史回归 | 当前 PostgreSQL 全链的生产质量 |
| Doc2Dial mini | 100 文档、300 case、488 span | Chunk/Query/Rerank/Packing/Generation 的 Dev 比较 | 中文真实业务分布或生产泛化 |
| Service-chain v2 | 分层 rubric + Owner probes | 真实应用边界、工具/发布/Handoff 合同 | 未运行场景的结果 |
| Local E2E | PostgreSQL engine、JWT、回答、OCR/VLM | 当前环境的一次可复现链路 | 长期稳定性和容量 |
| Restore report | 空库到 Alembic head、dump/restore 对账 | 本地 schema 可重建 | 生产 RPO/RTO |

RAG 实验显示 Query rewrite 在部分集合提升 Candidate recall，但父子拓扑和低成本 cross-encoder 没有通过跨切片非劣门禁，因此未成为当前默认。实验失败是有效结论，不应通过调整一个方便阈值包装成上线能力。

## 12. 安全与隐私审计

- JWT Principal 决定用户 scope；请求体不能改写身份。
- SourceRevision、retrieval cache、Memory 和 Tool 都必须保留 tenant/user scope。
- Prompt injection 文本只是数据，不能改变 Tool allowlist 或 system policy。
- Trace 默认脱敏，不记录完整 Prompt、工具输出、JWT 或 API Key。
- 媒体观察是不可信输入，不直接授予动作权限。
- 高风险写操作需宿主审批；模型传入的审批字段会被移除。
- 删除/retention 必须覆盖权威事实与派生投影，而不是只清 Redis。

## 13. 生产 readiness 缺口

当前仍需补齐：

1. 冻结的公开能力测试，以及 80 条合成架构合同通过真实 `ChatApplication.handle()` 的结果；
2. 对真实业务 corpus 的权限、保留、删除、PII 与审计制度；
3. 真实并发、索引增长、P95/P99、成本和故障注入容量测试；
4. 生产备份、恢复演练与经业务确认的 RPO/RTO；
5. OTel Collector、集中存储、告警、tail sampling 和值班手册；
6. 复杂文档解析、版面证据和多模态摄取的独立门禁；
7. 若出现真实流量，再设计 cohort、canary、回退和迁移 ADR。

## 14. 当前分支验收清单

- [x] PostgreSQL SourceRevision 与唯一 ACTIVE generation。
- [x] pgvector + PostgreSQL FTS 单一在线检索路径。
- [x] stable chunk id、revision/checksum/span provenance。
- [x] Raw + Standalone、weighted RRF、packing 与 grounded structured output。
- [x] FactRequirement / Coverage / Verifier fail-closed。
- [x] 公共 Knowledge 与私有业务 receipt authority 隔离。
- [x] 真实 JWT、本地 Docker、L1/L2 和 restore 机器报告。
- [ ] 冻结公开测试与 80 条合成合同真实 E2E 闭环。
- [ ] 真实生产容量、隐私治理、告警和 RPO/RTO。
- [ ] 复杂文档 ingest 与跨页/版面证据门禁。

复现实验细节见[客服 RAG 全链路评测]({{ '/rag-pipeline-evaluation/' | relative_url }})，系统边界见[架构边界]({{ '/architecture.html' | relative_url }})。

---

{% include_relative _includes/rag-evaluation-deep-dive.md %}
