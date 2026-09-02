# M4-T02A canonical ThreadSummary schema build evidence

日期：2026-09-02。状态：`SLICE IMPLEMENTED / M4-T02 IN PROGRESS`。

## Owner 与正向合同

Migration `20260902_0018` 建立 PostgreSQL `thread_summary_chunks` 与
`thread_summary_checkpoints`。chunk 是 immutable fixed range，绑定 tenant/user/conversation、`from_seq/to_seq`、
source SHA-256、summary、included/omitted ranges、summarizer/schema version 与 deletion epoch；checkpoint 保存
source/projection watermark、last chunk、closed state、expected version 与 deletion epoch。

`location:thread-summary:v1` 通过 DataLocation registry v5 从 REGISTERED 晋升 WRITE_APPROVED，唯一 producer
仍是 `thread-summary-projector`，并绑定实现证据、PostgreSQL deletion adapter 与 restore fence。迁移首写同时登记
v5 artifact fingerprint；未知/旧 registry 不能绕过 MigrationRunner verify。

数据库拒绝 chunk update、projection 超过 source、非零 checkpoint 无 last chunk，以及 tombstone/epoch 不匹配的
insert/update。Conversation deletion trigger 先删除 checkpoint 再删除 chunks，late producer 由 epoch fence 拒绝。
因此 schema slice 没有把 Redis summary 或模型 adapter提升为范围/checkpoint Owner。

## 边界

本 slice 只完成 schema/location/deletion owner。M4-T02 仍需 ThreadSummaryProjector 的连续 range 选择、source
hash 验证、同事务 chunk+CAS checkpoint、损坏检测/重建与模型 unavailable degradation；未完成前不把 M4-T02
标记 IMPLEMENTED。

## 验证

- migration/location/schema/claim 定向：`21 passed in 14.37s`；
- capability claim validator：`PASS`；
- 全量回归：`889 passed in 72.21s`；
- 变更文件 Ruff 与 `git diff --check`：通过。
