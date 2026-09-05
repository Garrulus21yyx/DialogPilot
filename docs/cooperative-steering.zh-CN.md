# DialogPilot 运行中修正与取消

## 目标

用户可以在任务运行期间新增目标、修正目标或取消目标。控制粒度是一个独立目标，而不是整场
会话。修正商品任务不会停止并行的退款查询；一次普通追问也不会默认打断已有任务。

## 权威合同

`ConversationState` 保存每个目标当前接受的 `control_id + revision + status`。`WorkItem`
携带不可变的 `WorkControlBinding`。新目标创建 revision 1；修正明确引用原 control，并提交
下一 revision；取消把当前 revision 置为 `CANCELLED`。

Conversation Agent 负责结合对话上下文判断本轮是新增、修正还是取消，并输出结构化计划。
RoutePolicy 验证目标仍然存在且处于可控制状态。Conversation Manager 用 CAS 提交计划；它不
直接停止协程，也不执行工具。

## 执行边界

共享 `WorkControlGuard` 从会话状态 Owner 读取当前 revision：

- 父 LangGraph 在派发前、Worker 返回后和 Evidence 循环边界检查；
- 旧 ReAct 在 model 前后、tool 前后检查；
- framework Agent 的原子 Tool 与复合 Skill 边界检查；
- 普通多步 Skill 在每个外部调用之间检查；
- Workflow 在业务写提交前检查；
- Publication 持有 conversation 行锁时检查，拒绝过时回复提交。

检查失败统一返回 `SUPERSEDED`。Orchestrator 只传播该结果，不重新解释用户含义。

## 调用已经发出时

模型调用不能及时取消时允许其返回，但结果在下一安全边界丢弃。只读 Tool 已发出时，结果按
原对象保留为执行证据，不进入修正后答案。

写操作在取得提交资格前发现 revision 失效则不提交。操作已进入持久 `EXECUTING` 后，不把
本地取消解释为远端撤销；任何异常都进入 `OUTCOME_UNKNOWN / RECONCILING`，通过原
`operation_key` 查询 Receipt，禁止盲目重复写入。

## LangGraph 边界

父图继续负责 WorkPlan、并行、依赖、checkpoint 与 interrupt。长模型—工具循环可以使用
framework Agent/Subgraph；现有 ReAct 通过相同控制检查也能响应修正。是否图化由恢复粒度和
循环复杂度决定，不是所有领域 Agent 的强制要求。

`interrupt()` 表示程序主动等待补充信息或审批。用户在任务运行中发新消息则由现有后台 Run
入口受理为新 invocation；新计划提交对应 control revision 后，正在执行的旧 Worker 在下一
安全边界协作式停止。

## 验收不变量

1. 修正被接受后，旧 revision 不能产生新的有效工具动作或 Publication。
2. 未被引用的并行 control 保持有效并可正常交付结果。
3. 旧对象的读取结果不会被重标为新对象事实。
4. 写提交后的中断只走 Receipt/Reconciliation，不再次提交。
5. 取消计划不伪造成 Tool、Skill 或业务 Flow。
