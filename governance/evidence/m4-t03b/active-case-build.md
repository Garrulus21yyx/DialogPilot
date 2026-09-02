# M4-T03B ActiveCase projection build evidence

日期：2026-09-02。状态：`IMPLEMENTED / TARGET CONSUMER SHADOW ONLY`。

## Owner、投影与选择合同

TicketService 仍是工单身份、状态、priority、assignee、时间和 version 的唯一事实 Owner。状态迁移只在真实
authoritative transition 时递增 version；既有 SQLite schema 使用前向 migration 补齐 version。只读 adapter 输出闭合
`NO_ACTIVE_CASE/CASES/UNAVAILABLE/CONFLICT`，失败与重复身份不会伪装成空集。projection 包含目标架构要求的
case/ticket、summary、topic、opaque entity refs、status、priority、SLA/commitment refs、assignee、时间与 version；
`published_response` 不进入 projection 或目标上下文。

legacy compare profile 冻结为：只排除 `CLOSED`（`RESOLVED` 继续包含）、limit 3、
`updated_at DESC, created_at DESC, ticket_id ASC`、section priority 90。candidate policy 对精确 ticket/case、
breached SLA/commitment、critical 与 security hard include；其余只用确定性 task topic/entity match、业务 priority 与
legacy recency order，不引入未经 heldout 校准的数值分数。

Router renderer 只输出有界 summary/ref；Worker renderer 必须重新使用 task query/topic/entity refs 选择并输出证据，
不能复用一份全局未结工单 section。冻结合同：
`governance/concurrency/m4-t03b-active-case-v1.json`。

## Dark shadow、pointer 与边界

默认 invocation binding 为 `SHADOW`：active policy 仍是 `active-case-legacy-recency-v1`，candidate 为
`active-case-context-policy-v1`，previous verified 仍指向 legacy。在线 section 继续由 legacy ID/顺序装配；target 只记录
逐 case target/legacy ID、顺序、match 与 typed state。rollback 确定性回到 previous verified legacy pointer。
M4-T03C 才拥有 pinned bounded-canary enable；本节点未切 consumer，也未声称 heldout、生产连续性或 rollback 演练完成。

SLA/Commitment 当前没有进入 legacy Ticket writer，因此 adapter 保留 typed refs 与 hard-include 规则，但真实 Owner
backfill/对账依赖 M4-T07。该 verification prerequisite 未满足，M4-T03B 不标记 VERIFIED/READY。

## 验证

聚焦测试覆盖 projection 全字段、`RESOLVED/CLOSED`、空/不可用/冲突、完整 legacy tie-break、version transition、
精确 ticket、critical/security、breached commitment、task relevance、Router/Worker scope、shadow pointer 与 rollback。
测试计数和全仓回归结果在本节点提交前写入。

聚焦 ActiveCase/Ticket/Application 回归：`30 passed in 1.77s`；全仓：
`950 passed in 84.90s`。Ruff（`api/main.py` 保留仓库既有 E402 例外）与 `git diff --check` 通过。
