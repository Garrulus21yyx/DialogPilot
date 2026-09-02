# 客服 Agent 目标架构实施跟踪

- 分支：`feat/customer-service-target-architecture`
- 权威需求：
  - `docs/customer-service-agent-target-architecture.zh-CN.md`
  - `docs/customer-service-agent-implementation-plan.zh-CN.md`
- 执行原则：按依赖 DAG 推进；每张任务卡独立验证、记录文件、commit 并 push；不把 `IMPLEMENTED` 冒充 `VERIFIED` 或 `READY`。
- 当前阶段：M2

## 状态

| 节点 | 状态 | 验证 / 产物 |
|---|---|---|
| Bootstrap：冻结需求文档与执行跟踪 | done | commit `c2beceb`，已 push |
| M0-T01 应用服务边界 | done | typed `ChatOutcome`、薄 `/chat`；392 tests passed |
| M0-T02 稳定身份和值对象 | done | stable IDs/keys；Trace/Tool/Ticket/Delivery/Memory propagation |
| M0-T03 生产主链 Eval Runner | done | full execution 共用 `ChatApplication`；typed stage/owner evidence |
| M0-T04 当前行为基线 | done | `data/eval/baselines/m0-v1/manifest.json`，7 条真实主链 Trace |
| M0-T05 Gate Manifest Foundation | done | `evaluation/gates/m0-exit/v1.*`，decision=`APPROVE` |
| M1-PF01 PostgreSQL Platform Foundation | implemented | 生产快照副本验证待真实快照；本地 restore drill 通过 |
| M1-T00 Admission/Execution/ChatOutcome v1 | done | CAS/ports/projection/OpenAPI/M3 cutover contract |
| M1-T01 ConversationTurnStore schema | done | PostgreSQL migration `0002` + immutable scoped repositories |
| M1-T02 Inbound-first / outbox dispatcher | implemented | PostgreSQL `0003`；生产 `/chat` cutover 归 M1-T05 |
| M1-T03 Unified publication/delivery | done | PostgreSQL `0004`；atomic publication/delivery outbox + canonical receipt lifecycle |
| M1-T03A ResponseDelivery PostgreSQL 单主切换 | implemented | PR-10A/10B + local crash/restore drill；production snapshot cutover unverified |
| M1-T04 Conversation projection outbox/deletion fence | implemented | PostgreSQL `0006`；4 projections + generation watermark + tombstone epoch |
| M1-T04A DataLocationRegistry / pre-write fence | done | PostgreSQL `0007`；31 stable locations，4 write-approved，future writes fail closed |
| M1-T05 Conversation/API read projections | implemented | PostgreSQL `0008`；turn/status/finalize watermark/close + PG delivery compatibility |
| M1 完整会话事实与幂等发布 | in_progress | 按 T00–T05/T03A/T04A 子节点推进 |
| M2-PF01 共享 PostgreSQL HybridRetrievalBackend | implemented | PR-18P-A/B/C done；生产质量、RTO/OLTP gate 尚未 VERIFIED |
| M2-T01A Agent-owned Intent/Domain/Instance policy | done | V1 registry + typed decisions/trace；582 tests passed |
| M2-T01 RouteDecision / RouterInvocationPolicy | done (flag-off) | 8 modes + call/skip algebra；602 tests passed |
| M2-T02 FactRequirement / AuthorityPolicyRegistry | done (planner flag-off) | minimum requirements + startup manifest gate + refund_status；609 tests passed |
| M2-T03 Canonical EvidenceReceipt | done (consumer flag-off) | 7 kind typed algebra + registered adapters + resolver verification；624 tests passed |
| M2-T04 Requirement CoverageGate / VerificationProfile | implemented (flag-off) | build complete；Knowledge verification waits T04A；642 tests passed |
| M2-T04A SourceRevision v0 / active manifest | implemented (review pending) | PG owner/backfill/active validator done；independent human heldout review pending；650 tests passed |
| M2-T05 统一 KnowledgeRetriever | implemented (canary blocked) | A1/A2/B + immutable PG dark-shadow report；675 tests passed |
| M2-T06A Multi-Agent TaskGraph 收紧 | implemented | A/B/C policy、execution、dependency、native signal 与 terminal algebra 完成；697 tests passed |
| M2-T06 自适应 RAG 发布路径 | in_progress | A/B1/B2/B3: gated ChatApplication adapter done；native tool receipts + shadow executor pending；744 tests passed |
| M2 Route/Authority/Evidence/RAG | in_progress | 按 M2-PF01、T01–T06R 子节点推进 |
| M3 薄 Durable Agent Runtime | pending | 按 M3-T01–T09 子节点推进 |
| M4 Memory/Context/Commitment/Handoff | pending | 按 M4-T01–T08 及 release 子节点推进 |
| M5 Knowledge Lifecycle/Multimodal | pending | 按 M5-T01–T09 子节点推进 |
| M6 Eval/Observability/Release | pending | 按 M6-T01–T09 子节点推进 |
| X-T01–X-T05 跨里程碑治理 | pending | 在各 Gate 依赖点前完成适用项 |

## 变更记录

### Bootstrap

- 已创建分支 `feat/customer-service-target-architecture`。
- 工作区起始时存在其他未跟踪实验文件；不纳入本任务提交，除非后续任务卡明确需要。
- 计划提交文件：两份权威需求文档与本跟踪文件。

### M0-T01

- 正向合同：认证后的 `ChatCommand` 进入唯一 `ChatApplication.handle()`；结果属于显式
  `ChatOutcome` tagged union；HTTP adapter 只做输入安全、身份绑定与 HTTP 映射。
- Owner 修复：完整主链从 `api.main.chat` 移到
  `application/chat_application.py`；API 不再选择 RAG、执行 Agent、发布候选或写 Memory。
- 修改文件：
  - `application/__init__.py`
  - `application/chat_application.py`
  - `api/main.py`
  - `tests/test_chat_application.py`
- 验证：
  - `PYTHONPATH=. .venv/bin/pytest -q` → `392 passed`
  - `ruff check --ignore E402 application api/main.py tests/test_chat_application.py` → passed
  - `git diff --check` → passed
- 说明：M0 保持现有同步聊天语义；`Accepted/NeedsInput/...` 合同已冻结，具体 admission
  与 execution 投影由 M1-T00/M1-T05 实现。

### M0-T02

- 正向合同：`IdentityFactory` 在 Application 边界一次性解析
  `TenantId/UserId/ConversationId/RequestId/ContinuationId/TurnId/WorkflowRunId`；
  `TurnKey/InvocationKey/OperationKey` 使用带 namespace/version 的无歧义 canonical tuple
  SHA-256 构造，外部投影仍为字符串。
- Continuation：`ContinuationIdFactory` 只接受 typed `StartNew` 或 Agent Gate 已校验的
  `ReusePriorFrame(frame_ref, frame_version)`；复用前强制校验 tenant/user/conversation/version。
- 消费者迁移：同一 identity metadata 已贯穿 Application trace、Agent/ReAct execution context、
  Tool handler/audit、Ticket fact/outbox、ResponseDelivery fact 与 Memory message；Ticket、Delivery
  使用各自稳定 `OperationKey`。Agent `Request` 不再随机铸造第二个 request ID，shadow 也复用原 ID。
- 持久层：Ticket/ResponseDelivery SQLite 增加 `identity_metadata_json` 的幂等 forward migration；
  现有数据库默认回填 `{}`，新写入携带完整稳定引用。
- 修改文件：
  - `core/identity.py`
  - `application/chat_application.py`
  - `agents/agent_orchestrator.py`
  - `mcp/tool_manager.py`
  - `services/ticket_service.py`
  - `services/response_delivery.py`
  - `api/main.py`
  - `tests/test_identity_contracts.py` 及相关 owner/集成测试
- 验证：
  - `PYTHONPATH=. .venv/bin/pytest -q` → `409 passed`
  - 相关文件 `ruff check --ignore E402` → passed
  - `git diff --check` → passed
  - 负向搜索未发现业务路径继续以 `uuid4` 或 `:shadow` 派生 request ID。

### M0-T03

- 正向合同：`ChatApplicationRunner.run(ChatCommand) -> ChatRunResult` 只调用生产应用服务，
  保存公开 outcome、typed stage observations、owner probes 和 latency；完整执行缺少 runner 时
  fail closed，不再回退到 `orchestrator.run()`。
- 可替换环境：`ChatRuntimeOverrides` 明确承载 model、clock、business backend、knowledge index
  和 delivery adapter，由 application factory 在构造同一主链时适配；测试验证五种替换均透传。
- 阶段证据：Application 记录 `memory_load/intent/knowledge_retrieval/route_and_agent/tool/
  verification/ticket/delivery/memory_write` 的 `OK/SKIPPED/DEGRADED/FAILED` typed observation。
- Eval 迁移：routing-only 仍调用 Planner 局部 runner；所有 `full_execution` 通过
  `ChatApplicationRunner`，并在单个 fixture metadata 同时归档公开 response、stage evidence
  与 owner state probe。
- 修改文件：
  - `application/chat_application.py`、`application/__init__.py`
  - `evaluation/chat_application_runner.py`
  - `evaluation/evaluator.py`
  - `api/main.py`
  - `tests/test_chat_application_runner.py`
  - `tests/test_eval_graduation.py`、`tests/test_chat_handoff.py`
- 验证：
  - `PYTHONPATH=. .venv/bin/pytest -q` → `412 passed`
  - 相关文件 `ruff check --ignore E402` → passed
  - `git diff --check` → passed
  - `evaluation/evaluator.py` 负向搜索无 `orchestrator.run()`。

