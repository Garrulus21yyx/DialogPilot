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
| Profile/MemoryFact 切换 PostgreSQL 并删除 Chroma 存储服务 | done | Redis 仅保留当前窗口投影 |
| 删除生产迁移、Reviewer 签署与多副本演练材料 | done | 保留空库 Alembic 初始化和本地 dump/restore |
| 删除 Eval Graduation/Pareto 与 baseline promotion API | done | `/eval/run` 保留确定性 dev/heldout 报告 |
| 收缩本地评测并清理展示文档中的旧路径 | done | README/架构/项目讲述/面试文档以当前代码为准 |
| 本地 E2E、恢复验证、Demo 复现 | done | `4a0a0f4`；835 tests passed；两份机器报告 PASS |
| OCR/VLM 分级调用 | done | `c0b7d96`、`ac8ee64`、`5ad83d6`、`b77a196`；L1/L2 两份真实 E2E PASS；887 tests passed |
| Handoff PostgreSQL 收敛 | complete | Ticket/Event/Outbox 已直接绑定 PostgreSQL，SQLite writer/config/test 已删除 |
| Commitment 最小闭环 | complete | PostgreSQL owner、显式来源、自动违约、receipt 履约、Agent read tool 与 Handoff critical 联动；HTTP E2E PASS |
| 持久 OTel/Langfuse | pending | 当前只有 TraceId、进程内 span 与 Prometheus |

## 必须保留的核心

- Router / Planner / TaskGraph / Worker / ReAct / ContextPolicy。
- PostgreSQL checkpoint、幂等键、Tool receipt、ServiceEpisode、Profile、Commitment、Handoff。
- Redis 当前会话窗口，作为 PostgreSQL 事件的可重建快速投影。
- Knowledge / Business tools、Evidence / Coverage，以及 Agent-owned L0/L1/L2 媒体决策。
- TraceId/Prometheus、本地崩溃恢复、确定性评测和 Demo；持久 OTel/Langfuse 待实现。

## 完成标准

1. 全量测试通过。
2. 空库 PostgreSQL 初始化和本地 dump/restore 通过。
3. 本地 E2E 通过并产出机器可读报告。
4. README、架构说明和 Demo 命令不再引用已删除的旧路径。
