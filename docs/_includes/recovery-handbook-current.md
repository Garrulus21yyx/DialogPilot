## 6B. 子Agent失败后，谁决定下一步 {#worker-recovery}

**可以直接讲：**“子Agent失败后，Runtime先把失败记录成明确结果，让其他独立任务正常完成。如果是可重试读取故障或者无进展，就把原任务、失败原因和已有成功结果交回现有Conversation Agent。主Agent决定换方案、追问还是说明阻塞，再由Compiler和Runtime执行新计划。这里复用的是原来的观察与重规划流程，重试不会偷偷扩大权限，也不会把写操作重新提交一遍。”

### 一次读取失败的完整链路

假设A查物流、B查退货政策，A连接超时而B已经查到政策。OrchestrationRuntime在非写执行边界把异常转成AgentResult，保留异常类型、原因链和retryable信息；B的结果继续进入ResultBoard。依赖A的任务被阻断，B不会因为同波A失败就消失。

`recovery_observations`只从当前计划的READ任务中选取DIRECT/DELEGATED的RETRYABLE_FAILURE，或reason为AGENT_NO_PROGRESS的BLOCKED。`requires_observation`把它们接入TurnRuntime已有的commit_observation/plan_observation路径，Manager也使用同一个判断。提交观察后，主Agent得到原目标、任务范围、诊断与成功同伴结果，选择下一步；新计划仍经Policy/Compiler接受，不绕过WorkPlan合同。

例如主Agent可选择另一个受支持的查询方式、请求缺少的订单信息，或者明确说明物流暂时不可用并先回答政策。Runtime不会根据retryable自动安排同参数重跑；主Agent也不能声称回复结束后仍有后台任务在继续。如果主Agent自身调用失败或新计划不合法，保留已有结果并进入回答/阻塞路径。

### 哪些故障允许这个分支

| 情况 | 当前处理 |
|---|---|
| 非写任务TimeoutError、ConnectionError或可重试ModelInvocationError | 类型化为可重试失败，符合当前READ任务条件时回到主Agent决策 |
| READ任务的AGENT_NO_PROGRESS | 将停滞结果作为观察交回主Agent，仍受已有预算限制 |
| 程序/配置错误、其他不可重试异常 | 终止型失败并保留诊断，不因可用重规划入口就不断重复 |
| 等待批准、等待补答、取消 | 保留原生命周期，不把正常等待当故障 |
| 旧retained失败记录、无新WorkPlan的普通回复 | 不通过新增recovery_observations重复触发恢复；原有assignment修复规则另行保留 |
| 写操作结果未知或写基础设施异常 | 原operation_key、回执与对账处理；逸出的写异常交给原Run接管，不能新建替代写入 |

执行合同损坏、取消和框架中断继续传播，不包装成普通可重试故障。DIRECT执行受声明的timeout_seconds约束；领域Agent保留原流式进展与模型/工具预算。

### SDK重试、重规划和写恢复是三层事

当前`core/framework_models.py`将SDK `max_retries`设为2，处理SDK范围内的瞬时模型传输失败。它不是“整段Agent最多重跑两次”，也不表示所有业务请求自动尝试三次。项目没有另加模型middleware重试或整个Agent回放循环。

主Agent重规划受现有`max_observation_steps=4`默认限制，同时计算观察是否产生新进展。第一次看见某失败记作新观察，之后同目标、参数/能力范围和失败状态重复出现会积累停滞：重复两轮给警告，再重复则停止；仅换尝试ID不算进展。错误原因等观察内容变化可能改变键，硬预算仍负责兜底，不能把这套判断说成完整语义循环识别。

停止会记录OBSERVATION_NO_PROGRESS或OBSERVATION_BUDGET_EXHAUSTED等阶段原因。模型选的替代方案是否有效仍需真实模型任务验证，结构上能回到决策并不保证每次都选对。

## 6C. 修改一个目标，其他待答任务如何保留 {#objective-conservation}

