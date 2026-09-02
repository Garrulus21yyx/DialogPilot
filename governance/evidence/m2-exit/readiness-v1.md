# M2 Exit v1 readiness audit

日期：2026-09-02。结论：`NOT_READY / DRAFT_ONLY`。

本报告只审计仓库证据，不代表 Evaluation 或 Domain Tool 的独立签署，也不授权 bounded canary。

## 已有 build 证据

| 条件 | 当前证据 | 判定 |
|---|---|---|
| 八种 RouteMode 闭合 typed outcome/forbidden calls | M2-T01/T06、Application-boundary fixtures | IMPLEMENTED |
| 动态事实/动作 authority gate | M2-T02/T03/T04、native tool receipts | IMPLEMENTED flag-off |
| 单一 KnowledgeRetriever 与 cache/consumer 迁移 | M2-T05 A1/A2/B | IMPLEMENTED；canary blocked |
| PG backend/generation/outbox/delete fence dark shadow | M2-PF01、M2-T05 frozen report | IMPLEMENTED；生产 RTO/OLTP 未 VERIFIED |
| TaskGraph/formation/synthesis | M2-T06A | IMPLEMENTED |
| Route Bundle pinned rollback | M2-T06R + crash tests/runbook | IMPLEMENTED；canary blocked |
| Handoff 分类/目标兼容 draft | M2-T06 B7/B8 | IMPLEMENTED dark-shadow；无生产 Handoff write |
| X-T03 threat/control/corpus/disable | X-T03 build evidence + 13-case corpus | IMPLEMENTED；生产安全复核待补 |
| X-T04 route/ingest cost policy | X-T04 build evidence + frozen cost manifest | IMPLEMENTED；生产账单抽样待补 |

## 阻断项

1. X-T04 已有 provider usage capture/reconciliation 实现，但没有 production invoice/export 的真实账单样本，
   `provider_usage_reconciliation_rate=1.0` 尚未由部署证据满足。
2. `M2-KNOWLEDGE-HELDOUT` 当前 manifest 明确为 `REVIEW_REQUIRED / PROVISIONAL_NOT_GOLD`；实现上下文
   作者创建的 heldout 不能代替独立 human review。
3. `M2-T04A` 与 `M2-PF01` 的 build 已完成，但生产快照/Recall/latency/rebuild RTO/OLTP 影响仍没有
   VERIFIED 证据。
4. M2 route shadow 的行为前置仍受 `M1 Exit` 约束；当前 M1 总状态不是 APPROVE，不能据本草稿开启流量。
5. Gate 尚无 Application+Agent+Platform owner signature，也无 Evaluation+Domain Tool independent approval；
   禁止生成伪造 evidence/decision 文件。

## 非阻断项边界

- `M2-T05C` 是 M2 Exit 后的独立 release action，不是本 Gate build prerequisite。
- LangGraph、Multimodal 与 Graph Retrieval 不属于 M2 Exit。
- `CORE_TEXT_GA` 之前不得广泛启用八条 route；M4-T07C 之前不得开启新的生产 Handoff write。

## 恢复审计条件

由独立 reviewer 复核 X-T03/X-T04 并封存 unseen knowledge heldout；补齐真实 provider billing sample 与
生产平台证据；确认 M1 Exit
行为前置；随后由声明的 evidence owners 冻结/运行 manifest，Evaluation 与 Domain Tool 独立审阅。
只有生成 checksum 一致的 `APPROVE` decision 后，M2 bounded canary 才可进入单独 release action。
