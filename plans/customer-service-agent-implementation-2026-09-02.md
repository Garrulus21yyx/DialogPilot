# 客服 Agent 目标架构实施跟踪

- 分支：`feat/customer-service-target-architecture`
- 权威需求：
  - `docs/customer-service-agent-target-architecture.zh-CN.md`
  - `docs/customer-service-agent-implementation-plan.zh-CN.md`
- 执行原则：按依赖 DAG 推进；每张任务卡独立验证、记录文件、commit 并 push；不把 `IMPLEMENTED` 冒充 `VERIFIED` 或 `READY`。
- 当前阶段：M0

## 状态

| 节点 | 状态 | 验证 / 产物 |
|---|---|---|
| Bootstrap：冻结需求文档与执行跟踪 | in_progress | 本文件、两份架构文档、新分支 |
| M0-T01 应用服务边界 | pending | `ChatApplication`、薄 `/chat`、characterization tests |
| M0-T02 稳定身份和值对象 | pending | typed IDs/keys、属性测试、链路 metadata |
| M0-T03 生产主链 Eval Runner | pending | `ChatApplicationRunner`、typed stage observations |
| M0-T04 当前行为基线 | pending | 冻结 baseline artifacts/report |
| M0-T05 Gate Manifest Foundation | pending | 版本化 manifest schema/validator/report |
| M1 完整会话事实与幂等发布 | pending | 按 M1-PF01、T00–T05/T03A/T04A 子节点推进 |
| M2 Route/Authority/Evidence/RAG | pending | 按 M2-PF01、T01–T06R 子节点推进 |
| M3 薄 Durable Agent Runtime | pending | 按 M3-T01–T09 子节点推进 |
| M4 Memory/Context/Commitment/Handoff | pending | 按 M4-T01–T08 及 release 子节点推进 |
| M5 Knowledge Lifecycle/Multimodal | pending | 按 M5-T01–T09 子节点推进 |
| M6 Eval/Observability/Release | pending | 按 M6-T01–T09 子节点推进 |
| X-T01–X-T05 跨里程碑治理 | pending | 在各 Gate 依赖点前完成适用项 |

## 变更记录

### Bootstrap

- 已创建分支 `feat/customer-service-target-architecture`。
- 工作区起始时存在其他未跟踪实验文件；不纳入本任务提交，除非后续任务卡明确需要。
- 计划提交文件：两份权威需求文档与本跟踪文件。

## 下一步

1. 提交并推送 Bootstrap。
2. 建立 M0-T01 的现状 characterization，确认 `/chat` 当前完整生命周期与依赖构造。
3. 在 Application Owner 处提取 `ChatApplication`，迁移 API 与 Eval 消费者。
