# DialogPilot Target Architecture v2 文档化计划

日期：2026-09-05

## 目标

把已确认的 Conversation Agent、上下文、LangGraph、后台运行、恢复、Memory、
Domain Agent、Tool/Skill/Flow、Verification 与 Publication 边界收敛为：

1. 一份可作为后续实现权威输入的正式架构文档；
2. 一份映射当前代码、包含迁移顺序和可证伪退出条件的实施计划。

## 冻结约束

- `TargetConversationManager` 是会话和权威状态生命周期 Owner，不是 LLM Agent。
- `ConversationAgent.plan()` 吸收并替代独立的 `StructuredTargetCommandRouter` 语义职责。
- Intent Encoder 只提供候选与低风险快速路径，不与 Conversation Agent 重复理解。
- 简单请求采用最短安全路径；只有真正需要领域规划时才调用 Domain Agent，只有真正
  多领域的独立任务才并行派发。
- Agent 可在 WorkItem 能力包络内直接调用原子 Tool 或可选复合 Skill；高风险、跨轮
  写操作进入受控 Flow。
- LangGraph 负责图执行、并行、暂停、恢复和 checkpoint；业务事实、Receipt、审批、
  Flow CAS、Publication 继续由业务存储负责。
- SSE 连接生命周期、后台 Run 生命周期、会话/业务生命周期和模型上下文生命周期分离。
- Transcript 无损保存；Thread Summary 是可重建投影；ServiceEpisode 按需检索；
  单次 Agent 工具循环的局部压缩不回写全局摘要。
- `ConversationAgent.compose()` 只消费已验证结果且按需调用；Publication 是唯一提交和
  交付出口。
- 不新建第二套调度器、恢复引擎、Memory 系统或通用消息总线。

## 交付物

| 阶段 | 状态 | 交付物 | 验证 | 提交 |
|---|---|---|---|---|
| 1 | in_progress | 本计划文件 | 约束与用户结论一致 | 待提交 |
| 2 | pending | `docs/dialogpilot-target-architecture-v2.zh-CN.md` | 责任、数据流、状态与恢复合同完整 | 待提交 |
| 3 | pending | `plans/dialogpilot-target-architecture-v2-implementation-2026-09-05.md` | 映射代码、测试、迁移与退出条件 | 待提交 |
| 4 | pending | 全文一致性和读者问题检查 | 无重复 Owner、无冲突状态、现状/目标清晰 | 待提交 |

## 非目标

- 本次不修改运行时代码。
- 本次不把未实现能力描述为已经上线。
- 本次不删除 v1 实施报告；它继续作为提交 `512d1c0` 的历史证据。
- 本次不选择或引入新的队列、Agent Server、SSE 基础设施依赖；计划中只定义选择条件。

## 完成标准

- 文档能明确回答：谁理解、谁规划、谁调度、谁执行、谁验证、谁回复、谁发送。
- Context 的来源、过滤、预算、压缩和注入边界具有可实现合同。
- 断线、进程崩溃、等待输入、审批、写结果未知、回复已提交但未送达均有唯一恢复 Owner。
- 实施计划中的每个里程碑都有受影响文件、正向合同、迁移步骤、测试、观测和提交边界。
- 读者无需依赖本次对话即可区分 v1 当前实现和 v2 目标状态。
