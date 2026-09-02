# Admission / Thin Execution / ChatOutcome v1 合同

## Authority

- Application 永久拥有 admission、start/resume outbox 与 dispatch binding。
- Runtime 只通过 opaque `ExecutionPointer(runtime_kind, runtime_version, run_id)` 暴露只读事实；
  `workflow_invocations` 不拥有 Agent 内部节点状态。
- final response、handoff、cancel、typed failure、PendingSignal 与 Delivery 各自由领域 owner 产出，
  `ExecutionView` 只做非权威投影。

## AdmissionContract v1

唯一状态和合法 CAS 为：

```text
START_QUEUED -> EXECUTION_BOUND
START_QUEUED -> EXPIRED_BEFORE_START
```

claim/lease/attempt 是 start-outbox 消费元数据，不是 admission 状态。相同 `InvocationKey` 与相同
request fingerprint 返回既有 admission；同键异内容返回 `IDEMPOTENCY_CONFLICT`。Dispatcher 只能以
expected status/version CAS 绑定 opaque execution pointer。lease 过期只释放同一 outbox item，不能创建
第二个 workflow/run identity。

`InvocationRepository`、`AdmissionUnitOfWork`、`StartOutbox`、`Dispatcher` 与 `PendingSignalStore` 的 v1
ports 冻结在 `application/admission_contract.py`；M1-T01/T02 的 PostgreSQL 实现必须实现这些 ports，
不得另建 caller-side 状态或 key。

## ThinExecutionContract v1

投影顺序固定为：互斥 terminal facts → PendingSignal/WAITING → runtime RUNNING → admission。多个 terminal
facts 同时存在时 fail closed。`COMPLETED` 只能由 `FinalPublicationCommitted` 产生；该事实同时绑定
response、outbound event 和 delivery outbox，graph END 迟到不能再次生成回答。

- Principal signal → `NeedsInput`，客户端按 signal schema 回复。
- Media receipt → `Accepted`，客户端只能轮询。
- Reconciliation receipt → `Reconciling`，客户端只能轮询，不能重放写动作。
- final/handoff/cancel/failure 高于 PendingSignal；human reply 不复活 `HANDED_OFF` execution。
- Delivery retry、unknown、failed 或 READ 不改变 invocation terminal status。

M3 compatibility table 对当前 RunStore 的 `RUNNING/WAITING_APPROVAL/COMPLETED/BLOCKED/TOOL_ERROR/
MAX_STEPS/CANCELLED/EXPIRED` 各有唯一投影或 typed terminal outcome；切换 projector reader 不改变
Application admission owner。

## ChatOutcome / HTTP v1

| Outcome | HTTP | 客户端动作 |
|---|---:|---|
| Completed | 200 | 展示同一 response_id |
| Accepted | 202 | 同 request 轮询 |
| NeedsInput | 202 | 对 signal schema 回复 |
| Reconciling | 202 | 仅轮询 |
| HandedOff | 200 | 继续人工服务，不重启 execution |
| Cancelled | 200 | 需要时新建 request |
| Expired | 410 | 新建 request，不复活旧 run |
| Conflict | 409 | 读取既有 invocation 或更换 request ID |
| Rejected | 422 | 修正输入后再提交 |
| Failed(retryable) | 503 | 同 request ID 重试 |
| Failed(non-retryable) | 500 | 不盲重试，携 correlation ID |

外部 Pydantic tagged response models、body、`client_action` 和 `retry_hint` 由 API/OpenAPI 与
`application/public_chat_contract.py` 共同固定；adapter 不自行解释 execution state。
