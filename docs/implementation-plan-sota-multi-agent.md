# SOTA Multi-Agent 改造计划

## 目标合同

在保留“简单请求单 Agent、独立复合任务才并发”的前提下，把领域关键词路由升级为可验证的任务编排：每个必需子任务有唯一 Owner、闭合执行状态和覆盖结果；整次请求受统一预算约束；账户安全由独立能力负责；最终发布能够区分完整、部分、冲突、预算耗尽和人工接管。

## 不变量与非目标

- 每个 `required` 任务必须恰好映射到一个可用 Agent Owner。
- 每个已派发任务必须产生 `success/timeout/error/budget_exceeded` 中的一个结果。
- 最终回答不得把缺失的必需任务标记成完整成功。
- 请求级 deadline 优先于单 Agent timeout，剩余预算必须向下传播。
- 账户安全不再由 Billing 领域拥有。
- 本轮不引入 LangGraph、Swarm、分布式队列或无界 Agent 递归。

## 阶段

| 阶段 | 状态 | 交付与验证 |
|---|---|---|
| 1. 建立任务合同和 CoverageGate | done | `TaskSpec/TaskPlan/TaskOutcome/CoverageReport`，迁移编排和融合，单元/属性测试 |
| 2. 增加 AccountSecurity 能力和请求预算 | in_progress | 独立 Agent、统一 deadline/max_agents、取消与预算状态测试 |
| 3. 扩展验证、评测和可观测输出 | pending | Verifier 感知任务覆盖，编排指标与 API 结果字段，回归测试 |
| 4. 更新项目讲解 Page | pending | 新架构图、链路、面试追问和实现证据，静态页面验证 |

## 影响文件记录

- 阶段 1：新增 `agents/orchestration_contracts.py`；迁移 `agents/agent_orchestrator.py`、`services/result_synthesizer.py` 和 `tests/test_agent_orchestration.py`。完整回归 44 passed。
- 阶段 2：待更新。
- 阶段 3：待更新。
- 阶段 4：待更新。
