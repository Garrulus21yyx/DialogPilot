# M1 Exit v1 readiness audit

日期：2026-09-02。结论：`NOT_READY / DRAFT_ONLY / LOCAL INTEGRATION GAP`。

本报告只审计仓库证据，不代表 Application+Platform owner 或 Evaluation/Security/Privacy 的独立签署，不授权 M2 behavior shadow、M3 build 或任何生产 cutover。

## 已有 build 证据

| 条件 | 当前证据 | 判定 |
|---|---|---|
| PostgreSQL schema/role/pool/migration | M1-PF01、X-T01 registry/local restore | IMPLEMENTED；生产 snapshot/RPO/RTO 未 VERIFIED |
| Admission/CAS/outbox contract | M1-T00/T01/T02 + multi-process/fault tests | IMPLEMENTED repository port |
| Publication/delivery lifecycle | M1-T03 + PostgreSQL compatibility service | IMPLEMENTED；生产 binding/cutover 未 VERIFIED |
| ResponseDelivery cutover protocol | M1-T03A scripts/local restore evidence | IMPLEMENTED local rehearsal only |
| Projection/deletion fence | M1-T04 + effect/ACK/deletion fault tests | IMPLEMENTED |
| DataLocation/pre-write fence | M1-T04A v1–v4 registry | IMPLEMENTED |
| Transcript/status/finalize/close reads | M1-T05 PostgreSQL query projections | IMPLEMENTED when PostgreSQL is configured |
| Lease/multi-replica contract | X-T02 fencing/multi-process/dual-active tests | IMPLEMENTED；生产 failover rehearsal pending |

## 本地实现阻断项

1. `ChatApplication` 尚未消费 `PostgresAdmissionUnitOfWork` 或等价 Application admission port。当前主链在 `_handle_ready()` 中先读取 Memory、执行 Intent/Agent，最后才通过兼容 Delivery/Memory 写入；因此“所有用户输入在任何推理前 durable”不成立。
2. `StartOutboxDispatcher` 只在 repository tests 中组合，lifespan 没有启动 dispatcher 或把当前 invocation 绑定到稳定 compatibility execution owner。不能用 outbox 表存在来宣称 crash 后会实际接管。
3. 因前两项，API 对同 request 的并发/retry 仍没有以 AdmissionResult 决定 sole runner/replay/Accepted/Conflict；repository 的唯一键/CAS 证明不能替代在线 consumer 集成。

上述缺口必须在 Application/admission owner 边界修复：先 durable admit，再由稳定 claim/CAS 决定唯一 execution owner；Existing 必须读取同一 invocation/publication 或返回 typed Accepted，不能第二次运行推理。修复不能放在 HTTP adapter 或报告中。

## 环境与独立证据阻断项

1. `M1-PRODUCTION-SNAPSHOT-RESTORE` 当前 checksum 指向的 foundation artifact 明确是 local empty/application schema rehearsal，并写明没有 production snapshot，不能满足 `VERIFIED`。
2. `M1-RESPONSE-DELIVERY-CUTOVER` 当前 artifact 是 local isolated cutover/restore；没有生产 stop/backfill/count+hash/reconcile/single-writer observation。
3. TicketService 在 M4-T07C 前、compatibility Agent ledger 在 M3-T06/T09 前仍保留 SQLite。inventory 已修正为各自真实 future cutover Owner；M1 只能批准显式 retention，不得提前停写或把它们伪装成 PostgreSQL 已迁移。
4. 没有真实 PostgreSQL primary failover、生产 stale worker/drain、lock/storage/RTO 数据，也没有 Application+Platform owner signature 与 Evaluation/Security+Privacy independent approval。

## Gate 边界

- `evaluation/gates/m1-exit/v1.yaml` 是可重放、unsigned `DRAFT`；不生成 `.evidence.json` 或 `.decision.json`。
- M3-T09、M4-T07C 等未来 release action 不是 M1 build prerequisite；保留 SQLite 的条件决策不会形成依赖环。
- M2 dark shadow 与 M3-T01 都依赖 M1 Exit APPROVE，当前禁止启动。M2 的 flag-off build 证据可保留，但不能据此发布用户可见行为。

## 恢复审计条件

先完成在线 admission/sole-runner/replay 集成及 end-to-end crash/concurrency properties；再用真实生产 snapshot 和 delivery cutover 副本执行 count/hash/RPO/RTO/lock rehearsal，完成 PostgreSQL/Redis failover 与 legacy retention review。最后由声明 Owner 冻结/运行 manifest，并由独立 approvers 审阅。只有 checksum 一致的 `APPROVE` decision 才能解除 M2 shadow/M3 build 前置。