### M0-T04

- 已实现可重放 `BehaviorBaseline` schema、版本漂移 fail-closed 校验、逐 route 运行记录聚合、
  六类决策策略及独立 fingerprint；schema 明确禁止 production accuracy 汇总。
- 已实现真实 `ChatApplication` characterization capture 与 freeze CLI；capture 使用临时
  Ticket/Delivery/BadCase/Operation/Run 数据库、独立 baseline 用户/会话 identity，并输出实际
  Knowledge index manifest 与逐 Trace 记录。
- 真实 capture：7 个用例均经 `ChatApplication` 和实际模型运行，逐条保留 9 个阶段、request/trace、
  route、延迟、模型/工具调用、发布与失败类型；工具后端的 `order not found` 被明确计为
  `tool:error`，未被最终回答吞成成功。
- 隔离与环境事实：Ticket/Delivery/BadCase/Operation/Run 使用临时 SQLite，Memory 使用 Redis DB 15；
  部署的 Chroma collection 因 legacy chunk 缺少当前 index-contract metadata 被 owner validator 拒绝，
  已记录为 `INCOMPATIBLE_KNOWLEDGE_INDEX`，基线改用隔离构建的当前合同 public corpus，且不声称完成
  deployed-index certification。
- 冻结产物：`cases.json`、`records.jsonl`、`rag-index-manifest.json`、
  `environment-observations.json`、`manifest.json`；最终 manifest 绑定 capture commit `94ad6e6`、
  tree、active Bundle/content hash、模型策略、RAG fingerprint、六份数据 checksum 及七类决策策略指纹。
- 验证：manifest 可重放且 checksum 封闭；Bundle version/hash、commit 或 index 任一漂移均 fail closed；
  不存在 production accuracy 汇总；每条记录可追溯到 case/request/trace/stages。

### M0-T05

- 正向合同：通用 Gate 规格拥有 typed `TaskRef/GateDecisionRef/ArtifactRef/
  ConditionalRequirement` prerequisite algebra，manifest 生命周期唯一为
  `DRAFT→FROZEN→RUNNING→DECIDED`；`N/A` 只属于有 reason/approver 的 conditional。
- 冻结与签署：spec 绑定数据、oracle、零容忍性质、统计阈值、故障点、成本/SLO、Owner、
  independent approver 与 rollback；Evidence Owner 和 approver 必须是不同 signer，冻结后 spec
  fingerprint 不变，运行开始后只能创建 superseding version，不能原地改阈值或数据。
- 存储与 lint：`GateStore` 使用 `evaluation/gates/<profile>/<version>.yaml`（JSON 兼容 YAML）和
  immutable evidence/decision 文件；archive linter 重建 RUNNING revision 并校验 manifest、evidence、
  decision 三层 checksum 与引用。
- 首个实例：`M0-EXIT/v1` 绑定 M0-T01..T05、六份数据身份、行为基线文件 checksum 与 431-test
  证据；Evaluation producer 与 Application contract verifier 分角色签署，decision=`APPROVE`。
- 验证：property-style/参数化测试覆盖缺失/未知 prerequisite、Task/Gate/Artifact 非法 N/A、
  非法状态跳转、同 signer、运行后规格漂移、未满足 prerequisite 的 APPROVE 与 archive tamper。

### M1-PF01（IMPLEMENTED，尚未 production-verified）

- 平台决策：PostgreSQL 18.1、Psycopg 3.3.5/Pool 3.3.1、Alembic 1.19.1、SQLAlchemy
  2.0.52；默认 `READ COMMITTED`，schema namespace 为 `dialogpilot_platform/dialogpilot_app`。
- 本地/CI：Compose 增加 health-checked PostgreSQL；CI 注入隔离 service DB 并在测试前显式运行 migration；
  Testcontainers fixture 可由 `RUN_POSTGRES_TESTCONTAINER=1` 启动，所有集成测试再创建一次随机隔离 DB。
- Migration owner：应用不隐式建表；Alembic 建立 namespace、schema ledger 和 data migration ledger；
  runner 校验已应用 revision 文件 checksum/head，数据库不可用或漂移时 typed fail closed，不回退 SQLite。
- Cutover：ADR 与 runbook 固定 snapshot/backfill/shadow-read → freeze/stop old writer → final delta/
  reconcile → atomic binding switch → start new writer → forward-fix/restore，禁止双 writer 窗口。
- 旧库盘点：Ticket、ResponseDelivery、RunStore 的表、唯一键、状态、时间、tenant gap、导出、retention
  和 `migrate/retain/retire` 决策已机器归档；三者在各自 cutover 前继续作为各领域单主。
- 验证：真实 PostgreSQL 18 空库安装、重复升级、pool/search_path/隔离、checksum 漂移、不可用、canonical
  count/content reconcile 通过；本地 custom dump/restore `<1s`，dump SHA 与 ledger/count 已归档；全套
  `439 passed`。当前无生产快照副本，因此不声明 production snapshot upgrade/restore `VERIFIED`，对应
  technical cutover gate 保持未满足。

### M1-T00

- Admission v1：唯一状态为 `START_QUEUED/EXECUTION_BOUND/EXPIRED_BEFORE_START`，仅允许 queued
  CAS 到另外两态；claim/lease/attempt 不进入业务状态。request fingerprint canonical 化，同 key
  同内容绑定既有 admission，同 key 异内容的 typed result 为 `IDEMPOTENCY_CONFLICT`。
- Ports：冻结 `InvocationRepository/AdmissionUnitOfWork/StartOutbox/Dispatcher/PendingSignalStore`、
  expected status/version CAS、stable outbox key 与 lease command；本卡未建表或启动 worker。
- Thin Execution：投影优先级为互斥 terminal → PendingSignal → runtime running → admission；多个 terminal
  fail closed。`COMPLETED` 只能由同时绑定 response/outbound event/delivery outbox 的
  `FinalPublicationCommitted` 事实产生。
- Signal：Principal 才投影 `NeedsInput`；media receipt 只允许 poll，reconciliation receipt 只允许 poll
  且禁止 replay write；signal consume 代数覆盖 applied/already/conflict/expired/unauthorized。
- M3 cutover：当前 RunStore 八种状态均有唯一映射；`COMPLETED` 仍要求既有 final publication，
  `BLOCKED/TOOL_ERROR/MAX_STEPS` 映射为不同 typed failure，不压成未知字符串。
- Public contract：Pydantic/OpenAPI 和 protocol-neutral mapper 共同冻结所有 ChatOutcome 的 HTTP、body、
  client action 与 retry hint；API adapter 不再把非 Completed outcome 统一变成 500。
- 验证：state product/参数化 tests、terminal conflict、signal replay、fingerprint、M3 enum surface 和
  OpenAPI status 全覆盖；真实 PostgreSQL fixture 下全套 `466 passed`。

### M1-T01

- PostgreSQL schema：`conversations/conversation_turns/conversation_events/workflow_invocations/
  response_deliveries` 全部位于 `dialogpilot_app`；tenant/user/conversation 为显式 scope，TurnKey、
  InvocationKey、OperationKey、publication/delivery key 具有数据库唯一约束与 retention 字段。
- Transcript owner：Conversation 行在事务内持有独立 turn/event/publication next-seq；append 锁定 scope
  行后分配并同事务递增，失败回滚不会留静默洞。Turn/Event 有 content SHA，数据库 trigger 禁止 UPDATE；
  role 只接受 inbound/assistant/human/system_event，不保存内部 prompt。
- Invocation：仅保存 admission、pinned versions、opaque runtime pointer、terminal ref 与 CAS version；
  DB CHECK 强制只有 `EXECUTION_BOUND` 携 pointer，不新增 runtime lifecycle 列。
- Delivery schema：通用 publication ID/kind/operation key，状态覆盖
  `SELECTED→DELIVERING→DELIVERED/OUTCOME_UNKNOWN/FAILED` 与可选 READ、receipt/reconciliation；
  本卡只建 owner schema，发布命令与 outbox 在 T03 实现。
- Repositories：turn/event append 对同 key 同内容返回 `ALREADY_APPLIED`、异内容返回 typed conflict；
  transcript/invocation read 强制 tenant+user scope；Invocation repository 直接实现 T00 CAS interface。
- 验证：16 路同 key 并发只落一条；失败事务后 seq 为 `[1,2]`；immutable trigger、跨 scope deny、
  admission replay/conflict/bind CAS 均在真实 PostgreSQL 18 验证；Alembic head=`0002`，全套
  `472 passed`。

### M1-T02（IMPLEMENTED，cutover pending M1-T05）

- InboundDisposition：无显式 binding 的自由文本始终是 `NewInvocationInbound`，不会因开放 signal 自动
  resume；显式 binding 只经 `SignalAuthority` 的 tenant/user/conversation/version/kind/schema 校验，
  不调用 Memory/RAG/LLM/Tool，结果为 typed valid/invalid disposition。
- New admission 单事务：inbound turn、`REQUEST_ACCEPTED` event、`START_QUEUED` invocation 与唯一
  `WorkflowStartRequested` outbox 一起提交；canonical request fingerprint 绑定 authenticated scope、
  continuation 和 message，重试返回 existing，异内容 conflict。
- Resume 单事务：合法 binding 只写 inbound、`RESUME_REQUESTED` 与唯一 resume outbox，绝不新建
  invocation/start outbox；非法/过期/越权/version-kind-schema 错误写 `RESUME_REJECTED` event 且两种
  outbox 都不产生。同 request 改 binding 为 typed idempotency conflict。
