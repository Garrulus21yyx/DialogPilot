# M4-T04B2 durable Memory retrieval binding evidence

日期：2026-09-02。状态：`DURABLE POINTER IMPLEMENTED / CONSUMER DISABLED`。

Migration `20260902_0021` 新增 tenant + ServiceEpisode corpus 唯一行，将 active、previous、shadow candidate 的
MemoryRetrievalPolicy fingerprint、backend generation 与 corpus generation 放在同一 CAS authority。SHADOW/
PINNED_CANARY 必须有完整 candidate；ACTIVE/LEGACY 禁止残留 candidate。数据库 trigger 要求每次 update 恰好递增一个
version，repository 的 stale writer、不同初始化 tuple 和 rollback version drift 均 typed `GenerationConflict`。

`PostgresMemoryRetrievalBindingRepository.initialize_shadow` 只允许相同 tuple 幂等；`compare_and_swap` 一次更新所有字段；
`rollback` 一次恢复 previous tuple 并清空 candidate。读取结果是 frozen dataclass，因此 in-flight invocation 不受后续 CAS
影响。操作步骤冻结在 `docs/m4-t04-memory-retrieval-binding-runbook.zh-CN.md`。

本 slice 没有执行 canary/ACTIVE 切换。真实 legacy inventory、backfill reconciliation、same-query shadow 与正式 GateDecision
仍不存在，默认 consumer 必须保持 legacy/shadow。

聚焦 migration/schema/pointer/rollback 回归：`36 passed in 24.96s`；全仓：
`969 passed in 102.28s`。Ruff 与 `git diff --check` 通过。
