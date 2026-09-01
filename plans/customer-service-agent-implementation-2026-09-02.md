# 客服 Agent 目标架构实施跟踪

- 分支：`feat/customer-service-target-architecture`
- 权威需求：
  - `docs/customer-service-agent-target-architecture.zh-CN.md`
  - `docs/customer-service-agent-implementation-plan.zh-CN.md`
- 执行原则：按依赖 DAG 推进；每张任务卡独立验证、记录文件、commit 并 push；不把 `IMPLEMENTED` 冒充 `VERIFIED` 或 `READY`。
- 当前阶段：M0

## 状态

| 节点 | 状态 | 验证 / 产物 |
|---|---|---|
| Bootstrap：冻结需求文档与执行跟踪 | done | commit `c2beceb`，已 push |
| M0-T01 应用服务边界 | done | typed `ChatOutcome`、薄 `/chat`；392 tests passed |
| M0-T02 稳定身份和值对象 | done | stable IDs/keys；Trace/Tool/Ticket/Delivery/Memory propagation |
| M0-T03 生产主链 Eval Runner | done | full execution 共用 `ChatApplication`；typed stage/owner evidence |
| M0-T04 当前行为基线 | in_progress | 冻结 baseline artifacts/report |
| M0-T05 Gate Manifest Foundation | pending | 版本化 manifest schema/validator/report |
| M1 完整会话事实与幂等发布 | pending | 按 M1-PF01、T00–T05/T03A/T04A 子节点推进 |
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

### M0-T04（in progress）

- 已实现可重放 `BehaviorBaseline` schema、版本漂移 fail-closed 校验、逐 route 运行记录聚合、
  六类决策策略及独立 fingerprint；schema 明确禁止 production accuracy 汇总。
- 已实现真实 `ChatApplication` characterization capture 与 freeze CLI；capture 使用临时
  Ticket/Delivery/BadCase/Operation/Run 数据库、独立 baseline 用户/会话 identity，并输出实际
  Knowledge index manifest 与逐 Trace 记录。
- 当前验证：全套 `419 passed`，相关 ruff 与 `git diff --check` 通过。
- 待完成：在本代码 commit 上运行真实 capture，归档 cases/records/RAG manifest/final baseline，
  然后验证 replay 与 Bundle/Index 漂移拒绝。

## 下一步

1. 提交并推送 M0-T03。
2. 实施 M0-T04：冻结当前真实主链行为、数据/代码/Bundle 版本与 route 分层基线。
3. 生成机器可读 baseline artifact，并验证候选运行不会静默覆盖 Active baseline。
