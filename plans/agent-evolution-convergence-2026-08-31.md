# Agent 进化闭环收敛计划（2026-08-31）

## 目标

把现有 `BadCaseRegistry -> provisional regression` 扩展为可验证、可发布、可回滚的 Agent 进化闭环；同时先闭合该闭环依赖的评测基线、任务图和可恢复执行合同。每个阶段独立测试、提交并 push，最后同步 GitHub Pages。

## 正向合同

1. **评测事实**：评测 Run 永远不能自动覆盖 Active Baseline；Rubric 的硬约束不能被平均分抵消；只有满足确定性、质量、效率和数据 provenance 门禁的候选才能获得 `GRADUATED` 结论。
2. **任务编排**：每个任务携带唯一 Owner、目标、证据片段、上下文引用、依赖和副作用类别；调度器只并行执行同一波次中互不依赖且只读的任务，写任务保持稳定串行顺序；每个 Worker 只接收其任务范围内的上下文。
3. **可恢复执行**：每次 ReAct Run 绑定稳定版本和 `run_id`；Step/工具结果持久化；等待审批可以跨请求恢复；已成功写工具不能因重试、恢复或进程重启重复执行；终态闭合且可审计。
4. **进化归因**：Bad Case 保存脱敏、不可变的 `EvolutionEnvelope`，把失败绑定到实际 AgentBundle、模型、Prompt、路由、检索、工具和任务生产者；投影不能成为新的权威状态。
5. **候选进化**：Evolution Agent 只能创建不可变候选 `AgentBundle`，可编辑面限制为 Prompt/Few-shot/工具描述/检索及路由策略；安全边界和生产代码不可自动修改。
6. **发布与回滚**：请求开始时固定 AgentBundle；Shadow 不发布副本结果；Canary 按稳定分桶分流；硬安全信号立即回滚，软指标只有满足样本与阈值合同才回滚；回滚只原子切换 Active 指针，不篡改历史版本。

## 阶段

| 阶段 | 状态 | Owner / 主要产物 | 验证与提交 |
|---|---|---|---|
| 0. 架构与因果面盘点 | completed | 本计划；现有 evaluator/orchestrator/ReAct/BadCase/API 消费面 | 受影响消费者清单完整 |
| 1. Immutable Baseline + Rubric + Graduation | completed | `evaluation/rubric.py`、`evaluation/graduation.py`、Evaluator/API/测试迁移 | 220 tests passed；`0051ed9` 已 push |
| 2. 依赖感知且上下文隔离的 TaskGraph | completed | orchestration contracts、scheduler、orchestrator/API/评测投影 | 225 tests passed；`d178f1a` 已 push |
| 3. Run Checkpoint + Approval Resume | completed | ReAct RunStore、ToolManager 幂等调用、API resume | 238 tests passed；`e9682ad` 已 push |
| 4. EvolutionEnvelope + AgentBundle Registry | completed | `services/evolution/*`、BadCase/API 版本归因 | 244 tests passed；`c19e2c8` 已 push |
| 5. GEPA-lite Candidate + Graduation 集成 | completed | 聚类、责任归因、候选生成、Pareto 选择 | 248 tests passed；待 commit/push |
| 6. Shadow/Canary/Active + 自动回滚 | in_progress | Rollout Owner、稳定分桶、指标窗口、Active 指针 | 硬信号立即回滚、软信号防抖、在途版本固定；commit/push |
| 7. Pages/教程/面试问答同步 | pending | architecture、project-pitch、完整教程、图 | 页面构建与浏览器验证；commit/push |

## 影响面

- 生产：`api/main.py`、`agents/agent_orchestrator.py`、`agents/orchestration_contracts.py`、`agents/react_engine.py`、`mcp/tool_manager.py`、`services/badcase_registry.py`
- 评测：`evaluation/evaluator.py`、`evaluation/benchmark.py`、`evaluation/dataset.py`、新增 graduation/rubric/candidate runner
- 配置/持久化：Docker volume 与环境变量、SQLite schema 的向后兼容迁移
- 投影：公开 HTTP 继续脱敏；管理员接口才能查看候选、门禁和 rollout 证据
- 文档：README、Pages 架构页、项目讲述页、完整教程和面试追问

## 非目标

- 不进行在线权重训练或把 provisional 数据冒充人工 Gold。
- 不允许模型自动修改 Python 生产代码、工具权限、审批、JWT、PII 或 Verifier fail-closed。
- 不为了形态升级引入 LangGraph；先在现有 Python 边界实现必要合同。
- 不增加 Agent/工具数量，也不把 ReAct 步数机械提升到 20。

## 退出条件

- 所有 Owner 只有一个权威状态源，投影和报告不反写权威事实。
- 支持的状态、迁移、终态、幂等键和未知状态处理均闭合。
- 属性/状态机/变异或生成式测试覆盖正常、失败、重试、取消、审批、并发、回滚和投影边界。
- 现有 211 项回归与新增测试全部通过；CI 与 Pages 成功。
- 新鲜对抗用例不需要逐例新增生产分支即可通过。
- 代码、测试、文档、线上页面和对外口径一致。

## 变更记录

- 2026-08-31：建立计划；阶段 0 开始。
- 2026-08-31：确认基线覆盖和回归比较的重复权威根因；阶段 0 完成，阶段 1 开始实施。
- 2026-08-31：阶段 1 完成；普通 run 与 baseline 写权分离，Rubric 硬门禁和显式 Graduation 接入，220 项全仓测试通过。
- 2026-08-31：阶段 2 完成；TaskGraph DAG、读并行/写串行、依赖失败终态和 Worker 范围投影接入，225 项全仓测试通过。
- 2026-08-31：阶段 3 完成；SQLite Run checkpoint、JWT 身份绑定审批、跨进程 Resume 和 call_id 幂等账本接入，238 项全仓测试通过。
- 2026-08-31：阶段 4 完成；请求级固定不可变 AgentBundle，真实 Prompt/路由/检索/工具描述接线，Bad Case EvolutionEnvelope 脱敏归因，244 项全仓测试通过。
- 2026-08-31：阶段 5 完成；确定性责任归因、GEPA-lite 受限补丁、真实 GateArtifact、Bundle 绑定 Graduation 和质量/延迟/成本 Pareto 前沿接入，248 项全仓测试通过。
