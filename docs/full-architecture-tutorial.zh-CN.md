# DialogPilot 完整架构教程：从持久准入到可验证交付

> 本页以当前可运行代码为准，讲清一次客服请求如何在单一 PostgreSQL 主链中完成身份校验、路由、任务执行、证据覆盖、回答发布、送达和服务连续性。目标架构和实施计划用于解释方向与验收，不替代当前实现事实。

## 快速导航

- 想先建立全局图：读“项目定位”“端到端主链”“Owner 总账”。
- 想看 Agent：读“RouteDecision 与 TaskGraph”“有界 ReAct”“多模态”。
- 想看 RAG：读“Knowledge 与 Evidence”“Coverage 与回答发布”。
- 想看工程闭环：读“存储与恢复”“评测证据”“当前边界”。
- 想准备面试：继续阅读[面经校准]({{ '/interview-guide.html' | relative_url }})和[项目讲述]({{ '/project-pitch.html' | relative_url }})。

## 1. 项目定位与正向合同

DialogPilot 是 FastAPI 多 Agent 客服后端，不是多个 Prompt 的串联演示。它的正向合同是：

1. 可信身份和请求先被持久化，再允许模型推理。
2. 每个路由、任务、事实、工具副作用和发布状态都有唯一 Owner。
3. `EXECUTE / CLARIFY / OUT_OF_SCOPE` 构成闭合路由；未知状态有类型地失败。
4. 只有合法 Evidence 覆盖必需 Requirement，且 Verifier 通过，回答才可正常发布。
5. PostgreSQL 保存权威事实；Redis 只保存可重建的当前会话投影。
6. 本地报告只证明其声明的合成场景，不外推为生产流量、生产 SLA 或 Gold 准确率。

当前仓库采用 clean rebuild + direct cutover。因为没有线上流量和必须保留的旧数据，运行时不保留 Chroma、旧 SQLite delivery、双写、Shadow/Canary 或迁移回退链。

## 2. 端到端主链

```mermaid
flowchart LR
    C[Client + JWT] --> A[Durable Admission]
    A --> R[Canonical RouteDecision]
    R -->|EXECUTE| M[Media Requirement L0/L1/L2]
    M --> T[TaskGraph + bounded ReAct]
    T --> E[Tool Receipts + Evidence]
    E --> G[Coverage + Verifier]
    R -->|CLARIFY / OOS| P[Policy terminal]
    G -->|PASS| U[PostgreSQL Publication]
    G -->|REJECT / UNKNOWN| H[Handoff]
    P --> U
    U --> D[Delivery seq + ACK/replay]
    U --> X[Redis projection]
    U --> S[ServiceEpisode / Memory facts]
```

一次 `/chat` 的关键顺序：

1. FastAPI 验证 Bearer JWT，生成不可由请求体覆盖的 Principal。
2. Admission 在 PostgreSQL 原子记录请求、Invocation 与 start outbox；固定请求身份和版本引用。
3. Router 只产生一个 canonical `RouteDecision`。策略终态不创建虚假的 Worker 执行。
4. `EXECUTE` 才构建依赖感知 TaskGraph；每个任务声明 Owner、输入、依赖、风险、预算和成功条件。
5. 附件由 Agent 产生 MediaRequirementDecision：L0 不解析，L1 OCR，L2 OCR 加视觉推理。
6. Worker 在任务内运行有界 ReAct，只能调用 allowlist 中的工具；写操作需宿主审批。
7. Knowledge、业务工具、记忆和媒体路径产生带 provenance 的 EvidenceReceipt。
8. Coverage 对照 FactRequirement；Verifier 输出 `PASS / REJECT / UNKNOWN`。
9. 最终 publication 先写 PostgreSQL，再返回 HTTP。投影和连接器通过 outbox 异步推进。
10. 客户端 ACK 只允许送达状态单调前进，断线后按 `response_seq` 重放。

## 3. Owner 总账