- Dispatcher：`FOR UPDATE SKIP LOCKED` claim + stable outbox ID/lease/attempt；binder 必须按同一
  InvocationKey get-or-create 同一 run，再用 T00 status/version CAS 绑定。CAS 前后或 ACK 后崩溃分别
  release/reclaim、already-applied/ACK、known-bound，均不创建第二执行身份。
- 验证：四个 admission 事务故障点全部零残留；stale lease 只重领同一 item；CAS 后重试只创建一个
  run；ACK 后崩溃不重排；正常/合法 resume/非法 resume 的 start-vs-resume outbox 排他性质通过；
  Alembic head=`0003`，全套 `487 passed`。
- 激活边界：当前同步 `/chat` 尚未切到 admission，因为 T03/T04 publication 与 compatibility worker 尚未
  就绪；现在切换会产生永久 `Accepted`。M1-T05 将在整条恢复/发布链可用后执行唯一入口 cutover。

### M1-T03

- 已冻结 canonical `DeliveryStatusV1`、connector capability 与 receipt/event algebra：
  `SELECTED/DELIVERING/DELIVERED/OUTCOME_UNKNOWN/DELIVERY_UNCERTAIN/FAILED/READ`，connector 仅为
  `IDEMPOTENT_SEND/QUERY_RECEIPT/NONE`。
- send 断链只有 idempotent send 能自动重发；query-receipt 必须先得到权威 `NOT_DELIVERED`；NONE
  直接 uncertain。READ 与 delivered receipt 单调优先，READ 后 `NOT_DELIVERED` 为 typed conflict；
  retry exhaustion 不超过 max_attempts。
- property-style product 测试遍历所有 state × capability × event，每一组合必须得到 typed transition
  或 `InvalidDeliveryTransition`，不存在未知字符串/fallthrough。
- 三类命令：FINAL_RESPONSE 以 InvocationKey 唯一选择并绑定 candidate/verifier/evidence/Bundle/Index；
  INTERACTION_REQUEST 以 signal/version 唯一且签名绑定 resume；HUMAN_REPLY 以
  ticket/handoff/human-message 唯一。三类共享 publication/delivery identity，不共享业务终态。
- 原子事务：锁定 conversation 序列 owner 后，同事务追加 outbound turn、conversation event、
  immutable response-delivery selection 与 delivery outbox；四个故障注入点均整体回滚。加锁后复查
  publication，12 路并发相同命令只提交一份事实，turn/event/publication 序列无洞。
- 幂等与冲突：同命令重放为 `ALREADY_APPLIED`，同业务身份改变内容为
  `IDEMPOTENCY_CONFLICT`；final selection 唯一能产出 `FinalPublicationCommitted`，interaction 与
  human publication 均不修改 invocation admission/terminal 状态。
- 投递回执：每个 receipt ID 绑定 event+payload checksum；发布行锁内判定回执并转移 canonical state，
  重放稳定、异内容 typed conflict。首次 delivered/read 时间保持单调，迟到 delivered 不会把 READ
  降级或覆盖时间；无 connector guarantee 的未知发送结果进入 `DELIVERY_UNCERTAIN` 且禁止重发。
- Migration `0004` 在 T03A cutover 前显式要求 PostgreSQL `response_deliveries` 为空，不对未知 legacy
  行猜测回填；legacy SQLite backfill/shadow/reconcile/binding switch 仍由 M1-T03A 执行。
- 验证：canonical state product tests、原子 crash/retry、并发幂等、三类语义、ACK/READ 单调性均通过；
  Alembic head=`0004`，全套 `502 passed`。

### M1-T03A（IMPLEMENTED，尚未 production-verified）

- PR-10A 已实现只读 SQLite snapshot exporter、版本化字段/状态/ID 映射、单事务 PostgreSQL backfill、
  shadow-only reconcile 与运维 CLI；snapshot 自校验 row/status/ID/content checksum、final invocation、
  outbox 和会话 publication seq 唯一性。
- owner 边界：旧 SQLite 在切换前仍为唯一 authority；export/backfill 不写旧库。缺失 tenant/invocation、
  metadata scope 冲突、目标 Conversation/Invocation 不存在、多份 legacy final 或 target 非匹配数据均 typed
  fail closed，不伪造 transcript/admission authority。
- legacy `response_id` 原样成为 `publication_id`；legacy 没有 outbox ID，因此 exporter 首次确定性生成并冻结
  compatibility outbox ID。`delivered/read` 单调映射；无 receipt 的 `selected` 使用 connector=`NONE` 映射为
  `DELIVERY_UNCERTAIN`，所有迁入 outbox 均 ACK 且 `automatic_send_disabled`，不假定可重试或重复发送。
- 对账逐一覆盖 count、canonical status count、publication ID hash 和包含 scope/request/invocation/seq/text/
  timestamps/outbox 的 content hash；重复 backfill 仅在完整匹配时幂等成功。
- PR-10B：SQLite 新增事务内持久 writer fence；selection/ACK 在 `BEGIN IMMEDIATE` 后检查
  `ACTIVE/FROZEN/RETIRED`。PG `delivery_repository_binding` 用 generation CAS 管理
  `SQLITE_ACTIVE→FROZEN→POSTGRES_ACTIVE`，每次迁移写 immutable audit event；PG active 后没有回到
  SQLite 的 transition。
- Cutover coordinator 固定次序为 legacy freeze→PG freeze→final export/delta→同事务 reconcile+binding
  switch→legacy retire；只有 PG active 且 SQLite retired 才允许 worker resume。switch 前 abort 先恢复
  PG binding 再释放相同 freeze ID，switch 后只允许 PG restore/forward-fix。
- 崩溃性质：六个注入点（legacy freeze、binding freeze、final export、final backfill、binding switch、
  legacy retire）均能重入收敛，且任何观察点都不存在两个 active writer。final delta 只补缺失稳定 ID，
  既有 target 异内容或额外记录使整批回滚。
- 本地 restore：PostgreSQL 18.1 custom dump/restore 后保持 head=`0005`、binding=`POSTGRES_ACTIVE:3`、
  delivery/outbox=`1/1`、binding events=`2`，count/status/ID/content hash 全匹配；dump SHA 和 scope limit
  已归档到 `docs/data/response-delivery-cutover-restore-evidence-2026-09-02.json`。
- 验证：全套 `518 passed`。当前没有生产 SQLite/PG snapshot 副本和维护窗口授权，因此不声明生产
  technical cutover `VERIFIED`；旧生产 writer 仍按现状单主，M1-T05 只在真实 T03A gate 后恢复新 admission。

### M1-T04（IMPLEMENTED，activation pending M1-T05）

- Event owner：migration `0006` 在 `conversation_events` AFTER INSERT trigger 中为
  `working_window/thread_summary/episodic_index/fact_extraction` 原子生成 projection outbox；admission、
  resume、final/interaction/human publication 和 deletion 不再各自负责补写。source 事务回滚时 outbox 一起
  回滚，外部 projection 失败只释放 lease，不撤销已发生的会话事件。
- Projection contract：每个 target 有稳定 generation/policy/location ID，每个
  target+generation+subject 保存 source event watermark；同 subject 严格按 event seq claim，不同 subject
  可 `SKIP LOCKED` 并发。重建先原子晋升 generation，再从 immutable event stream 重新排队，不覆盖旧
  generation 证据。
- 幂等/恢复：adapter effect 使用 `projection+generation+event+deletion_epoch` operation key；effect 后崩溃
  重试得到 `ALREADY_APPLIED`，ACK 后崩溃为 known-applied 且不再 claim。backend unavailable 时 source
  event、outbox 与 watermark 责任边界保持明确。
- 显式策略：normal inbound/resume/final/human 可进入四类 projection；OOS、clarification、approval 和
  rejected resume 只进入 working window/thread summary；deletion event 必须进入所有 target 执行清理。
- 删除 owner：Conversation 行持有单调 `deletion_epoch/deleted_at`，删除 command 写 immutable deletion fact
  和 `CONVERSATION_DELETED` event。数据库 BEFORE INSERT fence 阻断 tombstone 后的 turn/event/publication；
  worker 在外部 effect 前后复查 epoch，竞态时调用 delete adapter 并 ACK `DELETION_FENCED`，防止补偿任务
  复活旧数据。
- 验证：source/outbox 原子性、lease retry、effect/ACK crash、policy algebra、generation rebuild、adapter
  unavailable、delete-vs-write race 和 late-write DB fence 全覆盖；Alembic head=`0006`，全套
  `537 passed`。
- 激活边界：当前同步 legacy `ConversationMemory.add_messages` 仍随旧 `/chat` 单主运行；M1-T05 在新
  admission/publication 主链启用时关闭该直写并启动 projection adapters。当前卡不把未切流量表述成生产
  projection 已启用。

### M1-T04A

- Registry artifact：`governance/data_locations/v1.json` 以语义 fingerprint 冻结 31 个独立 durable
  surface 的稳定 location ID、Owner、schema、retention、bounded producer、readiness、delete/de-identify
  adapter、proof contract 与 restore fence。Approval/Tool、Commitment/Handoff、Attachment/DerivedAsset、
  Redis exact/embedding/perception、Trace/Eval/Shadow、running invocation/external processor 均为独立 ID，
  不用聚合 ID 隐藏责任边界。
- Readiness：只有四个已实现的 M1 PostgreSQL location 为 `WRITE_APPROVED`；checkpoint、LangGraph Store、
  Memory/Profile/Episode、Case/Continuity、Ledger、Multimodal、cache、Trace/Eval/Shadow、Knowledge/Episode
  index、feedback/backup 等均仅 `REGISTERED`。未实现 adapter/proof/restore fence 时 producer、migration、
  backfill、dark shadow、restore 都不能借“已登记”越过首写围栏。
