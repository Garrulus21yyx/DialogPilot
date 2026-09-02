# M1-T02C durable compatibility execution build evidence

日期：2026-09-02。状态：`IMPLEMENTED / NOT YET COMPOSED ONLINE`。

## Owner 与正向合同

M1 admission 继续由 `workflow_invocations + workflow_start_outbox` 拥有；新增
`compatibility_execution_outbox` 只拥有整次 compatibility invocation 的消费 lease/attempt，不复制
Agent 节点或业务 lifecycle。`PostgresCompatibilityRunBinder` 先以 invocation/workflow run 稳定身份创建
唯一 durable work item，再允许既有 dispatcher 把 admission CAS 到 `EXECUTION_BOUND` 并 ACK start outbox。
因此在 work item、CAS、start ACK 任一边界崩溃后都只能恢复同一 run。

worker claim 使用 `claimed_by + attempt` epoch；renew 不能复活过期 lease，同名 stale worker 也不能 ACK。
claim 同时验证当前 conversation deletion epoch。retryable failure 释放同一 job；非重试 terminal 在一个
PostgreSQL 事务内 ACK job 并写 invocation `terminal_ref`。`COMPLETED` 的最终权威仍是 canonical final
publication，本 outbox 不拥有第二套完成状态。

## 验证与边界

PostgreSQL 测试覆盖 binder 幂等、四 worker 竞争只有一个 claim、release/reclaim 后 stale epoch renew/ACK
被拒、terminal ACK 与 invocation ref 原子一致、删除围栏、worker publication guard、terminal replay 和
retryable failure 的 attempt 单调性。`20260902_0017` 已进入 forward-only schema registry。

验证记录：

- 定向 PostgreSQL、admission、schema/claim registry：`28 passed in 12.04s`；
- 全量回归：`873 passed in 63.80s`；
- 变更文件 Ruff：`All checks passed!`；
- capability claim validator 与 registry hash 校验由上述定向测试覆盖并通过。

本节点尚未把 worker 组合进 `ChatApplication`/lifespan，也尚未把当前 direct Memory write 迁到 event
projection；因此它不修订 M1 Exit 的 `NOT_READY` 结论。后续 M1-T02D 必须先从 canonical publication
恢复 Completed、在最终发布前检查当前 claim，再启用在线 admission；M1-T04B 必须让 post-publication
Memory/summary/fact side effects 由 durable event outbox 恢复。