| 事实或决策 | 唯一 Owner | 消费者必须遵守的合同 |
|---|---|---|
| Principal | JWT 身份边界 | body 中的 user id 不是授权依据 |
| 请求 / Invocation | PostgreSQL Admission | 推理前存在；同一 request identity 可幂等重试 |
| RouteDecision | 路由策略与 `application/route_decision.py` | 下游不能重新猜 route mode 或 authority |
| TaskGraph | Agent Planner / orchestration contract | task id 唯一、依赖存在、无环、结果闭合 |
| 工具权限与副作用 | ToolManager + 业务系统 receipt | 模型不能批准自己；未知 effect 不写成成功 |
| Knowledge | PostgreSQL SourceRevision / active generation | chunk、索引和引用都是原文的派生投影 |
| Requirement 覆盖 | AuthorityPolicy + CoverageGate | 合法 authority 才能满足对应事实需求 |
| 发布资格 | AnswerVerifier | 只有 PASS 走正常回答，UNKNOWN 不猜 |
| 最终回答与送达 | PostgreSQL Publication / Delivery | publication 先于返回；ACK 单调且可重放 |
| 当前会话窗口 | Redis projection | 可丢弃重建，不能反向覆盖 PostgreSQL |
| 长期服务经历与用户事实 | ServiceEpisode / MemoryFact | 带来源、revision 和有效状态 |
| Ticket / Handoff | PostgreSQL TicketService | 幂等创建、合法迁移、event/outbox 同事务 |
| Commitment | PostgreSQL CommitmentService | 显式来源、版本 CAS、breach 保留、receipt 履约 |

## 4. RouteDecision 与 TaskGraph

路由先回答“系统应该采取哪种路径”，TaskGraph 再回答“在 `EXECUTE` 中由谁做哪些任务”。两者不能合并成一个自由文本 Planner 输出。

```text
Intent / request shape / continuation context
    → RouteDecision(mode, authorities, risk, verification profile)
    → route-specific candidate selection
    → TaskGraph(tasks, owners, dependencies, budgets)
    → typed task outcomes
```

- `CLARIFY` 表示信息不足，需要向用户收窄问题。
- `OUT_OF_SCOPE` 表示明确不在支持范围。
- `EXECUTE` 表示已有足够证据进入领域执行。
- 一个复合请求可包含 Billing 与 Technical 两个任务；只有可分离任务才并行。
- 请求级 deadline 和 max-agents 向下传播，避免各 Worker 的 timeout 相加。
- 依赖失败、预算耗尽、等待审批、超时和错误不会被流畅的融合文本隐藏。

## 5. 有界 ReAct、工具与副作用

ReAct 只存在于领域任务内部。主编排仍由确定性 TaskGraph 控制，因此模型不能递归创建无界 Agent 或改写全局状态机。

ToolManager 负责：

- Agent allowlist 与工具风险分级；
- 参数清洗，移除模型伪造的 `approved` / `approval_token`；
- 宿主审批、持久 checkpoint 和绑定验证后的 resume；
- `tool_call_id` 幂等与 receipt 复用；
- timeout、cancelled、error、outcome unknown 等 typed outcome；
- 脱敏 trace，避免记录密钥和完整敏感输出。

网络超时不等于“业务没有发生”。当本地无法知道远端是否提交时，系统记录 unknown effect 并交由 reconciliation / 人工处理，而不是盲目重试造成重复退款。

## 6. Knowledge RAG 与 Evidence

```mermaid
flowchart LR
    S[SourceRevision] --> C[512/64 fixed-token chunks]
    C --> V[pgvector]
    C --> F[PostgreSQL Chinese FTS]
    Q[Raw + Standalone query] --> V
    Q --> F
    V --> R[Weighted RRF]
    F --> R
    R --> K[Optional rerank + Top-5 packing]
    K --> E[EvidencePack]
    E --> G[Grounded generation]
```

当前本地默认使用 Dense `.25`、Lexical `.75`、RRF `k=10`，候选 20、最终 5。384 维 feature hashing 是确定性本地 embedding 基线，目标是可复现和低依赖，不冒充高质量语义模型。

EvidencePack 保存 source revision、checksum、稳定 chunk id、字符范围、scope、rank 和 manifest fingerprint。公共知识只能证明政策与说明；“我的退款是否到账”“账户是否被冻结”等私有实时事实必须来自业务工具 receipt。

