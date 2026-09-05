# DialogPilot Cooperative Steering 实施计划

状态：in_progress
日期：2026-09-05

## 目标合同

用户在一个 WorkItem/Workstream 仍运行时提交补充、修正或取消后，仅受影响目标的 revision
失效。旧执行在模型、工具和发布安全边界协作式停止；无关并行任务继续。已经完成的读取按原
对象保留，已经发出的写操作以 Receipt/Reconciliation 收口，不能由本地取消改写结果。

## Owner

- Conversation/Workstream state：保存目标 revision 和控制状态；
- Conversation Manager：接受新 turn，解析其影响范围并提交 revision 变化；
- Agent loop：在 model/tool 步骤边界检查 revision；
- Tool Runtime：在真实调用前执行最后一次受信版本检查；
- Publication：提交正式回复前拒绝 stale revision；
- Orchestrator：只传播 typed `SUPERSEDED`，不重新解释用户。

## 实施阶段

1. `completed`：审计现有 ConversationState、Run 并发、Agent hooks、Tool 和 Publication 边界；
2. `completed`：定义最小的目标 revision/control outcome 合同并接入持久状态；
3. `completed`：将共享控制检查接入旧 ReAct 与 framework Agent 的步骤边界；
4. `completed`：在 Tool 与 Publication 提交边界增加原子/权威校验；
5. `in_progress`：实现修正、取消、无关并行不受影响、写结果未知的状态/E2E 测试；
6. `pending`：更新正式架构文档，分阶段 commit、push。

## 非目标

- 不切换到 Agent Server；
- 不强制所有领域 Agent 使用 LangGraph；
- 不按用户句式、商品类别或具体 Tool 编写控制分支；
- 不允许整场 conversation 版本变化无差别取消所有 WorkItem；
- 不把已经发出的远端写请求视作本地可撤销。
