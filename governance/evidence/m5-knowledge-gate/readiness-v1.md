# M5 Knowledge Gate v1 readiness audit

日期：2026-09-02。结论：`NOT_READY / DRAFT_ONLY / EXTERNAL EVIDENCE PENDING`。

本报告只审计仓库内的 M5-T01 build，不代表 Knowledge Owner、Evaluation 或 Product/Support Ops 签署，
不授权 scoped canary，更不满足 `KNOWLEDGE_LIFECYCLE_GA`。

## 已满足的本地 build 性质

- M5-T01、X-T01、X-T04 build prerequisites 均已实现；M5-T02～T09 按 Gate 矩阵明确 non-blocking。
- canonical 状态乘积覆盖全部合法/非法迁移，未知状态 typed `INVALID_TRANSITION`。
- PostgreSQL 集成覆盖 scoped/global gate fence、expected-version CAS、请求 immutable manifest pin、
  old/new 完整 generation、previous manifest rollback，以及 retracted/rejected evidence fail-closed。
- Retriever 对同一逻辑 source 的多 revision 返回 `CONFLICT`；feedback/ticket/zero-hit 只写入带
  privacy review ref 的 immutable candidate，不存在自动发布边。
- `20260902_0016` 已进入 16-revision forward-only schema registry；空库和逐版本升级由全仓回归覆盖。

## 阻断项

1. `data/eval/knowledge-source-v0` 仍为 `PROVISIONAL_NOT_GOLD / REVIEW_REQUIRED`，没有 fresh heldout 的
   独立双人审核、仲裁结果和 Product/Support Ops 适用性批准，不能满足 `VERIFIED` threshold。
2. 没有真实 staging index/canary 流量窗口、并发 pointer swap、in-flight drain、production latency/storage
   budget 或 rollback reconciliation 的运行证据；repository property tests 不能替代这些观测。
3. 没有 Knowledge Owner signature，也没有 Evaluation 与 Product/Support Ops 的独立批准。
4. 既有 production snapshot/restore 和 failover 证据仍待补；`0015` 本地 restore 不能被改写成 `0016`
   或生产 restore 证明。

## Gate 边界

`evaluation/gates/m5-knowledge-gate/v1.yaml` 是 checksum-bound、可重建、unsigned `DRAFT`，因此不生成
evidence/decision 文件。待 fresh heldout 冻结后，应由 Knowledge Owner 签署并冻结 manifest，在隔离
staging 执行 fault/canary/rollback 观察，再由 Evaluation 与 Product/Support Ops 独立审阅。只有正式
`APPROVE` decision 才允许 scoped canary；global activation 仍需另行满足 `KNOWLEDGE_LIFECYCLE_GA`。

验证结果：Gate/manifest 聚焦测试 `15 passed`；全仓 PostgreSQL suite `865 passed in 54.03s`；ruff 与
`git diff --check` 通过。