- Pre-write fence：`DataWriteIntent` 同时绑定 location/producer/write-kind/schema/retention/subject/
  expected deletion epoch；合同不匹配、未知 location、planned location、subject 不存在且 location 无创建权、
  tombstone 或 epoch 漂移全部 typed fail closed。Adapter 与 RestoreFence 有显式 Protocol/DeletionProof。
- Migration gate：`0007` 将 registry version+fingerprint 绑定到 immutable PG revision；runner 在 upgrade/verify
  时同时验证 artifact binding。从 `0007` 起每个 migration 必须声明 `subject_linked_write` 与
  `data_location_ids`，subject-linked migration 不得为空且只能引用 write-approved location。
- 验证：artifact/catalog 结构、缺 proof、未知/planned/wrong producer/schema/retention、subject-create 权、
  四类 late write（producer/backfill/shadow/restore）、DB binding immutable 与 runner verification 全覆盖；
  空库重建到 Alembic head=`0007`，全套 `544 passed`。

### M1-T05（IMPLEMENTED，production activation gated）

- Transcript：新增 scoped `GET /conversations/{id}/turns?after_seq=`，只返回
  `seq/role/content/created_at/request_id` allowlist；Bearer/secret/email/card 经过 public redactor，跨
  tenant/user 统一拒绝且分页 cursor 稳定。旧 `/responses` 明确标记
  `assistant_only_compatibility/deprecated`。
- Invocation view：新增 `GET /invocations/{invocation_key}`，只读组合 Admission、compat runtime、最新
  PendingSignal、唯一 final publication、独立 DeliveryStatus 和 active Ticket；final publication 优先于
  runtime，legacy `COMPLETED` 缺 final 会投影 FAILED 而不是伪 COMPLETED，无 READ ACK 仍为 COMPLETED。
- Finalize/close：legacy `finalize` 在 PG mode 只比较当前 projection generation 的四个 watermark，未追平
  返回 202，追平返回 200；它不写 closed/deleted。`POST /conversations/{id}/close` 单事务写 immutable close
  fact + `CONVERSATION_CLOSED` event，DB fence 阻止后续 turn/event/publication，重放不新增审计事实。
- Delivery compatibility：PG binding=`POSTGRES_ACTIVE` 时 lifespan 原子选择 PG compatibility service，
  final selection 仍调用 T03 canonical transaction；ChatApplication 传递 candidate、完整 Verifier outcome、
  evidence hash、Bundle 和真实 Knowledge index manifest fingerprint。client delivered/read 转 canonical
  receipt；same invocation retry、跨用户防枚举和 binding 非 PG fail-closed 均通过。
- Binding 边界：`SQLITE_ACTIVE/FROZEN` 继续使用 legacy ResponseDelivery，不能因配置了 DATABASE_URL 就
  偷换 writer；生产 PG writer 只在真实 T03A maintenance gate 后启用。当前同步 `/chat` admission/runtime
  cutover 仍未执行，避免在 compatibility execution recovery 未闭合时制造永久 Accepted。
- 验证：read model/HTTP adapter、脱敏/分页/权限、WAITING→COMPLETED、delivery 独立、watermark、close
  fence、PG compatibility select/retry/ACK/READ/replay 全覆盖；Alembic head=`0008`，全套 `554 passed`。

### M2-PF01 / PR-18P-A（合同、generation registry、PostgreSQL foundation）

- Backend-neutral contract：`HybridRetrievalBackend` 只返回 Dense/Lexical 两路带连续 source rank 的
  candidate 与六种 typed status；请求固定 tenant/corpus/backend/generation/policy，并用不同 scope type
  约束 Knowledge 与 ServiceEpisode。合同不包含 RRF、recency、rerank、packing、evidence sufficiency
  或最终答案。
- Generation owner：不可变 generation 固定 backend/schema/watermark/embedding/dimension/digest/cosine/
  pgvector/index/tokenizer/lexical/manifest；状态只允许
  `REGISTERED→BUILDING→READY→ACTIVE→RETIRED`（任一构建前状态可按合同失败），active pointer 使用 corpus+
  backend 独立 CAS 并保留 previous pointer。未固定 64 位模型 digest 的 legacy MiniLM generation 明确标记
  不可跨环境重放。
- DataLocation：新增不可修改 v1 的 v2 overlay；只把 Knowledge/Episode index 两个 location 提升为
  `WRITE_APPROVED`，绑定 retrieval projection deletion adapter/proof/restore fence，其他 planned location
  保持 `REGISTERED`。Migration `0009` 先安装 v2 fingerprint，`0010` 才创建 subject-linked schema。
- PostgreSQL：Compose/Testcontainers 固定官方 `pgvector/pgvector:0.8.6-pg18-bookworm`；独立
  `dialogpilot_retrieval` NOLOGIN role、`retrieval` schema、pool/resource budget、statement timeout 和 pool
  metrics，不复用 OLTP 请求池。role 只能读 generation/pointer，并写删两张 projection 表，不能修改 registry。
- Corpus schema：`knowledge_chunk_search` 与 `service_episode_search` 独立，分别含 ACL/filter/source revision/
  provenance/deletion epoch 及各自专属字段；DB trigger 在写入事务中验证 corpus generation 可写、embedding
  dimension 和 canonical Conversation deletion epoch。缺 subject、tombstone、epoch 漂移或混维全部 fail
  closed；generation definition DB trigger 禁止原地修改。
- 激活边界：本提交没有 adapter 查询、HNSW、中文 tokenizer/FTS candidate 生成、canonical outbox projection、
  backfill/shadow 或任何在线 consumer 切换；这些属于 PR-18P-B/C。
- 验证：backend/status/rank/scope 与 generation state-machine property-style tests；真实 pgvector 0.8.6 上验证
  空库/重复 migration、独立 role/timeout/权限、registry CAS/不可变、混维与 deletion fence；全套
  `566 passed`，Alembic head=`0010`。

### M2-PF01 / PR-18P-B（pgvector Dense、中文 FTS、Legacy conformance）

- Tokenizer owner：把既有 `ascii-cjk-unigram-bigram-v1` 提升为 Platform 版本化函数；legacy BM25 改为
  委托该函数，保持 ASCII/编号、中文单字和二元词的原行为。PG lexical document 只接收预分词 lexeme，
  migration `0011` 将 `search_tsv` 改为数据库生成的 `to_tsvector('simple', lexical_document)` + GIN，
  producer 不能写入另一份 tsvector truth。
- PostgreSQL adapter：同一事务固定并验证 corpus/generation/backend fingerprint/dimension/cosine/tokenizer/
  lexical ranker；Dense 用 cosine 距离，支持显式 exact baseline，PG FTS 用参数绑定的 OR websearch query 与
  `ts_rank_cd`，两路只保留各自 source rank/score，不做融合。Knowledge 按 tenant/scope/locale/product，
  Episode 按 tenant/user/entity 过滤，跨 tenant/user/corpus 不互见。
- HNSW：按 generation 创建 partial expression index，固定 dimension、cosine opclass 与 m/ef_construction；
  index ID 由 generation 稳定派生，重试时验证现有 index method/definition/options，漂移 fail closed。
  小语料 exact 与 ANN 在同一 capture 上逐 candidate ID 对比。
- Legacy adapter：`LegacyHybridBackend` 与 PG 使用同一 request/result/status/rank contract；具体
  `ChromaBm25KnowledgeCandidateSource` 分别读取 Chroma raw distance 与 SQLite/Python BM25 source order。
  frozen comparison corpus 缺 tenant/ACL/source revision 时返回 `INVALID_CONTRACT`，backend 故障返回
  `UNAVAILABLE`，两者都不伪装为 `NO_EVIDENCE`。
- 状态语义：generation 不可读/维度/lexical contract/schema 漂移为 `INVALID_CONTRACT`，backend fingerprint
  漂移为 `CONFLICT`，真实空结果为 `NO_EVIDENCE`，连接/执行故障为 `UNAVAILABLE`。
- 验证：tokenizer golden、Legacy/PG conformance、PG generated tsvector/GIN、HNSW 重入、exact-vs-ANN、
  tenant/scope/user/entity/corpus isolation、所有 fail-closed status；真实 PostgreSQL/pgvector 全套
  `575 passed`，Alembic head=`0011`。没有 online consumer 或 read pointer 切换。

### M2-T01A（Agent-owned Intent / Domain / Instance policy）

- 单一策略 owner：新增 immutable `AgentRoutingPolicyRegistry.v1()`，分别固定 IntentFusion、DomainRouting、
  InstanceSelection version/fingerprint；registry 没有运行时 active-pointer mutation API，candidate surface
  不能靠在线 feedback 改 active policy。
- Intent migration：`IntentRecognizer` 不再直接选择模块常量，构造时消费 registry 的 V1 `ngram`
  `.70/.20/.10`、`disabled` `.85/.15` 与 accept `.50`；classifier fingerprint 同时绑定 policy version 和
  当前分支权重。两条分支分别验证。
- Domain migration：原 `_INTENT_ROUTING/_route()` 已删除；唯一 `DomainRoutingPolicy` 输出 typed
  `DomainDecision`，保留 M0 冻结的 prior、intent、affirmed keyword、entity 分项、supporting/clarification
  threshold，并对 CRITICAL/明确人工请求记录 hard-rule reason。相同输入、可用 owner snapshot 和 policy
  可重放完整 components/ordered owners/input fingerprint。
