# DialogPilot 评测与架构收敛计划（2026-08-30）

## 目标

在不混入现有记忆机制与 Reviewer C 未提交改动的前提下，完成三处 owner-level 修复并重新评测：

1. 意图识别只输出项目支持的业务标签，领域、动作和升级信号不再混入同一 taxonomy。
2. 路由评测直接验证 Planner/TaskPlan；完整 Agent 执行作为单独评测层，避免用昂贵执行链证明路由。
3. RAG 对 vector、BM25、RRF 做可复现消融，只用 dev 选择配置，再将既有 heldout 作为已消费回归集验证。

## 当前基线

- Intent：120/180（66.67%），Macro-F1 0.4916。
- Routing：120/120，但当前错误地执行 Worker、ReAct 和 synthesis；P50 约 5.77s，P95 约 19.05s。
- Retrieval：Top-5 89/100，MRR 0.7097，nDCG@5 0.7546；严格 Rank-1 为 60/100。
- Stateful：100/100 机械回归；本轮不把该结果升级为安全闭环或 human Gold。

## 正向合同

- Intent：所有支持输入均返回 `IntentCategory` 的封闭集合；未知模型标签确定性映射为 `other`，同时保留独立的 group/action/escalation 字段。
- Fast routing：输入只经过 Planner，输出可比较的 TaskPlan；不得运行 Worker、工具、synthesis 或 verifier。
- Full execution：显式选择后才运行完整链路，并独立记录任务完成、工具、发布边界和延迟。
- Retrieval：候选身份保持 chunk 对齐；检索策略及权重可配置、可记录；dev 选型与 regression 验证不混用。

## 步骤

- [x] 1. 审计权威 owner、消费者和当前未提交差异，建立失败切片。
- [x] 2. 修复 Intent taxonomy 边界，补充属性/回归测试，运行 180 条意图评测。
- [x] 3. 拆分 Fast Routing 与 Full Execution，补充“零 Worker 调用”测试，运行 120 条路由评测。
- [x] 4. 增加 RAG 消融运行器，分析 dev 失败，选择最小配置修复并运行 100 条回归。
- [x] 5. 运行全仓测试与分层评测，更新教程/Pages 的真实口径。
- [x] 6. 按相互独立的阶段提交并 push；只暂存本轮明确修改的文件。

## 非目标

- 不把 provisional 数据称为生产准确率或人工 Gold。
- 不继续增加 Reviewer 数量；验证由确定性合同、属性测试、现有回归集和一次 fresh-context review 分工完成。
- 不重写当前正在变更的记忆机制，不部署第二套 Docker 服务。

## 变更日志

- 2026-08-30：创建计划，进入步骤 1。
- 2026-08-30：完成 owner 审计。确认 Intent 问题是粗/细标签业务边界重叠而非非法枚举；TaskPlan 是路由权威但 evaluator 错跑完整执行；RAG 缺少策略配置与消融入口。进入步骤 2。
- 2026-08-30：实现完成。Intent 170/180；Fast Routing 120/120、LLM 0 调用；RAG dev 选择 BM25-only，Recall@5 0.95、MRR 0.8575；全仓 170 tests passed。结果仍为 provisional/consumed regression。
