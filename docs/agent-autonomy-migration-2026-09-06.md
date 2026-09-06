# 开放领域委派：第一阶段

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

本阶段没有开放领域 Agent 的写工具调用。下一阶段将复用 GovernedWriteRuntime，使 Action 可以独立于业务 Flow，审批绑定实际参数，工具结果未知时对账。待动作提出、等待、恢复及最终发布贯通后，才移除现有开放式写入限制。

τ³ 工具和政策桥接、完整对话及官方评分仍待完成。当前没有新增换货专用 Agent、Skill 或题目分支。