## 7. Coverage、Verifier 与发布

回答听起来完整，不等于事实已覆盖。CoverageGate 对照 RouteDecision 派生的 FactRequirement，验证每项 requirement 是否由允许的 authority 提供合格 evidence。随后 AnswerVerifier 输出：

- `PASS`：可按当前 publication kind 发布；
- `REJECT`：已确认不满足发布合同；
- `UNKNOWN`：校验器故障、解析失败或无法确定，必须失败关闭。

发布与送达分离。HTTP 200 表示服务返回了一个公共 outcome，不表示终端用户已经阅读。PostgreSQL 先生成 `response_id / response_seq`；连接器 ACK 再推进 `selected → delivered → read`。

## 8. Memory、Context 与服务连续性

上下文不是数据库内容的无差别拼接。ContextPolicy 按当前任务选择：

- Redis working window：当前会话快速读取；
- PostgreSQL Conversation：完整 turn/event 权威记录；
- ThreadSummary：带 generation 与完整性状态的压缩投影；
- ServiceEpisode：跨会话、经验证的服务经历；
- MemoryFact/Profile：带来源和 revision 的用户事实；
- ActiveCase、Ticket、Commitment：未完成事项和服务债务，优先于普通相似历史。

Token budget 归 ContextPolicy 所有。历史、工具输出、知识和当前问题必须在最终拼接文本上计费；无法容纳当前轮次时返回 typed budget failure，而不是静默截断问题。

## 9. 多模态链路

附件上传和理解分离：

1. `/assets/upload` 验证身份、类型、大小与安全状态，将 asset 绑定到本轮 turn。
2. Agent 根据任务产生唯一 MediaRequirementDecision。
3. L0 不调用感知模型；L1 用 Tesseract 提取字符；L2 在 OCR 后调用显式启用的 DeepSeek Vision。
4. ParseResult / EvidenceNode 保存 asset checksum、page/bbox、producer、model 和 version。
5. Worker 只有在任务声明 `media_observations` 时才能读取这些观察。

OCR/VLM 输出永远是“不可信派生观察”，不能直接决定退款资格、故障根因或账户状态。L2 未配置时返回 typed unavailable，不自动偷换成无来源结论。

## 10. PostgreSQL、Redis、Outbox 与恢复

PostgreSQL 是 Conversation、Invocation、Publication、Delivery、Knowledge、Ticket、Commitment、ServiceEpisode、MemoryFact、Attachment 和持久 Trace 的权威存储。Redis 只承担当前窗口、热缓存和可重建投影。

Outbox 把“事实已提交”和“异步效果已完成”分开：

- 领域事务同时提交事实和待投影事件；
- dispatcher claim 后执行 Redis/connector effect；
- 成功 ack，失败保留重试状态；
- Redis async client 始终在所属事件循环执行，数据库 claim/ack 可在线程中运行。

本地恢复报告证明空库可升级到 Alembic head，并对隔离 dump/restore 做记录对账。它不证明生产 RPO/RTO。

## 11. Handoff、Commitment 与 Trace

Handoff 不是回答末尾的布尔标志。PostgreSQL TicketService 拥有 ticket identity、优先级、合法状态迁移、event 与 outbox；重复请求通过业务 fingerprint 幂等收敛。

Commitment 只接受显式来源。它使用版本 CAS 防止并发覆盖；到期可从 scheduled 进入 breached，之后带业务 receipt 的履约可进入 late_fulfilled，同时保留 breach 历史。

TraceRecorder 默认把脱敏 span 写入 PostgreSQL，并保留进程内快速投影。配置凭据后可导出到 Langfuse v4；Collector fan-out、tail sampling 和生产告警治理不在本地作品集范围。

## 12. 评测与机器证据

评测必须调用和线上相同的 `ChatApplication`，并读取 Owner 状态和 typed outcome；不能让 fixture 接触 expected 后制造答案。

仓库的证据分为：