**可以直接讲：**“我还修了多目标续接中的一个状态问题：修改一个任务时，原来整组待答状态会被清掉，理解层又把其他任务都重跑来补偿。现在按任务依赖分支更新状态，修改或取消只影响相关部分，其他任务保留原问题、有效结果和checkpoint。用户之后回答哪一个问题，就只恢复对应任务及其满足条件的下游。”

### 为什么仅保留ResultBoard还不够

成功结果留在Board，并不代表没回答的问题仍可继续。多任务待答还需要requested_fields、suspended_work_items、workstream版本和原checkpoint关联。旧逻辑把pending_interaction整体清掉，会丢掉这些信息；只看active control仍存在发现不了恢复范围已经丢失。

现在ConversationState的`_input_revision_update`先找出变化的control，再用`partition_work_revision`划分受影响依赖分支。保留独立任务的字段、挂起WorkItem和恢复关联；保留的interaction增加版本，只消费旧signal版本。若确实没有剩余字段，才结束该待答状态。不是新建一个目标数据库，也不是把旧状态原封不动永远保留。

### 用两个待答目标举例

A在等用户补订单号，B在等用户补耳机型号。用户取消A，系统只关闭A及依赖A的执行分支，B仍保留“耳机型号是什么”的原问题和原恢复位置。用户下一轮给出型号，理解层只恢复B及符合依赖条件的后续任务，不重跑A，也不让B重新提同一个问题。

如果另有C已经成功查到的产品资料，WorkPlan.unreplaced_outcomes按control revision保留C，ResultBoard继续使用任务/结果配对计算覆盖。新计划只包含B，不代表所有没出现的任务都已经完成；它们可能是已完成结果、仍在等待的目标，或已经明确取消的分支，需要分别读取原状态。

### 状态Owner与消费方一起修改

- **ConversationState：**划分受影响分支，保留独立待答字段、挂起范围和信号版本。
- **StateBoundTargetUnderstanding：**恢复本次明确处理的目标与相关下游，不自动恢复仍未补答的独立同伴；下游是否就绪仍由Board检查。
- **ConversationManager：**待答状态只要发生变化，包括局部保留或纯取消，也处理原执行checkpoint的续接；关闭投影使用同一依赖划分。
- **WorkPlan / ResultBoard：**保留未替换的有效结果、缺结果与等待语义；新计划未提到不等于完成。

当前TurnRuntime为v24-scoped-input-revision，接替故障恢复阶段的v23。旧未发布生命周期checkpoint明确拒绝，避免按新规则执行以前自动恢复全部同伴的计划，没有追加兼容补猜路径。

### 验证到哪一步

f4bd1db报告组合238项、补充59项通过，有重叠，包含PostgreSQL回放；f5fcfa6报告440项通过，含多目标状态与真实PostgreSQL恢复验证。两次测试使用脚本模型，不是付费模型任务成功率，也不能把这几组次数相加作为独立样本量。

验证包括三独立目标修改/取消顺序、依赖影响、状态序列化、只补答另一个目标、原checkpoint恢复和保留结果；故障测试覆盖连接/无进展以及恢复、报告阻塞、规划失败、重复失败。它证明的是**已接受目标在执行、修改和续接中按合同保留**，没有证明模型首次理解绝不漏项，也没有证明自然语言最终回答一定覆盖全部需求。

源码与验证：[application/conversation_state.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/conversation_state.py)、[application/conversation_context.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/conversation_context.py)、[application/execution_progress.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/execution_progress.py)、[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/orchestration_runtime.py)、[application/target_conversation_manager.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/target_conversation_manager.py)、[application/target_understanding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/target_understanding.py)、[application/turn_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/turn_runtime.py)、[core/framework_models.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/core/framework_models.py)、[tests/test_worker_failure_dispatch.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/tests/test_worker_failure_dispatch.py)、[tests/test_objective_conservation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/tests/test_objective_conservation.py)、[plans/worker-failure-dispatch-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/plans/worker-failure-dispatch-2026-09-09.md)、[plans/objective-conservation-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/plans/objective-conservation-2026-09-09.md)。
