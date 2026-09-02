# M4-T04B ServiceEpisode retrieval build

日期：2026-09-02。状态：`FUSION CONTRACT IMPLEMENTED / CALIBRATION AND E2E PENDING`。

## Owner 与正向合同

`PostgresHybridBackend` 只生成 tenant/user scoped dense 与 lexical candidates，并附 canonical `verified_at` 和 generation
`source_watermark`。`ServiceEpisodeRetriever` 是融合、relevance gate、freshness gate 与可解释 trace 的唯一 Owner；它绑定一个
`ServiceEpisodeRetrievalPolicy` fingerprint，内部固定 Memory RRF policy 和两项独立阈值。

时间信号只对 dense/lexical candidate ID 并集排名，不能创建候选。每个 hit 固定 vector/lexical/recency source rank、policy
fingerprint、episode revision、provenance、freshness 与 index watermark。同一 candidate ID 在两路出现但 canonical ref、revision、
provenance 或 freshness 不一致时返回 `CONFLICT`；缺 watermark、损坏/未来 freshness、policy 漂移返回
`INVALID_CONTRACT`，后端 `UNAVAILABLE/CONFLICT` 不降格为 miss。

## 隔离与验证状态

PostgreSQL 集成用相同 query/entity 同时放入同 tenant 不同 user、不同 tenant 相同 user 的 episode，只返回请求的
tenant+user。聚焦回归：`23 passed in 5.26s`；全仓：`975 passed in 100.36s`。当前阈值只在 deterministic contract fixture 中显式传入，不冒充真实 heldout
校准；未连接 `ChatApplication`，也未启用 consumer。当前项目无 legacy 运行数据，因此 migration/backfill 与 old/new runtime
shadow 均不适用；后续使用新 canonical fixtures 做 paired offline replay 和真实主链 E2E，验收后按目标架构直接切唯一 binding。