- Instance split：Owner 选择与同 Owner 实例选择成为两个 policy surface。Instance policy 固定 success/
  quality/latency `.35/.45/.20`、EWMA `.25`、prior `.50`、10 样本收缩、latency normalization 和 Monitor
  penalty cap；单实例返回 `NOT_APPLICABLE(OWNER_POOL_SINGLETON)`，多实例按冻结 health snapshot 分数及
  instance ID tie-break 确定性选择。
- Trace/pinning：每个 request-local `RoutingPolicyTrace` 绑定 registry/三策略 version+fingerprint、Intent
  classifier/input/source scores、Domain component decision 与每次 Instance snapshot/status；ChatApplication
  结果证据保存该 trace。没有使用 orchestrator 级可变“last trace”承载并发请求事实。
- 验证：策略独立变更、两条 Intent V1 分支、Domain 分项/hard rule/多领域、single/multi instance replay、
  registry frozen、旧第二 producer 负向检查、Application trace 集成；全套 `582 passed`。

### M2-T01（RouteDecision / RouterInvocationPolicy，flag-off build）

- Canonical route：新增闭合 `RouteMode` 八态 `DIRECT/KNOWLEDGE_QA/AGENT_TASK/MIXED/MULTI_DOMAIN/
  CLARIFY/HANDOFF/OUT_OF_SCOPE`，输出 typed required authorities、risk、missing inputs、owner IDs、reason
  codes、policy/input fingerprint；旧 `execute/clarify/out_of_scope` 只由只读 compatibility property 投影。
- Invocation order：`RouterInvocationPolicy` 只规范化 PendingSignal/Continuation/request shape 和 T01A typed
  ports，不包含第二个 LLM。每次固定记录 IntentFusion/DomainRouting/InstanceSelection 三个 component 的
  `INVOKED/SKIPPED/NOT_APPLICABLE`、reason、input fingerprint 与 policy version，缺项或顺序漂移直接拒绝。
- 短路径：Greeting/Thanks/FAQ/MissingInput/ExplicitHandoff/OOS/UnsupportedOperation 在 hard shape 已确定时
  三个昂贵组件全部 skip；FAQ 只声明 Knowledge，个人实时状态必须声明 DomainTool 并调用 Domain port，
  Mixed 同时声明 Knowledge+DomainTool，明确人工不会被 RAG/情绪信号覆盖。
- Continuation：PendingSignal reply 与 `CONTINUE` 复用 pinned child/route，三个组件全部 skip；`SWITCH`/
  `AMBIGUOUS` 调用一次强 Intent；`EXPAND` 只有新增 requirement 歧义时调用 Intent，并只为新增 Worker
  调 Domain/Instance。无 Intent port 时澄清、无 Domain owner 时 Handoff，均 typed fail closed。
- Instance：Worker route 必须消费 T01A DomainDecision；所有 selected owner pool size=1 时 Instance 返回
  `NOT_APPLICABLE`，只有 pool>1 才调用 typed Instance port。Emotion 仅写 auxiliary signal，不改变 mode、
  authority 或 risk。
- 激活边界：本卡提供稳定 `RouteDecisionPort` 与 property/forbidden-call fixture，未替换当前线上 legacy
  Planner/同步 `/chat`；M1 production gate 未闭合前保持 flag-off，不把 build 描述为 dark shadow。
- 验证：所有确定性 shape、FAQ/个人状态/Mixed/Security/Handoff、CONTINUE/SWITCH、single/multi pool、
  missing port fail-closed、legacy projection 与 emotion auxiliary；全套 `602 passed`。

### M2-T02（FactRequirement / AuthorityPolicyRegistry）

- 单一策略 owner：Application 新增 immutable `AuthorityPolicyRegistry.v1()`，登记 Knowledge、Order、Refund、
  Account、Security、Support、Memory、Commitment 权威，固定 required fields、freshness、read/write effect、
  allowed/forbidden tools、receipt schema、owner approval 与 registry fingerprint；未知 requirement/version
  fail closed。
- 最低要求：Registry 从 `RouteDecision.required_authorities + intent/action` 产生最低 requirement；Planner
  proposal 只能并集追加，不能删除 minimum。非 DIRECT/OOS/CLARIFY/HANDOFF 的空集合拒绝；缺少
  Account/Commitment API 返回 typed `UnsupportedAuthority`，Knowledge 不能代替个人动态状态。
- 工具合同：`Tool` manifest 补齐 authority、manifest/output/receipt schema version、preconditions、approval、
  idempotency、timeout、retry、typed outcomes 与 output fields；registry fingerprint 绑定这些安全字段。
  API 在全部生产工具注册后统一执行 startup validation，测试 Fake manager 同步迁移只读注册快照端口。
- Owner API：CustomerOperations 新增 user-scoped `get_refund_status` 与 `refund_status` 只读工具；不存在或
  跨用户均 typed not-found。工单读写、订单/退款/安全、Knowledge、Memory 的现有 manifest 全部迁移；
  启动门禁实际捕获并修复 `support_ticket_get.updated_at` 清单遗漏。
- 输出验收：只有 registry 允许的注册工具及结构化字段可满足 requirement；普通生成文本、错误工具、
  缺字段、缺 `observed_at`、未来或超 freshness 观察均返回确定性失败 reason。EvidenceReceipt 的 canonical
  构造与 schema 签发仍归下一节点 M2-T03，本节点不提前建立第二套 receipt authority。
- 激活边界：生产工具 manifest startup gate 已启用；FactRequirement 尚未替换线上 legacy Planner，等待
  T03 receipt 与 T04 CoverageGate 后按既定 M2 gate 接入。
- 验证：聚焦 `26 passed`；真实 PostgreSQL/pgvector 全套 `609 passed in 17.61s`；ruff 与
  `git diff --check` passed。

### M2-T03（Canonical EvidenceReceipt）

- 证据代数：新增闭合 `EvidenceKind` 七类 `KNOWLEDGE/BUSINESS_TOOL/ACTION_RECEIPT/MEMORY_EVENT/
  COMMITMENT/MEDIA_OBSERVATION/HUMAN_ASSERTION`；Knowledge/Business/Action 分别复用闭合
  `RetrievalStatus/ToolCallStatus/ToolEffectStatus`，Memory/Commitment/Media/Human 各有独立 typed status，
  Coverage 结果固定为 `RequirementStatus` 六态，不使用跨 producer 自由字符串。
- 唯一签发边界：AuthorityPolicyRegistry v1 登记 Knowledge、Business Tool、Action、Memory 四个现有 adapter
  的固定 ID/version、requirement scope 与 producer output-schema version；Issuer 只能返回 Registry 创建的
  `RegisteredEvidenceAdapter`。未实现的 Commitment/Media/Human 保留闭合 schema，但没有注册 producer，
  因而不能签发 receipt。
- 最小 receipt：canonical `EvidenceReceipt` 仅保存稳定 locator、required field names、kind/status、authority、
  producer/adapter/policy/schema version、observed/expires time、Owner payload hash 与 receipt hash；不复制原文、
  私有返回值或默认 `legacy/public` provenance。Receipt ID 由 canonical body hash 确定性派生。
- 可复验性：恢复 wire receipt 时重新校验 Registry 授权、producer version、evidence kind、required fields、
  authority、policy fingerprint、receipt schema/ID/hash；Resolver 回到原 Owner 后复验 payload hash、locator
  身份/version/checksum/receipt binding 与 freshness。COMMITTED/OK/SUCCESS 等可满足，合法但非成功状态保持
  typed MISSING/CONFLICTING/INVALID，不被误升为 evidence success。
- 现有对象边界：`EvidencePack` 继续是 Knowledge Owner 的上下文对象，`ToolResult` 继续是工具执行结果；
  二者都不直接成为跨域权威事实，避免复制敏感内容或形成第二 authority。
- 激活边界：canonical schema/issuer/verifier 已完成，尚未接入线上 legacy Planner/Verifier；等待 T04
  CoverageGate 与 T04A active SourceRevision 后成为消费主链。
- 验证：7 kind schema property、kind/status/locator 错配、未注册 adapter/producer/version、诊断文本、缺
  provenance、wire 篡改、Owner 内容漂移、checksum/receipt 绑定、freshness 与非 committed action；聚焦
  `22 passed`，真实 PostgreSQL/pgvector 全套 `624 passed in 17.73s`，ruff/diff checks passed。

### M2-T04（Requirement CoverageGate / VerificationProfile，flag-off build）

- Authority 修复：新增 Application-owned `RequirementCoverageGate`，输入只有本轮 `FactRequirement`、canonical
  receipt、Owner resolver 与 claim binding；task/Agent `SUCCESS` 不再是 requirement 完成依据。现有线上
  `services.result_synthesizer.CoverageGate` 暂保留为 task compatibility projection，等待 T04A 后迁移消费面。
- 闭合报告：逐 requirement 输出 `SATISFIED/MISSING/CONFLICTING/STALE/UNSUPPORTED/INVALID_EVIDENCE`，
  同时显式记录 duplicate/unexpected/invalid-wire refs；未知或 checksum/schema/policy/producer 错误 receipt
  归 `INVALID_EVIDENCE`，不降格成普通 missing。空 requirement 不能在该 Gate 独立成功。
- 证据规则：动态业务事实必须有可解引用且字段/locator/freshness 有效的 Tool receipt；写 requirement 还必须
  是 COMMITTED 且 final claim 显式绑定该 receipt。多份相异 business/action authoritative values 归冲突；
  Knowledge 除 receipt 自校验外强制调用 active revision validator，T04A port 缺失时 fail closed。
