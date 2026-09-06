# Agent 自主规划与统一动作治理迁移

## 观察与根因

τ³ 入口仅完成首轮消息探测。换货无法表达不是缺少另一个标签：ConversationAgent 的业务枚举/分支、RoutePolicy 的预选只读工具以及 ActionDefinition 必须绑定 Flow 共同限制了开放能力。治理机制已有 GovernedWriteRuntime，不应另建执行引擎。

## 正向合同

主 Agent 根据 Registry 中的领域描述与可用能力提出开放目标；领域 Agent 在当前授权包络内规划工具。工具事实、执行权限、风险由 Registry/ToolManager 拥有，目标文本不授予权限。明确只读查询继续允许直接执行。写动作由共享治理执行，审批绑定具体参数与对象，未知结果保留对账；业务 Flow 只用于确需持久编排的操作。没有业务后端能力时明确不可用，不把换货转换为退款。

## 分阶段迁移

1. implemented：Registry 领域能力卡、通用目标委派、Schema/编译/RoutePolicy/领域执行接线与回归。新增领域无需业务目标枚举；当前仅开放只读委派。合同回归通过，尚无真实模型收益结论。
2. implemented：Action 可不关联 Flow；统一 PREPARE_ACTION / EXECUTE_ACTION / CONTINUE_ACTION，独立写入使用 ACTION 执行模式。已有待审批 Workstream、信号消费、恢复及操作账本复用。PostgreSQL 回归 136 passed、2 skipped（两项内存账本不适用的数据库隔离测试）。
3. implemented / verification_open：领域模型提出写动作，经共享准备服务、已有审批状态和写运行时暂停、恢复、继续；使用框架原生 DomainOutcome 明确完成、阻塞或缺信息，不按商品类别枚举字段。基础回归 154 passed / 2 skipped；补充拒绝和依赖恢复后 26 passed。多种待决交互竞争的整体收敛仍需验证，不据此声明全部闭环。
4. in_progress：环境注入、官方 Orchestrator 工具往返、Target 后台运行/Publication 读取已接通。两条开发任务已产生 ENV/ACTION 失败评分；补齐与 API 一致的 verifier 装配并删除预算内工具截断后，继续以冻结代码跑 ALL 官方评分，不把调试轮次作为效果提升证明。

## 验证与完成标准

新增领域/只读工具仅通过注册可被语义层委派并由框架执行；非法领域、越权工具、伪造参数不获执行权。受控写验证同一审批参数绑定、重复操作、结果未知、取消和恢复。实际模型测评与合同测试分开报告。整体迁移未完成前，不以单阶段回归通过声明所有能力闭环。

不修改用户已有未提交文档/评测工作；不新增退货/换货专用 Agent、试题规则或自写 ReAct 循环。阶段完成分别 commit/push。

## 收敛验证记录

- 领域模型必须提交 DomainOutcome；空 requirement 或模型停止不能推导业务成功。字段定义由领域上下文决定，Schema 错误反馈复用框架循环及调用预算。
- 绑定到暂停领域任务的纯文本回答消费 REPLY_PENDING_INPUT；只有明确结构化值走 FILL_PENDING_INPUT。两者共用原 checkpoint 续接，文本不冒充字段事实。
- Framework 模型请求继承 ModelPolicy 的推理配置及输出预算，而非只使用模型名。逐角色请求参数有回归测试。
- τ³ v8 暴露文本续接遗漏导致的 pending state 冲突；失败轨迹保留。修复后仍须重新跑官方完整评分，不据合同测试宣布业务成功。

- Registry 显式拥有可选 planning_shortcuts，未配置快捷目标的环境仅暴露开放委派和目标取消；领域执行预算由 AgentDefinition 提供。
- 审批保存显式 origin_work_item_id，确认/拒绝不依赖队列顺序。已有明确 Action 与等待中的领域任务共享一个待决入口；恢复保留任务依赖。
- PendingApproval 的 CAS fingerprint 覆盖动作、对象、参数、期限、续接目标。持久化与消费合同同步迁移。
- Framework Agent 不再把预算内结构化工具结果剪成 1600 字符前缀；真实溢出返回 CONTEXT_BUDGET_EXCEEDED。未新增通用压缩运行时。
- Target composition 默认装配现有 AnswerVerifier，避免非 API 消费者遗漏校验器后持续只输出模板。
- 当前组合 PostgreSQL/合同回归：186 passed、2 skipped。不是完整仓库全部测试通过的声明。

## 第一阶段记录

- AgentDefinition 拥有领域描述及框架工具执行主体；生产装配按 Registry 创建领域 Worker，不再另维护固定领域 Prompt/Worker 列表。
- ConversationAgent 新增通用 delegate_task（target_agent + objective），保留已有明确查询和受控动作入口。参数仍使用有来源的实体绑定。
- RoutePolicy 从注册领域取只读工具/可选只读 Skill；指定 requirement/Skill 的任务继续使用收窄范围。
- 框架结果适配保留动态选择工具产生的有效事实，验证工具与事实权威绑定；空 requirement 不再掩盖工具失败。
- 回归：224 passed / 2 skipped；另以真实 PostgreSQL 运行框架 Agent 与 cutover 测试，37 passed，覆盖已有子图进程退出恢复用例。没有调用真实规划模型，没有 τ³ reward。
- 剩余边界：开放式委派写入仍不可用；明确业务快捷入口和写执行仍使用现有定义，不能把可注入 Registry 宣称为完整异构环境适配。下一阶段需连同 Action/Flow、执行主体、审批和恢复消费者一起迁移。
