# M5-T01 SourceRevision lifecycle build evidence

日期：2026-09-02。状态：`IMPLEMENTED / BEHAVIOR GATED`。

## 正向合同与 Owner

不可变 `SourceRevision` 是内容、适用范围和运营来源的唯一 Owner；可变审核状态由独立 lifecycle row
和 append-only audit 拥有，避免修改不可变内容事实。状态代数严格为
`DRAFT→REVIEWED/REJECTED`、`REVIEWED→STAGED`、`STAGED→ACTIVE/REJECTED`、
`ACTIVE→SUPERSEDED/RETRACTED`，未知和其余边统一返回 `INVALID_TRANSITION`，数据库 trigger
再次执行相同约束。

Knowledge Publication 独占 scoped canary/global 指针。scoped pointer 需要
`M5-KNOWLEDGE-GATE:READY`，global pointer 需要 `KNOWLEDGE_LIFECYCLE_GA:READY`；两者都以
expected version 做事务内 CAS。每个请求首次解析指针时持久化 immutable manifest pin，切换或回滚后
已开始请求仍使用原 generation。rollback 只交换 current/previous verified manifest，不把
`SUPERSEDED` 历史伪改回 `ACTIVE`；被请求固定的 superseded revision 仍可解析，而
`RETRACTED/REJECTED` 一律不能产生 evidence payload。

Retriever 对同一个 `source_id` 同时出现多个 revision 返回 typed `CONFLICT`。点踩、人工工单和
zero-hit 只能写入脱敏、不可变 `KnowledgeCandidate`，该表与发布指针之间没有自动提升路径。

## 数据与迁移边界

`20260902_0016` 以 forward-only migration 增加 lifecycle/audit、publication pointer/audit、request pin
和 candidate 表，并为 v0 修订回填明确的 legacy owner/applicability 与 `ACTIVE` lifecycle。新 v1 修订必须
先显式 register draft，经 review/stage 后才允许进入 generation。schema registry 已扩展为 16-revision
单 head；既有 `0015` restore 演练仍按历史证据标注，未冒充 `0016` production restore。

## 验证范围与限制

property/state-product 测试覆盖全部状态对、未知状态、reject/retract/supersede、review provenance；
PostgreSQL 集成覆盖旧 v0 backfill、v1 stage fence、scoped/global gate、指针 CAS、旧/新完整 generation、
request pin、rollback、candidate 不发布和 retracted evidence fail-closed。行为 rollout 仍受 M5 Knowledge
Gate 与 `KNOWLEDGE_LIFECYCLE_GA` 阻断，本产物不声明生产知识已切换，也不替代 fresh heldout 与独立
Product/Support Ops 审核。

验证结果：全仓 PostgreSQL suite `862 passed in 56.27s`；ruff 与 `git diff --check` 通过。
