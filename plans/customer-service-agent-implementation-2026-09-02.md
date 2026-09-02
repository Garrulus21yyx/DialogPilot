# 客服 Agent 本地简历版实施跟踪

- 分支：`feat/customer-service-target-architecture`
- 已完成需求文档：
  - `docs/customer-service-agent-target-architecture.zh-CN.md`
  - `docs/customer-service-agent-implementation-plan.zh-CN.md`
- 目标：本地完整跑通、可复现 Demo、适合简历展示。
- 数据前提：没有旧数据，不保留 backfill、双写、shadow/canary、promotion/rollback 或生产审批模拟。

## 收敛进度

| 节点 | 状态 | 验证 / 提交 |
|---|---|---|
| 删除 raw episodic Chroma writer/reader 与空 backfill | done | `e9fe382`、`9957966` |
| 删除 Agent/Route shadow、canary/promotion 与 rollout 状态机 | done | `a7ada9f`、`cd2878d`、`15dce38`、`c55e6e1` |
| ActiveCase relevance selector 成为唯一消费者 | done | `2dd226d` |
| PostgreSQL ResponseDelivery 单主，删除 SQLite cutover/binding | done | `bfc28e7` |
| PostgreSQL Knowledge reader/ingestion 单主 | done | `8f76df7`、`0d10f1c`、`6dc923b` |
| 删除 Chroma Knowledge 栈与 publication rollout lifecycle | done | `be11e9c`、`1fe1ea3` |
| 删除 release gate/signature、双盲仲裁与 claim audit | done | `465d4fe` |
| 删除 governance evidence/concurrency 冻结副本 | done | `d90ba81`；848 tests passed |
| ServiceEpisode 直接解析唯一 ACTIVE generation | done | `679a89b`；844 tests passed |
| 删除生产迁移、Reviewer 签署与多副本演练材料 | done | 保留空库 Alembic 初始化和本地 dump/restore |
| 删除 Eval Graduation/Pareto 与 baseline promotion API | done | `/eval/run` 保留确定性 dev/heldout 报告 |
| 收缩本地评测并清理文档中的旧路径 | in_progress | 保留确定性 dev/heldout 与机器报告 |
| 本地 E2E、恢复验证、Demo 复现 | pending | 最终验收 |

## 必须保留的核心

- Router / Planner / TaskGraph / Worker / ReAct / ContextPolicy。
- PostgreSQL checkpoint、幂等键、Tool receipt、ServiceEpisode、Profile、Commitment、Handoff。
- Redis 当前会话窗口，作为 PostgreSQL 事件的可重建快速投影。
- Knowledge / Business / OCR / VLM tools 与 Evidence / Coverage。
- 基础 OpenTelemetry / Langfuse、本地崩溃恢复、确定性评测和 Demo。

## 完成标准

1. 全量测试通过。
2. 空库 PostgreSQL 初始化和本地 dump/restore 通过。
3. 本地 E2E 通过并产出机器可读报告。
4. README、架构说明和 Demo 命令不再引用已删除的旧路径。
