# 客服 Agent 目标架构实施跟踪

- 分支：`feat/customer-service-target-architecture`
- 权威需求：
  - `docs/customer-service-agent-target-architecture.zh-CN.md`
  - `docs/customer-service-agent-implementation-plan.zh-CN.md`
- 执行原则：按依赖 DAG 推进；每张任务卡独立验证、记录文件、commit 并 push；不把 `IMPLEMENTED` 冒充 `VERIFIED` 或 `READY`。
- 当前阶段：M1

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
| M1 完整会话事实与幂等发布 | in_progress | 按 T00–T05/T03A/T04A 子节点推进 |
| M2 Route/Authority/Evidence/RAG | pending | 按 M2-PF01、T01–T06R 子节点推进 |
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

## 下一步

1. 完成 M1-T03A：ResponseDelivery legacy export/backfill、shadow reconcile 与单 writer binding cutover。
2. 完成 M1-T04/T04A：PendingSignal owner、原子 consume 与 legacy audit/cutover。
3. 保持 production snapshot restore 和 deployed Chroma legacy index 不兼容为显式未满足证据，
   不让后续 migration/cutover 静默越过。
