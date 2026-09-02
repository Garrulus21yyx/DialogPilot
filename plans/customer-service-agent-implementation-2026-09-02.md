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

## 下一步

1. 提交并推送 M1-T00 contracts。
2. 实施 M1-T01：建立 PostgreSQL ConversationTurnStore schema、不可变 turn/event、invocation admission
   与通用 publication/delivery owner。
3. 保持 production snapshot restore 和 deployed Chroma legacy index 不兼容为显式未满足证据，
   不让后续 migration/cutover 静默越过。