- VerificationProfile：版本化六类 `RULE_ONLY/GROUNDED_KNOWLEDGE/AUTHORITATIVE_RECEIPT/MIXED_AUTHORITY/
  MULTI_TASK/HANDOFF_CONTRACT`，每类冻结 deterministic gates 与 semantic verifier policy。DIRECT/OOS/
  CLARIFY/HANDOFF 及确定性 action receipt 禁止通用 LLM；Knowledge/Mixed/Multi/read receipt 只有存在语义
  歧义才调用一次，无歧义记录 AVOIDED，需要但 verifier 不可用记录 UNAVAILABLE。
- 激活/验证边界：本节点完成 build 与非 Knowledge/缺-validator 的 fail-closed 验证；按任务卡 Verification
  prerequisite，不在 T04A active SourceRevision/backfill 完成前标记 VERIFIED，也不提前替换线上发布门禁。
- 验证：纯文本不能满足动态事实、malformed receipt、mixed partial、duplicate/unexpected、unsupported、
  action claim binding、Knowledge validator prerequisite、RouteMode×Profile 和 invoked/avoided/unavailable；
  聚焦 `18 passed`，真实 PostgreSQL/pgvector 全套 `642 passed in 17.93s`，ruff/diff checks passed。

### M2-T04A（SourceRevision v0 / active manifest）

- Authority owner：新增 immutable `SourceRevision`（tenant/source/revision/checksum/content/effective interval）与
  `KnowledgeSourceManifest`；revision 由内容 checksum 确定性派生，`legacy-*`、空 provenance、checksum 漂移、
  隐式 scope/locale 全部拒绝。Chunk 只允许是原 revision 的精确 span projection。
- 单一 active transition：manifest/entries 一对一绑定已有 retrieval generation；不创建第二 active pointer。
  继续由 `retrieval_generation_pointers` 原子切换，Knowledge validator join 同一 pointer，因此任一事务快照只见
  完整旧或完整新 generation。切换后旧 receipt 为 STALE，未知 revision/checksum/span 为 INVALID_EVIDENCE。
- PostgreSQL：migration `0012` 增加 raw source revisions、generation manifests/entries、immutability 和 BUILDING
  generation write fence；`PostgresKnowledgeSourceRepository` 可重入 backfill sources/manifest/chunks，验证
  generation manifest hash、embedding dimension、source projection 与完整计数，并提供 locator dereference/
  active validation。DataLocationRegistry v3 新增 Knowledge-owned `location:knowledge-source:v1`，共 32 locations/
  7 write-approved，fingerprint=`14bda1d84d888883c2d4f42bfbc0b88c04860441c0d243b2b01e3a9cdfc98ade`。
- Legacy conversion：`build_v0_backfill` 只接收完整 raw source export 和显式 tenant/backend/generation/scope/
  locale/reviewer ref，生成 content-addressed revisions、full-source chunks 与 provenance；Chroma/BM25 adapter 不再
  补 provenance，SourceDocument mapping 不再默认 public。KnowledgeBase contract 升至 source v2/index v3，
  chunk metadata/output 增加 canonical source revision/checksum，旧索引必须显式 reimport。
- T04 闭环：KnowledgeLocator 新增 tenant/backend/scope/locale/product，消除跨租户/过滤范围歧义；真实 PG
  repository 同时作为 resolver 与 active-revision validator，T04 Knowledge coverage 集成测试通过。
- Evaluation honesty：冻结 `data/eval/knowledge-source-v0` 的 synthetic sources/dev 与 author-created heldout
  candidate，全部 checksum 绑定；manifest 明确 `REVIEW_REQUIRED/PROVISIONAL_NOT_GOLD`。该候选由实现上下文
  创建，不能冒充 fresh independent human-reviewed heldout，因此 T04A 保持 implemented、未 VERIFIED；独立
  reviewer 替换或封存未见集合后才能关闭验证门槛。
- 验证：backfill 重入、source immutability/dereference、old/new pointer、active CoverageGate、legacy/provenance/
  scope fail-closed、artifact checksum；Alembic head=`0012`，真实 PostgreSQL/pgvector 全套
  `650 passed in 18.57s`。涉及原有压缩 fixture 文件只做显式 scope 消费者迁移，未将其历史风格问题纳入本卡。

### M2-PF01 / PR-18P-C（canonical outbox projection / deletion fence）

- Owner 边界：Knowledge backfill 只在同一事务固化 immutable `SourceRevision`、manifest/entries、精确
  `knowledge_source_chunk_specs` 并发出只含 canonical refs/fingerprint 的 durable outbox；不再直接写
  `knowledge_chunk_search`。Retrieval Platform projector 只解析 Owner refs、验证 BUILDING generation 和
  完整 fingerprint 后生成可删除、可重建搜索 projection；ServiceEpisode 只定义 opaque owner resolver port，
  不在 M4 前复制 episode 事实或 verified-outcome 语义。
- 可重放合同：确定性 event identity、immutable canonical fields、闭合
  `PENDING→PROCESSING→APPLIED/REJECTED` 状态与 typed result code；重复执行已完成 event 返回
  `ALREADY_APPLIED`，candidate set 或 canonical fingerprint 漂移为 `CANONICAL_SOURCE_DRIFT`，关闭或未知
  generation fail closed。多 scope/locale/product manifest 按 manifest hash 单独投影，不跨 manifest 混写。
- 写边界：DataLocationRegistry v4 注册 `location:retrieval-projection-outbox:v1`，共 33 locations/
  8 write-approved，fingerprint=`2aae62ba01ac4195ae50a7dbd7b619f433d5a800b3fce8698a1e3a9a3f49f502`；
  runtime retrieval role 已撤销两个搜索 projection 表的写权限，正常 PG retrieval row 只能由平台投影边界写入。
- 删除/迟到写：Episode enqueue 与 projector 都在事务内读取 conversation tombstone/deletion epoch；conversation
  首次 tombstone 的数据库 trigger 同事务删除已有 Episode projection 并拒绝 pending/processing event。删除后
  enqueue、直接搜索投影和 rebuild 都受 fence 阻止；dark-shadow 仅写独立 BUILDING generation，不修改唯一
  active pointer。完整备份恢复故障证明仍按任务卡由 M4-T08 收口。
- PostgreSQL：migrations `0013`–`0015` 增加 source chunk specs、canonical outbox/receipt、event owner/fence
  guard、projection delete adapter 与 runtime read-only boundary；Alembic head=`0015`。
- 验证：backfill-before-projector 零搜索行、exact replay、identity immutability、canonical drift、closed generation、
  tenant/scope filter、shadow pointer、missing Episode resolver、tombstone purge 与 late enqueue；聚焦 `36 passed`，
  真实 PostgreSQL/pgvector 全套 `655 passed in 21.40s`，ruff/diff checks passed。节点标记 IMPLEMENTED；生产
  Recall/latency/rebuild RTO/OLTP 影响证据仍未满足，不声明 VERIFIED。

### M2-T05-A1（KnowledgeRetriever / cache port）

- 唯一 Owner：新增 Knowledge-owned `KnowledgeRetriever`，统一拥有 Raw/Standalone fallback、legacy
  Dense/BM25 policy、candidate/final/packing budget、完整 rerank permutation fallback、EvidencePack 与闭合
  `RetrievalStatus`；backend 只提供候选，不拥有 fusion/rerank/packing。候选 manifest/source revision/checksum/
  scope 不完整时返回 `INVALID_CONTRACT`，重复 stable ID 返回 `CONFLICT`，backend 异常为 `UNAVAILABLE`，
  非 OK 结果绝不携带 partial evidence 或诊断文本。
- Profile：冻结 `LEGACY_BM25_V1` comparison 数值 Raw/Standalone `.25/.75`、Dense/BM25 `.25/.75`、
  RRF `k=10`、`20→5`、pack `2600`；所有权重、budget、backend/lexical/transformer/embedding/reranker/packer
  version 都进入 canonical policy fingerprint。rewrite 失败将 Raw 质量恢复为 `1.0`；非法 rerank 结果整体保留
  first-stage order，不虚构 rerank weight。
- Evidence：现有 `SourceReference` 补齐 canonical `source_revision`，EvidencePack trace 记录 query variants、
  source ranks、manifest/generation/backend/policy 与两类 fallback。
- Cache 边界：定义 Knowledge `RetrievalCachePort`，Infrastructure 提供 Redis exact-byte adapter；Redis 错误只
  旁路为 miss，不改变 retrieval status。固定 `langchain-classic==1.0.8`，唯一 Infrastructure adapter 使用官方
  `CacheBackedEmbeddings.from_bytes_store`、显式 query store 与 `sha256` key encoder；业务代码不引用易变 import。
- 验证：legacy variants/trace/pack、rewrite/rerank fallback、六态关键分支无 partial evidence、每个 owned 参数
  fingerprint 变化、真实 LangChain query/document cache compatibility 与 Redis outage bypass；聚焦 `39 passed`，
  全套 `664 passed in 19.15s`，ruff/diff checks passed。A1 完成；A2 exact layered key/invalidation/single-flight
  与 A3 consumer migration 尚未完成，因此 M2-T05 保持 in_progress。

### M2-T05-A2（exact layered key / invalidation / equivalence）

- 分层 key：新增 `RetrievalCacheKeyBuilder`，transform 只含当前 query/requirement/conversation range 与
  transformer version；embedding 加 subject deletion epoch/text/model/normalizer；candidate 在生成前加入
  tenant/user scope、authorization-set、ACL policy、epoch、locale/product/filter variants、manifest、backend/
  generation 与 lexical/dense policy；rerank 绑定 canonical candidate-set hash 和 model/policy；EvidencePack 再
  加 requirement、source revision/checksum、packer/budget 与全部上游 fingerprint。未来 stage 输出不反塞上游 key。
