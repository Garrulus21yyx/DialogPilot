# M1 Exit v1 readiness audit

日期：2026-09-02。结论：`NOT_READY / DRAFT_ONLY / PRODUCTION EVIDENCE GAP`。

本报告只审计仓库证据，不代表 Application+Platform owner 或 Evaluation/Security/Privacy 的独立签署，不授权 M2 behavior shadow、M3 build 或任何生产 cutover。

## 已有 build 证据

| 条件 | 当前证据 | 判定 |
|---|---|---|
| PostgreSQL schema/role/pool/migration | M1-PF01、X-T01 registry/local restore | IMPLEMENTED；生产 snapshot/RPO/RTO 未 VERIFIED |
| Admission/CAS/outbox contract | M1-T00/T01/T02 + multi-process/fault tests | IMPLEMENTED repository port |
| Durable compatibility execution | M1-T02C/T02D claim epoch、pinned assignment、canonical replay、lifespan pump | IMPLEMENTED；online facade flag-off |
| Publication/delivery lifecycle | M1-T03 + PostgreSQL compatibility service | IMPLEMENTED；生产 binding/cutover 未 VERIFIED |
| ResponseDelivery cutover protocol | M1-T03A scripts/local restore evidence | IMPLEMENTED local rehearsal only |
| Projection/deletion fence | M1-T04/T04B + real Redis/Chroma target adapters、effect/ACK/deletion fault tests | IMPLEMENTED；online facade flag-off |
| DataLocation/pre-write fence | M1-T04A v1–v4 registry | IMPLEMENTED |
| Transcript/status/finalize/close reads | M1-T05 PostgreSQL query projections | IMPLEMENTED when PostgreSQL is configured |
| Lease/multi-replica contract | X-T02 fencing/multi-process/dual-active tests | IMPLEMENTED；生产 failover rehearsal pending |

## 已关闭的本地集成缺口

1. T02D 现在先持久化 inbound/accepted/invocation/start outbox，之后才允许 durable compatibility worker
   进入 Memory/RAG/LLM/Tool。stable binder、claim epoch 与 lifespan pump 只恢复同一 invocation/run。
2. Existing/Conflict/Accepted/terminal replay 已由 Application coordinator 决定；worker 在生成前查 canonical
   publication，final commit 后的投影失败不能覆盖成 `Failed` 或触发模型重生成。
3. T04B 已把四类 conversation projection 组合到真实 Redis/Chroma target operation，并在 durable facade
   内关闭 direct Memory write；每个 target 有 event idempotency 与 deletion fence。

这些是 repository/local integration build 证据，不等于生产开关已启用。显式 flag-off 保持到下述 production
cutover/failover 和独立 GateDecision 完成；本审计不把“缺生产证据”重新伪装成已关闭的代码缺口。

## 环境与独立证据阻断项

1. `M1-PRODUCTION-SNAPSHOT-RESTORE` 当前 checksum 指向的 foundation artifact 明确是 local empty/application schema rehearsal，并写明没有 production snapshot，不能满足 `VERIFIED`。
2. `M1-RESPONSE-DELIVERY-CUTOVER` 当前 artifact 是 local isolated cutover/restore；没有生产 stop/backfill/count+hash/reconcile/single-writer observation。
3. TicketService 在 M4-T07C 前、compatibility Agent ledger 在 M3-T06/T09 前仍保留 SQLite。inventory 已修正为各自真实 future cutover Owner；M1 只能批准显式 retention，不得提前停写或把它们伪装成 PostgreSQL 已迁移。
4. 没有真实 PostgreSQL primary failover、生产 stale worker/drain、lock/storage/RTO 数据，也没有 Application+Platform owner signature 与 Evaluation/Security+Privacy independent approval。

## Gate 边界

- `evaluation/gates/m1-exit/v1.yaml` 是可重放、unsigned `DRAFT`；不生成 `.evidence.json` 或 `.decision.json`。
- M3-T09、M4-T07C 等未来 release action 不是 M1 build prerequisite；保留 SQLite 的条件决策不会形成依赖环。
- M2 dark shadow 与 M3-T01 都依赖 M1 Exit APPROVE，当前禁止启动。M2 的 flag-off build 证据可保留，但不能据此发布用户可见行为。

## 本次审计验证

- M1 gate + admission/execution/publication/projection 因果面：`51 passed in 24.31s`；
- 全量回归：`881 passed in 63.17s`；
- manifest 可重放且仍为 unsigned `DRAFT`；Ruff 与 `git diff --check` 通过。

## 恢复审计条件

下一步直接使用真实生产 snapshot 和 delivery cutover 副本执行 count/hash/RPO/RTO/lock rehearsal，完成
PostgreSQL/Redis multi-replica failover、projection backlog/drain 与 legacy retention review。随后由声明 Owner
冻结/运行 manifest，并由独立 approvers 审阅。只有 checksum 一致的 `APPROVE` decision 才能启用 durable
online flag 并解除 M2 shadow/M3 build 前置。
