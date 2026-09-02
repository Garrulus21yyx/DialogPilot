# M1-T02D online durable composition build evidence

日期：2026-09-02。状态：`IMPLEMENTED / FLAG OFF PENDING M1-T04B`。

## Owner 与正向合同

`CompatibilityChatCoordinator` 是 `/chat` compatibility 路径的 Application composition owner。它在任何
Memory/RAG/LLM/Tool 工作前生成稳定 invocation identity、解析非模型 rollout assignment，并把完整 primary/
shadow execution refs 与 authorization fingerprint 固定到 admission。PostgreSQL 单事务继续拥有 inbound、
accepted event、invocation 与 start outbox；dispatcher 只绑定 T02C 的唯一 durable work item。

worker 每次执行前先按 InvocationKey 查询 canonical final publication；存在时直接重建完整 `Completed`，不会
重新调用模型。不存在时从 admission pins 重建并校验 Bundle hash/rollout assignment。当前 claim epoch guard
同时围住工单创建和 final publication。final publication 一旦提交，即使后续 telemetry、badcase、rollout 或
legacy Memory projection 失败，public terminal 仍从 publication 恢复，不能被 compatibility `Failed` 覆盖。
相同 authenticated request 稳定重放；message 或 authorization fingerprint 改变得到 typed conflict。

API lifespan 已具备 abandoned start/execution pump；只有 `DATABASE_URL`、PostgreSQL ResponseDelivery 单主以及
显式 `DIALOGPILOT_DURABLE_CHAT_MODE=enabled` 同时成立才组合在线 facade。开关默认关闭，因为现有
`MemoryManager.add_messages` 仍是 final publication 后的同步投影，M1-T04B 尚未把它迁到已有 durable
conversation projection outbox。故本节点不把 build 描述成 production cutover，也不修订 M1 Exit 为 ready。

## 验证

验证覆盖 admission→bind→claim→guard→terminal 的一次执行与重放、authorization change conflict、canonical
public response/stages 恢复、publication/delivery/conversation query 回归，以及默认 flag-off lifespan 行为。

- 核心 admission/execution/publication/lifespan 定向：`36 passed`（新增 frozen contract proof 后）；
- 相邻 publication/query/cutover/public contract 回归：`45 passed in 11.81s`；
- 最终全量回归：`876 passed in 65.04s`；
- 变更文件 Ruff 与 `git diff --check`：通过。