- Correctness invalidation：candidate cache hit 必须通过 Owner validator 复验 active manifest/ACL；pack hit 还要
  复验 source revision/freshness/coverage。validator 缺失或失败时只 miss 并回源；已证明 stale candidate cache
  不会遮蔽 backend `UNAVAILABLE`。空候选不缓存，损坏 JSON 删除后重算；TTL+deterministic jitter 只管理资源。
- Subject/source embedding scope：query namespace 强制 tenant/user/deletion epoch/model；source 默认同 subject，
  只有显式 approved shared corpus 才能移除 user scope，且仍保留 tenant/corpus/model。LangChain adapter 同时启用
  query/document cache 与 SHA-256 encoder。
- 等价/击穿：`force_recompute` 绕过所有读 cache，full recompute 与 transform/candidate/rerank/pack 全命中的
  stable evidence IDs 相同；并发 candidate miss 经 Redis `SET NX` lease、bounded wait 与 compare-token release
  只执行一次 Owner source 调用，lease/cache 故障仍可重算且不改变 Retriever 结果合同。
- 验证：16 个 A1/A2 专项测试，相关 retrieval/context 聚焦 `52 passed`；全套
  `671 passed in 19.01s`，ruff/diff checks passed。A2 完成；外层 ToolManager cache 删除和三个 consumer
  切换归 A3，因此 M2-T05 仍保持 in_progress。

### M2-T05-B1（consumer migration / outer-cache removal）

- 唯一消费合同：`/search`、pre-Knowledge QA 与 Agent `knowledge_search` 全部调用
  Knowledge-owned `KnowledgeRetriever`；工具只返回版本化 `EvidencePackResult`，Grounded Answer 仍由
  pre-Knowledge 投影层按本轮 Context 生成。
- 身份边界：HTTP 认证入口产生 authorization fingerprint，经 `ChatCommand`/Invocation metadata
  传入 Agent tool；工具端不再用 Agent 名称临时伪造授权身份，缺 tenant/user/auth/
  deletion fence 时按 Retriever 合同 fail closed。
- 旧路移除：删除 `MCPToolManager.search_with_rewrite` 及 KnowledgeBase MCP handler；Knowledge 工具
  `cache_ttl=0` 且 `supports_rerank=False`，不再存在 Retriever 外的二次 cache/rewrite/rerank。导入
  API 直接使用 KnowledgeBase Owner，不再通过 `handler.__self__` 反向定位。
- 证据合同：Authority registry 登记 `knowledge-evidence-pack-result-v1`，只允许 `OK` 且
  EvidencePack item 具有 source ID/revision/checksum/text 时满足 `knowledge.active_source`；其他状态
  或缺 provenance 均 fail closed。
- 验证：直接 Retriever、`/search`、Agent handler 与 pre-Knowledge QA 在同一 query/
  identity/policy 下的 EvidencePack 字节等价，`UNAVAILABLE` 语义一致；聚焦 `46 passed`，
  全套 `672 passed in 19.49s`，ruff（忽略历史 E402）/diff checks passed。consumer slice 完成；
  pre-Exit PG dark-shadow 不可变比较报告尚未生成，因此 M2-T05 不标记 IMPLEMENTED。

### M2-T05-B2（frozen Knowledge PG pre-Exit dark shadow）

- 冻结输入：`evaluation/fixtures/knowledge-pg-shadow-v1/spec.json` 以 SHA-256 绑定 5 条
  canonical public source、7 条 dev + author-created heldout-candidate query、tenant/scope/locale/product filter
  和 `LEGACY_BM25_V1_COMPARISON` 的 `.25/.75` query、`.25/.75` Dense/lexical、RRF `10`、
  `20→5`、pack `2600` 及全部 component version。任一文件或 policy 漂移都在执行前拒绝。
- 真实写链：runner 先经 DataLocationRegistry v4 审批 `knowledge-shadow` 的 dark-shadow write，
  然后使用 immutable SourceRevision/manifest/chunk spec→canonical projection outbox→PG search projection；
  generation 只到 `READY`，未写 active pointer。Legacy Chroma/BM25 始终是唯一 publisher。
- 不可变报告：`governance/evidence/m2-t05/knowledge-pg-pre-exit-v1.report.json`，
  `report_sha256=b6bad25276c1f2906edb099a43cbe1b3de9d3979836d526684da3159f1387d26`；
  同一 runner 连续两次重放得到相同字节/哈希，已有不同报告拒绝覆盖。
- 结果：status agreement=`1.0`、Top-5 source-set Jaccard=`1.0`，但 exact order=`0.0`，
  legacy/PG 均在 4 个 harmful/dynamic-authority case 返回 forbidden Top-5。该差异被保留为
  `COMPARISON_COMPLETE_NOT_CANARY_AUTHORIZATION`，不冒充 fresh heldout/quality/latency Gate；M2-T05C
  必须在 M2 Exit 后独立消费报告，不得因本节点切 PG。
- 验证：frozen drift、deterministic report、immutable write、registry lifecycle `get`、canonical projection
  `APPLIED`、generation `READY`、active pointer count `0`；全套 `675 passed in 19.11s`，ruff/diff
  checks passed。A1/A2/B 三个 delivery artifact 已完成，M2-T05 标记 IMPLEMENTED；canary/GA
  仍由 M2-T05C 与 release profile 阻断。

### M2-T06A-A（versioned TaskFormation / Execution / Synthesis contracts）

- `TaskSpec` 补齐 requirement IDs、permission/interrupt boundary、`may_interrupt`、typed
  `DependencyInput(upstream_task_id, artifact_kind, receipt_schema)`、split reason 和 deterministic
  assembly capability；版本化 TaskGraph 对每条 dependency 强制 typed input，并将 task 原始顺序、
  dependencies 与三项 policy/pinned config 写入 immutable plan fingerprint。
- `TaskFormationPolicy v1` 只合并同 Owner/风险/权限/context/interrupt boundary、无独立依赖、
  read-only 且 ReAct budget 可容纳的 requirements；write/approval、permission、interrupt、dependency
  或不同 Owner 保持独立 Task。
- `MultiAgentExecutionPolicy v1` 将迁移基线正名为 max planned=`4`、max executed=`3`、
  max parallel workers=`3`、request/worker timeout=`20s/15s`、Worker ReAct=`4`；选择精确展开
  `execution_waves()` 并保持 plan 顺序，超过 plan 安全边界返回 typed `PLAN_TOO_LARGE`，不截断。
- `SynthesisInvocationPolicy v1` 闭合 `NONE/DIRECT/DETERMINISTIC/LLM/CONFLICT`；缺 coverage
  不调用 synthesis，单 outcome 直接使用，可模板多 outcome 确定性组装，只有需要跨来源
  语义组织时允许 LLM，authority conflict 始终不调用模型。
- 验证：合并/拆分边界、typed dependency、fingerprint drift、4→3 dependency-closed replay、
  plan overflow 和五种 synthesis 代数；聚焦 `41 passed`，全套 `684 passed in 19.19s`，
  ruff/diff checks passed。执行器迁移尚未完成，M2-T06A 保持 in_progress。

### M2-T06A-B（TaskGraph execution / dependency binding / synthesis migration）

- DAG 触发从 `plan.multi_agent` 修正为 `len(tasks)>1`；`multi_agent` 仍只表示
  `distinct_owner_count>1`，因此同 Owner 确需拆分的 read/write 任务全部执行但不冒充
  multi-Agent fan-out。Planner 新计划统一经 TaskFormationPolicy 生成并 pinned 三项 policy。
- 调度器使用 `MultiAgentExecutionPolicy.select()` 而不再自行截断；同波次保持 plan
  原顺序，safe read 按 `max_parallel_workers` 分批，write/approval 与 `may_interrupt`
  任务在调度 Owner 内串行。超过 plan 边界的执行返回 typed `PLAN_TOO_LARGE`
  诊断且不启动 Worker。
- 每个成功 Worker 产生 typed `TaskArtifact(agent_candidate, agent-candidate-v1, receipt refs)`；
  下游只有在 upstream `SUCCESS` 且 kind/schema 精确匹配 `DependencyInput` 时执行，artifact
  作为结构化 dependency context 进入 scoped Request；缺失/错 schema 为 `BLOCKED_DEPENDENCY`。
- ResultSynthesizer 消费 `SynthesisInvocationPolicy`：required coverage 不完整为 `UNKNOWN`
  fail-closed，单成功直接候选，多个 template-capable outcome 确定性组装，只有完整、
  非模板、无 authority conflict 的多结果调用一次 LLM；冲突返回 `CONFLICT` 并转复核。
- 验证：同 Owner split DAG、valid/wrong dependency schema、parallel cap、interrupt serialization、
  0/1/template/LLM/conflict 实际模型调用次数；聚焦 `53 passed`，全套
  `690 passed in 19.21s`，ruff/diff checks passed。PendingSignal 和 CANCELLED/EXPIRED 终态仍待收口。

### M2-T06A-C（native PendingSignal / terminal outcome closure）

- 编排执行边界改为互斥 `TaskExecution(outcome | pending_signal)`：ReAct
  `waiting_approval` 只产生版本化 `PendingSignal(APPROVAL, signal_id, task_id, version)`，不再写
  `AWAITING_APPROVAL` 伪终态；旧 `awaiting_approval/pending_approval_call_ids` 仅由公开兼容投影读取。
