# 子任务受阻后的交接

状态：implemented / verification_open。当前证据是合同、集成及 PostgreSQL 重开恢复测试，不代表 τ³ 真实任务已完成。

## 责任边界

- SDK / ToolManager：有限的传输重试、工具原始错误与副作用状态。运行层不另套整任务重试。
- 领域 Agent：根据工具反馈修正参数或方法；已知缺用户信息时直接调用 `request_user_input(question)`。
- 框架 Runtime：保存工作消息、有效事实与 Receipt；局部无进展有界停止。
- ConversationAgent.recover：只在开放子任务受阻后调用一次，读取目标、实际错误、已尝试工具、保留事实和未解决证据。决定提出一个有价值的用户问题，或结束本次尝试。不是每轮必调，也不重新生成成功子任务。
- ConversationManager：将问题绑定回受阻 WorkItem 和原 checkpoint。问题是会话决策，原 AgentResult 的失败状态不改成成功或缺输入。
- ConversationAgent.compose / ResponseAssembler：解释实际结果和限制；失败明细与结果进入正常回复组织及验证。成功任务的确定性结果可与追问一并交付。
- Publication：提交正式问题或回复。

## 恢复语义

用户补充信息或给出新处理指令后，经已有 signal、owner、registry、control revision 校验，只恢复目标子任务，带上有效工作消息与事实。成功的并行任务不重跑。新回复不是业务写入授权。

`finish` 结束本次尝试，不承诺后台继续重试或已经转人工。实际人工提交仍走现有受控能力；不能用恢复模型的一句话代替工单 Receipt。当前没有新增“自动换 Agent 再试”的循环。

恢复模型不可用、输出非法、重复/越界任务 ID 时，原执行结果与进度保留，正常回复组织继续说明限制。未完成写入结果仍由对账路径处理，不进入恢复重试。

## 审批期间的追问

同一审批 checkpoint 内已暂停的工作仍按原审批继续。另一个执行线程产生的追问有独立 PendingInteraction，不能加入旧审批的 suspended_work_items。

审批和澄清可同时保存，但当前交互先完成澄清，再处理明确审批；τ³ 客户端也先绑定澄清 signal，不把“是的，我指的是这个”分类成批准。补答不修改原审批参数、operation key 或 checkpoint。

## 验证范围与尚待证据

测试覆盖错误明细投递、无隐式重跑、独立成功保留、非法恢复输出、普通追问不重写、PostgreSQL 关闭重开后补答、审批中追问—补答—批准及仅一次真实测试工具写入。

本轮定向回归 197 passed（含真实 PostgreSQL）；新增 provider 输出工具接线测试 1 passed；τ³ adapter / binding 回归 16 passed。这些是不同范围的工程测试，不是官方任务得分。测试执行时工作区仍包含上一轮未提交的 ToolRejected 和 Langfuse session 接线改动，本提交未将它们混入。

真实模型是否总能提出有效恢复问题、问题中事实性前提的最终核验、完整 τ³ 换货成绩仍需独立评测。不得把本轮测试计数写成业务任务成功率。
