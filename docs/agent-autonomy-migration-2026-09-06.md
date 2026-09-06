# 开放领域委派与动作治理

目标是让领域能力通过注册扩展，而不是每增加一个客服诉求就修改主 Agent 的业务枚举。此次复用现有 ConversationAgent、RoutePolicy、WorkPlan 和 create_agent，没有新增运行时。

## 当前合同

主 Agent 读取 Registry 的领域描述、工具及 Skill 范围，以 `delegate_task(target_agent, objective)` 提出开放目标。已有明确查询仍可直接执行，不强制多 Agent。

RoutePolicy 校验领域、实体来源和权限，形成只读执行包络。领域 Agent 自主选择该范围内的工具及复合 Skill。未预先声明具体 requirement 时，实际工具结果仍按 Registry 中的权威来源保留为 Fact；工具失败不能因为 requirement 为空而变成成功。结果继续经过现有 ResultBoard、回复检查和 Publication。

领域描述及框架执行主体归 AgentDefinition；生产装配从 Registry 创建 Worker。同一领域无需在规划层和运行层分别添加名称分支。新增业务后端仍须注册真实 Tool、Schema 和权限；自然语言目标不会创造后端能力。

## 验证

- 三个不同注册领域走相同的规划—策略—编译—框架工具调用链，并保留来源信息。
- 验证未知领域、委派字段缺失、写工具隔离、动态事实、工具失败和错误权威。
- 相关回归 224 passed、2 skipped；真实 PostgreSQL 补跑框架及 cutover 测试 37 passed。

这些是合同和集成测试，不是模型准确率，也不是 τ³ 任务完成率。

## 尚未完成

第二阶段已完成动作合同解耦：`ActionDefinition.flow_ref` 可为空；准备、执行、续接使用统一 Action 命令。独立动作使用 `ControlMode.ACTION`，明确业务流程继续使用 WORKFLOW。两者复用同一个 GovernedWriteRuntime、审批和 PostgreSQL 操作账本。写执行测试同时覆盖两种模式；无 Flow 的准备—确认—原线程恢复已经通过测试。当前领域 Agent 提出动作的接线仍在进行。

领域 Agent 现在可以选择注册 Action。框架工具只提交动作建议，由 TargetActionPreparation 获取前置状态和版本；ConversationState 保存具体参数与被暂停的目标。确认后仍由 GovernedWriteRuntime 执行，Receipt 作为依赖结果交回领域 Agent。模型不直接执行写入。

框架原生 DomainOutcome 返回明确终态和 typed MissingInput；缺信息终止本次模型循环。确认被拒绝时，原目标及其依赖不继续，无关暂停目标可以恢复；确认时再次验证原目标 revision。生产装配和写工具执行主体统一来自 Registry。

第三阶段回归：真实 PostgreSQL 154 passed、2 skipped；新增拒绝与依赖顺序检查后定向 26 passed。已有大 HTTP 场景的旧 fixture 仍存在退款资格候选被最终回复验证拒绝的问题，不能将该用例记为通过。多类待决交互竞争与完整外部环境评测仍在进行。

τ³ 工具和政策桥接、完整对话及官方评分仍待完成。当前没有新增换货专用 Agent、Skill 或题目分支。

## 环境装配与收敛

Registry 的 planning_shortcuts 决定主 Agent 可用的既有快捷目标；新环境可只启用通用委派。AgentDefinition 的 timeout_seconds/max_model_calls 决定领域预算，不再用固定四轮限制所有开放任务。Encoder 可在实验装配中明确关闭，不能把旧业务分类器自动当作新环境分类器。

审批包含显式动作来源目标，等待队列与来源不混用；拒绝来源目标不取消无关任务。完整参数、对象版本和动作身份进入审批状态 fingerprint。组合回归 186 passed、2 skipped。

评测桥接位于 evaluation/tau3_full_adapter.py 和 evaluation/tau3_tool_binding.py：前者只做协议往返，后者注册官方工具元数据及政策。真实调用经官方 Orchestrator 执行；Target 保留原审批、操作账本、回复验证和 Publication。基准没有远端 operation-key 查询或原子 CAS，适配器只能记录已观察到的结果，未知结果不会伪装为可重试失败。

当前完整评测仍未通过，不能以“工具已接通”宣称退换货业务闭环已验证。

## 领域终态与文本续接

开放任务通过 create_agent 原生结构化输出 DomainOutcome 表达 SUCCEEDED、NEEDS_USER_INPUT 或 BLOCKED；字段、问题和结果由领域模型生成，工具事实仍从实际 artifact/Receipt 适配。没有终态时返回 DOMAIN_OUTCOME_MISSING，不能因 requirement 为空标记成功。

PendingInteraction 接受两类不同输入：结构化字段经精确绑定消费；明确引用原 interaction 的纯文本回复恢复暂停的领域目标，由 Agent 理解回答、修正或缺失信息。纯文本不会直接填入某个字段，旧信号只消费一次，后续仍缺信息时产生新追问。

框架模型装配使用既有 ModelPolicy.request 的完整参数，包括推理模式和实际输出预算。未新增供应商适配、执行循环或业务特化流程。

## 当前对话与任务进度的不同续接

Target 当前窗口改用已有 PostgresMemoryProjectionReader：检查 PostgreSQL 源水位与缓存投影，投影未覆盖时读取正式 Transcript；不把 Redis 本身视为错误，也不建立第二套会话事实。当前请求从历史窗口排除，状态与原因码一并传入上下文。

暂停领域任务恢复时，编译后的 WorkItem.continuation_of 绑定原任务及同一 control 的下一 revision。LangGraph 从原 checkpoint 读取对应 AgentResult 的未过期事实，并按续接目标传入；新目标不继承这些结果。已完成检查保留原 subject、来源和观察时间，不冒充本轮实时查询。这里验证的是结果级任务进度复用，不声称所有子 Agent 内部消息已经跨轮保留。

回归：相关 PostgreSQL、控制、框架、上下文测试 139 passed；官方评测适配诊断 2 passed。τ³ v11 的 task 0 完成官方评分但 reward=0（必需写动作未完成）；task 1 用户模拟器返回无内容消息导致运行中止。完整业务验证保持开放。

## 评测后续证据（非同预算成绩比较）

v12 的协议日志确认：领域输出在 1024 tokens 截断；用户模拟器在 512 tokens 截断且正文为空。ModelPolicy 的 completion floor 现对全部推理模式生效，默认配置不改变；评测脚本通过显式参数记录预算对照。

v13 使用 4096 输出预算，task 0 官方 ALL reward=1，但最终表达受事实误归并影响，不能算完整质量闭环。task 1 在带追问的审批文本处中止。适配器现在将无法绑定为明确决定的文本原样交给主链，既不授予审批也不当作传输异常。

工具查询结果带 query_ref（工具、参数和授权主体的稳定标识）；未提供业务聚合主体时，不再把整个 WorkItem 当作所有查询的共同事实主体。它标识查询而非推断出的业务实体，重复同一查询的矛盾值仍进入冲突检查。
