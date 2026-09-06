# Agent 自主规划与统一动作治理迁移

## 观察与根因

τ³ 入口仅完成首轮消息探测。换货无法表达不是缺少另一个标签：ConversationAgent 的业务枚举/分支、RoutePolicy 的预选只读工具以及 ActionDefinition 必须绑定 Flow 共同限制了开放能力。治理机制已有 GovernedWriteRuntime，不应另建执行引擎。

## 正向合同

主 Agent 根据 Registry 中的领域描述与可用能力提出开放目标；领域 Agent 在当前授权包络内规划工具。工具事实、执行权限、风险由 Registry/ToolManager 拥有，目标文本不授予权限。明确只读查询继续允许直接执行。写动作由共享治理执行，审批绑定具体参数与对象，未知结果保留对账；业务 Flow 只用于确需持久编排的操作。没有业务后端能力时明确不可用，不把换货转换为退款。

## 分阶段迁移

1. implemented：Registry 领域能力卡、通用目标委派、Schema/编译/RoutePolicy/领域执行接线与回归。新增领域无需业务目标枚举；当前仅开放只读委派。合同回归通过，尚无真实模型收益结论。
2. pending：动作与 Flow 解耦；审批、准备、operation key、Receipt、结果未知和 Publication 消费者迁移。
3. pending：领域模型提出写动作，经共享执行入口暂停、恢复和继续；覆盖目标修正与审批失效。
4. pending：环境可注入，τ³ 工具及政策桥接，完整两条开发任务与官方评分。

## 验证与完成标准

新增领域/只读工具仅通过注册可被语义层委派并由框架执行；非法领域、越权工具、伪造参数不获执行权。受控写验证同一审批参数绑定、重复操作、结果未知、取消和恢复。实际模型测评与合同测试分开报告。整体迁移未完成前，不以单阶段回归通过声明所有能力闭环。

不修改用户已有未提交文档/评测工作；不新增退货/换货专用 Agent、试题规则或自写 ReAct 循环。阶段完成分别 commit/push。

## 第一阶段记录

- AgentDefinition 拥有领域描述及框架工具执行主体；生产装配按 Registry 创建领域 Worker，不再另维护固定领域 Prompt/Worker 列表。
- ConversationAgent 新增通用 delegate_task（target_agent + objective），保留已有明确查询和受控动作入口。参数仍使用有来源的实体绑定。
- RoutePolicy 从注册领域取只读工具/可选只读 Skill；指定 requirement/Skill 的任务继续使用收窄范围。
- 框架结果适配保留动态选择工具产生的有效事实，验证工具与事实权威绑定；空 requirement 不再掩盖工具失败。
- 回归：224 passed / 2 skipped；另以真实 PostgreSQL 运行框架 Agent 与 cutover 测试，37 passed，覆盖已有子图进程退出恢复用例。没有调用真实规划模型，没有 τ³ reward。
- 剩余边界：开放式委派写入仍不可用；明确业务快捷入口和写执行仍使用现有定义，不能把可注入 Registry 宣称为完整异构环境适配。下一阶段需连同 Action/Flow、执行主体、审批和恢复消费者一起迁移。