- 500 条 provisional 四层数据：Intent、TaskPlan Routing、Retrieval、Stateful；
- Service-chain v2：按 perception、route、memory、retrieval、tool、publication、handoff、delivery 等层评分；
- Doc2Dial RAG：Chunk、Query、Retrieval、Rerank、Packing、Generation 分层实验；
- pytest：Owner 合同、状态机、属性和集成回归；
- Docker E2E：真实 JWT、PostgreSQL、Redis、OCR/VLM、Commitment、Trace；
- restore rehearsal：空库迁移和隔离恢复对账。

最新已提交机器报告见：

- [本地完整链](../evaluation/reports/local-e2e-v1.json)
- [视觉 L2](../evaluation/reports/local-vlm-e2e-v1.json)
- [Commitment](../evaluation/reports/local-commitment-e2e-v1.json)
- [持久 Trace](../evaluation/reports/local-trace-e2e-v1.json)
- [PostgreSQL restore](../evaluation/reports/local-postgres-restore-v1.json)

报告中的 `scope_limit` 是结论的一部分。provisional、auto-mapped 或已经参与修复的 heldout 不能改名为 human-reviewed fresh Gold。

## 13. 本地运行与验证

```bash
cp .env.example .env
# 填写 ANTHROPIC_API_KEY 与至少 32 字节的 AUTH_JWT_SECRET
docker compose up -d --build --remove-orphans
curl http://localhost:18000/health
```

```bash
PYTHONPATH=. .venv/bin/python scripts/run_local_e2e.py \
  --output evaluation/reports/local-e2e-v1.json

PYTHONPATH=. .venv/bin/python scripts/run_local_trace_e2e.py \
  --output evaluation/reports/local-trace-e2e-v1.json

PYTHONPATH=. .venv/bin/python scripts/rehearse_x_t01_restore.py \
  --database-url postgresql://dialogpilot:dialogpilot-local@localhost:15432/postgres \
  --postgres-container dialogpilot-postgres \
  --output evaluation/reports/local-postgres-restore-v1.json
```

测试和迁移命令必须从仓库根目录运行并设置 `PYTHONPATH=.`；报告应保留 commit、环境和 scope，而不是只抄一个 passed 数字。

## 14. 仓库地图

```text
api/              HTTP 合同与 composition root
application/      Admission、Chat、Route、Coverage、Projection 用例
agents/           Planner、TaskGraph、Worker、bounded ReAct
mcp/              ToolManager、Evidence、packing 与 grounded generation
infrastructure/   PostgreSQL/Redis 仓储、检索、outbox 与 dispatcher
memory/           Working context、summary 与事实提取
services/         Verifier、Ticket、Commitment、Bad Case/Bundle 元数据
evaluation/       Dataset、runner、grader 与机器报告
scripts/          本地 E2E、迁移、恢复与实验入口
docs/             目标架构、实施计划和 GitHub Pages
```

## 15. 当前边界与下一步

已实现并可本地复现：单一 PostgreSQL 服务链、TaskGraph/有界 ReAct、混合 RAG、Evidence/Coverage、发布与 ACK、Redis 投影、PostgreSQL Ticket/Commitment、L1/L2 多模态和持久脱敏 Trace。

仍需诚实标注：

- 没有生产流量、生产 SLA、生产 RPO/RTO 或组织级值班与发布治理；
- Bad Case、ReAct checkpoint 和 Bundle metadata 仍有本地 store；
- DeepSeek Vision 是显式启用的实验路径；
- 评测数据仍包含 provisional/auto-mapped 标签，fresh human-reviewed Gold 未闭合；
- LangGraph 薄 runtime、复杂文档摄取和生产 OTel Collector 属于后续目标，不是当前已交付能力；
- 当前直接替换模型不需要 Shadow/Canary；未来若出现真实不可丢数据或线上流量，应新建迁移 ADR。

继续阅读：[架构边界]({{ '/architecture.html' | relative_url }}) · [Agent 进化闭环]({{ '/agent-evolution/' | relative_url }}) · [500 条分层评测]({{ '/evaluation-500/' | relative_url }}) · [RAG 生产化审计]({{ '/customer-service-rag-production-audit/' | relative_url }})。
