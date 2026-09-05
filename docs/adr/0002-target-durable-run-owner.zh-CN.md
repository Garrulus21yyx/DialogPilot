# ADR-0002：Target 使用 PostgreSQL Durable Run Owner

- 状态：Accepted
- 日期：2026-09-05
- 范围：Target `/chat` 的 Admission、后台执行与运行状态

## 决定

Target 主链复用现有 PostgreSQL Admission、start outbox、invocation ledger 和 lease fencing，
由 `TargetRunCoordinator` 接受请求，由 `TargetRunWorker` 在 HTTP 请求之外执行完整 TurnGraph。
不引入 LangGraph Agent Server，也不保留 compatibility coordinator 作为 Target 的第二执行入口。

LangGraph 继续拥有单次 TurnGraph/WorkPlan 的节点状态与 checkpoint；PostgreSQL Run Owner 拥有
任务是否已入队、由哪个 attempt 执行以及 terminal outcome。Publication 仍是用户可见完成事实，
Tool Receipt 仍是业务副作用事实。

## 数据流与状态

```text
HTTP /chat
  → 原子写入 inbound turn + accepted event + invocation + start outbox
  → 202 Accepted(workflow_run_id, invocation_key)

background worker
  → start outbox binding
  → QUEUED → RUNNING
  → TurnGraph / Tool Receipt / Publication
  → WAITING_INPUT | WAITING_APPROVAL | RECONCILING | terminal
```

每次 claim 增加 attempt。renew、release 和 terminal commit 都必须同时匹配 worker 与 attempt；
lease 过期后的旧执行者不能提交结果。相同 request_id 重试返回原 invocation，不创建第二个 Run。

当前 PostgreSQL 物理 ledger 沿用已提交 migration 中的
`compatibility_execution_outbox` 表名；Target 通过 `execution_runtime_kind='target'` 隔离读取与 claim。
这是存储命名债务，不是并行 compatibility runtime。待工作区中尚未合入的 migration 链稳定后，
只做一次物理 rename migration，不改变应用合同。

## 为什么不选 Agent Server

现有仓库已经具备事务 Admission、PostgreSQL outbox、lease epoch、Invocation 查询、Publication
和业务 Receipt。Agent Server 会新增部署与运行身份，并要求迁移这些现有权威边界；当前没有证据
表明它能以更小复杂度提升正确性。框架仍用于它擅长的图执行、checkpoint 与 interrupt。

## 验收不变量

1. HTTP 或 SSE 断开不取消 Run。
2. 同一 invocation 最多存在一个 durable Run identity。
3. 同一时刻最多一个有效 lease attempt 可以提交 terminal。
4. retryable failure 只重新排队原 Run；不会创建新 invocation。
5. 已存在 Publication 时恢复只读取，不重新执行 Tool 或重新生成回复。
6. compatibility 与 target start 请求由 runtime kind 分开领取。

## 非目标

- 不在本阶段实现 SSE replay；由 M7 基于 conversation event cursor 完成。
- 不让 checkpoint 替代 Tool Receipt、Flow CAS 或 Publication。
- 不把低风险、只读、写操作统一改造成同一种 Agent 循环。