- CoverageGate 独立接收 terminal outcomes 与 native signals；等待 task 投影
  `AWAITING_SIGNAL(kind)`、不进入 failed outcome，同时保持 required unresolved 并阻止
  Synthesizer/发布。公开 response 增加 `pending_signals` 诊断合同。
- effect-aware 串行任务在首个 signal 后停止本 wave/后续 wave；Planner 漏标导致并发产生第二个
  interrupt 时，稳定保留 plan 顺序第一项，第二项写 typed
  `ERROR/UNSUPPORTED_CONCURRENT_INTERRUPT`，v1 不自建多 interrupt barrier。
- terminal outcome 闭合 `CANCELLED/EXPIRED`，Worker 通过显式 terminal producer 字段传递；未知值、
  `AWAITING_APPROVAL` 冒充终态或矛盾 success 均 fail closed 为 `ERROR/INVALID_TERMINAL_STATUS`。
  `PriorOutcomeBinding` 作为 Request 只读 delta-plan 输入端口保留，不复制为本轮 outcome。
- 验证：native signal 不调用 Synthesizer、单 signal claim、串行停止、漏标并发 fail-closed、
  cancel/expire/unknown terminal、prior binding 隔离；聚焦 `46 passed`，全套
  `697 passed`，本次文件 ruff/diff checks passed。M2-T06A 标记 IMPLEMENTED；LangGraph native
  checkpointer/resume 的持久化迁移仍属于 M3，不在本节点伪造。

### M2-T06-A（eight-mode execution/publication contract）

- 新增 `RouteExecutionPolicy v1`，将八种 `RouteMode` 闭合为唯一 candidate Owner、expected
  outcome，以及穷尽且互斥的 required/conditional/forbidden component algebra；每条路径均强制
  `TURN_RECORD`，合同 fingerprint 绑定 route input/policy 与 verification gates。
- `KNOWLEDGE_QA` 唯一候选 Owner 为 GroundedAnswerGenerator；`AGENT_TASK` 禁止 pre-route
  grounded generation，仅允许 Agent 内按需 Knowledge/业务工具；`MIXED` 只形成一份
  mixed-authority Agent candidate；`MULTI_DOMAIN` 委托 TaskGraph 并只条件允许 synthesis。
- `DIRECT/OUT_OF_SCOPE/CLARIFY` 禁止 Retriever、Agent、业务工具和 semantic verifier；
  `CLARIFY` 投影 `NEEDS_INPUT`；`HANDOFF` 在 M4-T07C 前只允许 schema-compatible draft，明确
  禁止新 handoff write。
- `VerificationProfileRegistry.contract_for()` 成为执行路径读取 deterministic gates/profile 的公开
  Owner 端口，route executor 不复制 verification 决策。
- 验证：八 mode 组件集合完备/互斥、逐路径 forbidden calls、单 candidate Owner、handoff release
  fence 与 fingerprint replay；聚焦 `55 passed`、全套 `714 passed in 19.36s`，ruff checks passed。主链路仍待 T06-B 迁移，
  因此 M2-T06 保持 in_progress。

### M2-T06-B1（typed RoutePathExecutor / single-candidate enforcement）

- 新增 `RoutePathExecutor v1`，Application 只消费已归一化 `RouteExecutionContract` 并在
  rule / Knowledge / Agent / mixed / TaskGraph / handoff-draft 端口间择一；不重新解释 intent、
  authority 或 TaskGraph 内部语义。
- 每条执行只接受一个带 `CandidateOwner` 的 `RouteCandidate`；Owner 不匹配、重复 component
  receipt、禁止调用或遗漏必需组件均 typed fail closed，不能靠 tool audit 在结束后猜测候选来源。
- KnowledgeQA 固定 `retrieve → grounded generate`；AgentTask 禁止前置 RAG，业务状态必须由 Agent
  回传 `BUSINESS_TOOL` receipt；Mixed 固定 `retrieve evidence → one mixed-authority Agent candidate`；
  MultiDomain 仅委托 TaskGraph，conditional synthesis 是否实际调用仍由 T06A policy/receipt 声明。
- deterministic profile gates 总是先运行；仅其明确返回 semantic ambiguity 且 profile 允许时，才调用
  semantic verifier 一次。无论 publishable 与否都执行 `TURN_RECORD`；新 Handoff path 仍只有 draft，
  `HANDOFF_WRITE` 保持 forbidden。
- 验证：八 RouteMode E2E call trace、required/forbidden receipts、单 candidate、mixed 无双答案、
  Agent 无 pre-route grounded answer、semantic verifier 0/1 次；聚焦 `44 passed`、全套
  `723 passed in 19.25s`，ruff checks passed。现有 ChatApplication adapter 尚未切换，M2-T06
  保持 in_progress。

### M2-T06-B2a（DomainDecision selected-owner authority）

- 修复 canonical RouteDecision 接入前发现的 Owner 代数缺口：`ordered_owners` 只是完整排名，新增
  `selected_owners` 作为 DomainRoutingPolicy 按 frozen supporting threshold 产生的实际选择事实；
  hard route 同样显式产生单一 selected Owner。
- RouterInvocationPolicy 只消费 `selected_owners`，不再把 0 分或阈值以下候选误投影为实际 Worker；
  RoutingPolicyTrace 同时记录 ranked/selected，两者不再混义。旧手工 DomainDecision 无新字段时仅
  保留兼容 fallback。
- 验证：单域请求保持四候选可诊断排名但只选择 Billing；Router owner IDs 不再包含未选 General/
  Technical；聚焦 `62 passed`、全套 `725 passed in 19.38s`，ruff checks passed。

### M2-T06-B2b（Agent RouterPlanner canonical RouteDecision producer）

- AgentOrchestrator 新增 `decide_route(Request, RequestShape)`：复用本 invocation 已有 Intent，按需调用
  effective DomainRoutingPolicy/InstanceSelectionPolicy，再由 RouterInvocationPolicy 规范化唯一
  RouteDecision；Knowledge 等无 Worker 路径不会触发 Domain/Instance。
- `Request.domain_decision` 成为 request-scoped 只读缓存；RouteDecision 与后续 TaskPlan 在 input/policy
  fingerprint 相同时复用同一选择事实与 Trace，不再二次筛 Owner。Bundle 的 supporting threshold
  先进入 effective DomainRoutingPolicy，因此变化会产生新 policy fingerprint，而非 Planner 旁路阈值。
- TaskPlanner 改为直接消费 `DomainDecision.selected_owners`；排名、选择、TaskFormation 形成单向数据流。
- 验证：Knowledge 跳过昂贵路由；Refund business-state 只选 Billing；RouteDecision owner IDs 与 TaskPlan
  owners 一致且 Trace 只有一份 DomainDecision；聚焦 `90 passed`、全套 `727 passed in 19.30s`，
  ruff/diff checks passed。下一步仍需 hard-rule `RequestShape` producer 与 ChatApplication adapter。

### M2-T06-B2c（versioned RequestShapePolicy）

- 新增 Agent-owned `RequestShapePolicy v1`，以已有 canonical Intent、confidence、urgency、entities
  和肯定/否定 message evidence 产出 immutable shape/authority/risk/reason/fingerprint；不新增模型调用。
- 闭合 greeting/thanks、FAQ、personal business state、write action、bounded Agent assistance、
  mixed policy+state、multi-domain、security、clarify、handoff 与 out-of-scope；新增
  `ACTION_REQUEST` 和 `AGENT_ASSISTANCE`，避免把写动作或技术排障误投影为静态 Knowledge final。
- AgentOrchestrator `classify_request_shape → decide_route` 保留 shape 产生的 authority/risk；Mixed
  实测保持 `Knowledge + DomainTool` 且只选择 Billing。低置信 OTHER 只澄清，高置信 OTHER 才越域；
  否定的第二领域不会触发 MultiDomain。
- 验证：12 类 shape fixture、authority/risk、否定 evidence、shape→RouteDecision；聚焦 `79 passed`、
  全套 `742 passed in 19.53s`，ruff/diff checks passed。下一步迁移 ChatApplication adapter。

### M2-T06-B3（gated ChatApplication route adapter）

- ChatApplication 在 canonical Intent 后构造 immutable `RoutePathInvocation`：包含 shape、route、
  minimum FactRequirements、VerificationProfile 与 RouteExecutionContract；Application 只组合 Owner
  输出，不重新解释 route/authority。
- 服务端 `route_execution_mode` 仅支持 `legacy/dark_shadow/evaluation`：legacy 不触发新路径；
  evaluation 同步执行 adapter；dark shadow 后台执行且不能发布；任何 `active`/未知值在 M1/M2/
  CORE_TEXT_GA release action 前 fail closed，客户端 ChatCommand 没有开关字段。
- 新 adapter 缺失时显式失败，不静默回 legacy 冒充 shadow；Trace stage 保存 mode/shape/contract
  fingerprint，legacy response/delivery publisher 保持不变。
- 验证：Knowledge route invocation 绑定 `knowledge.active_source` 与 grounded profile；evaluation
  callback 恰好一次；pre-release active 被拒；聚焦 `38 passed`、全套 `744 passed in 18.23s`，
  ruff checks passed。下一步由 Agent/ReAct 原生返回 tool/evidence receipts 后接真实 shadow executor。

## 下一步

1. 实施 M2-T06：接通八种 route-specific execution/publishing paths，并消费已完成的
   TaskGraph/SynthesisInvocationPolicy 合同。
2. M2-T05C 受 M2 Exit + `POSTGRES_RETRIEVAL_GA` candidate manifest 阻断，当前不执行 canary。
3. T04/T04A live activation 仍受 M2 gate 与独立 review 约束。
