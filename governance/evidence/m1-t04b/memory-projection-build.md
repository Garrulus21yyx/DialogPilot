# M1-T04B durable Memory projection composition evidence

日期：2026-09-02。状态：`IMPLEMENTED / FLAG OFF PENDING PRODUCTION GATE`。

## Root cause 与正向合同

T04 已让 PostgreSQL trigger 为四个 projection 原子创建 outbox，但 production lifespan 没有 adapter/consumer；
与此同时 `ChatApplication` 在 final publication 后同步调用 `MemoryManager.add_messages`。因此 durable chat
若直接启用，会出现 publication 已完成而 Memory effect 丢失的窗口，并让 PostgreSQL watermark 与真实
Redis/Chroma effect 脱节。

T04B 保持 `conversation_events` 为唯一 source authority，并把现有 MemoryManager 的四个真实 store operation
显式暴露为 target adapters：working window 的 raw+marker 同 Redis 事务；episodic 使用 canonical turn 的稳定
message/chunk ID upsert；summary 使用 source-range checkpoint CAS；fact extraction 只从允许的 final-response
event 原子安排 Redis job。summary/fact 在相同 event 的 working marker 缺失时返回 retry，不伪造成功。

adapter 只按 event payload 中的 canonical turn key 回读同 tenant/user/conversation turn；未知或缺失 turn fail
closed。dispatcher 在 effect 前后检查 deletion epoch，删除会清理 Redis raw/summary/fact/job/markers、Chroma
episodic、带 source conversation 的 facts；旧 profile 没有 provenance，不能猜测性相减，因此安全删除该用户
的 legacy fallback。effect 后删除竞态再次清理并 ACK `DELETION_FENCED`。

durable facade 组合时才把 ChatApplication direct Memory write 切为
`owned_by_conversation_projection_outbox`，同一个 lifespan worker 恢复 start/execution 与四类 projection。
没有完整 PostgreSQL composition 时，即使误设环境变量也继续 direct mode，避免静默漏写。

## 激活边界

本地 build 已闭合 T02D 的 T04B 前置，但默认开关仍关闭：真实 ResponseDelivery snapshot/cutover、multi-replica
failover rehearsal 与独立 Owner/SRE gate 尚未完成。本节点不宣称 production cutover 或 M1 Exit ready。

## 验证

验证覆盖 target idempotency、summary prerequisite retry、canonical turn scope/mapping、non-final fact exclusion、
既有 projection lease/crash/deletion/rebuild 性质，以及 chat/lifespan/compatibility 回归。

- projection policy/PostgreSQL adapter/Memory target 定向：`55 passed in 9.40s`；
- 全量回归：`881 passed in 65.53s`；
- 变更文件 Ruff 与 `git diff --check`：通过。
