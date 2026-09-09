## 6A. Compiler、WorkPlan、TaskGraph、ResultBoard 与 LangGraph {#workplan-contract}

**可以直接讲：**“主Agent决定要做什么，RoutePolicy先校验权限和当前状态，Compiler把通过校验的命令编译成WorkPlan。WorkPlan包含任务、依赖和执行规则；TaskGraph是这些依赖形成的图。LangGraph负责调度和保存运行位置，ResultBoard则根据任务合同判断哪些结果有效、下一步能否运行、哪些内容可以先交付。最新版本要求所有图结果绑定本次计划，恢复也只能沿已接受的任务范围继续。”

### 五个名词各自是什么

| 名称 | 项目中具体是什么 | 输入 → 输出 | 为什么保留 |
|---|---|---|---|
| Compiler | application/turn_planning.py里的TurnPlanCompiler，确定性代码 | ValidatedCommandPlan＋状态/Registry/Invocation → TurnPlan | 把模型命令变成可校验的任务与状态变更；它不是另一个规划LLM |
| WorkPlan | application/work_item.py里的不可变数据合同 | WorkItem集合＋primary ID＋WorkPlanPolicy | 固定本次执行内容、依赖、规则与指纹 |
| TaskGraph | WorkItem.dependencies形成的任务DAG | 任务依赖 → execution_waves | 描述业务任务的先后关系；当前没有额外的TaskGraph类或第二套计划存储 |
| ResultBoard | application/result_board.py里的确定性计算 | WorkPlan＋已验证结果＋保留结果对 → ResultBoardSnapshot | 判断就绪、缺项、冲突、完成与部分交付 |
| LangGraph | OrchestrationRuntime装配的StateGraph运行设施 | 图状态＋Send/Command → 节点执行与checkpoint | 提供调度、合并、暂停和恢复机制；业务规则由WorkPlan与应用定义 |

TaskGraph和LangGraph不是同一张图：前者是一次请求的任务依赖，后者是承载不同请求的运行流程。一个依赖边不会自动变成一段新模型Prompt，也不要求为每个任务重新定义一个StateGraph节点。

### Compiler实际编译哪些东西

完整调用关系是 `TurnProposal → RoutePolicy.accept → ValidatedCommandPlan → TurnPlanCompiler.compile → TurnPlan.work`。Policy先检查任务Owner、能力范围、参数绑定和恢复来源；Compiler再次确认state_fingerprint和registry_fingerprint未变化，把command_id依赖映射为实际work_item_id，并生成WorkItem。WorkItem携带Owner、工具/Skill/动作白名单、参数与来源绑定、requirement_ids、依赖、控制模式、状态版本和执行预算。

TurnPlan比WorkPlan大：它还包含路由和待应用的状态变更。普通回复可以有TurnPlan但没有WorkPlan。`primary_work_item_id`是主任务标识，不代表其他任务可被忽略。WorkPlan构造时检查唯一ID、主任务归属、未知依赖和环；编译后不能让模型临时改变执行规则。

WorkPlanPolicy固定五项合同：依赖必须成功且覆盖完整；可提案/写任务按会话审批槽串行；无法满足的依赖产生BLOCKED；旧结果按control revision保留或替换；部分交付只采用可交付的结果。Compiler对纯只读任务选择不串行，对含写能力的任务选择会话串行。枚举名ONE_PENDING_ACTION_PER_CONVERSATION限制的是同波可写任务，领域Worker仍可准备一个含多个独立成员的操作集合。

### 用一条请求串起执行过程

用户说“查配送，结合订单告诉我退货运费，再查一下耳机兼容性，先别退款”。假设规划明确形成三个任务：A查订单，B解释该订单退货费用并依赖A，C查兼容性且独立。WorkPlan的结构波次是第一波A/C、第二波B，但是否实际启动B还要看ResultBoard。

若A返回SUCCEEDED却缺少它声明的必要订单事实，B仍不能启动；若A成功且覆盖完整，B才就绪。A失败时，B被阻断，但C的有效结果可以先交付。这里依赖的含义是成功加覆盖，不是“上游函数返回了就行”。B、C没有完成时也不能因为主任务A成功就说全部完成。

这只是解释任务合同的示例，不是每个输入都固定拆成三个任务；实际计划取决于主Agent原生动作选择与Policy接受结果。业务写入仍需领域准备和批准，依赖DAG本身不授予权限。

### 图结果为什么必须带计划身份

不同计划都可能出现局部任务ID，比如work:1。只按这个ID收集结果，恢复后就可能把旧计划的订单查询当成新计划的完成结果。当前Worker内部仍返回AgentResult，由运行时在执行边界绑定成PlanScopedAgentResult，执行身份为：

`plan.fingerprint : work_item_id : item.fingerprint`

Plan指纹包含任务指纹、主任务和policy指纹；因此规则不同也不是同一份计划。Reducer只接受这种带作用域的图结果：同身份、同内容重放合并一次；同身份、不同内容抛出冲突，不取最后一次覆盖。读取图状态时再次对照当前WorkPlan验证身份，成功后才投影为AgentResult供Board使用。

**需要区分两道检查：**Reducer处理流内身份与重复，读取边界检查该结果是否属于当前计划。一个其他计划的事件不能因为通过Reducer就被Board接受。缓存完成、取消、execute/resume返回路径也走相同检查。ResultBoard接收的是验证后结果，不能误写成“Board本身只接受PlanScopedAgentResult”。裸AgentResult只存在于Worker内部返回边界，不再是图状态的兼容格式。

### ResultBoard的四个判断不能混用

| 属性 | 实际含义 | 不能推出什么 |
|---|---|---|
| complete | 当前计划每个任务已有有效或合成的结果记录 | BLOCKED、失败也可能已有记录，所以不等于成功 |
| coverage_complete | 所需事实/回执无缺项且无冲突 | 证据齐不等于所有Worker成功，更不等于回答准确 |
| task_completed | complete、覆盖完整、保留项有结果且所有结果SUCCEEDED | 仍是任务合同完成，不是外部τ³最终评分 |
| partial_delivery_allowed | 至少有一项可交付，同时还有未完成任务 | 不能把PARTIAL包装成整任务成功 |

`coverage_for(item, result)`按配对的任务和结果计算，不跨计划用同名ID拼接。部分可交付要求SUCCEEDED/PARTIAL、事实或回执、要求满足且无相关冲突。依赖失败按拓扑传播，独立结果保留；当前结果与retained_outcomes都保留自己的WorkItem合同。新control revision只替换对应旧目标结果，不清空其他独立目标。

### 恢复为什么不能“根据当前目标补一份任务”

用户补答或批准时，原等待状态可能已经消费；这时必须把已接受的任务范围继续向下传，而不是根据active goal重新推导工具权限。当前来源是持久等待/批准记录，或resolver已确认的任务范围。目标描述、模型自带的resumed envelope都不能自行扩大权限。新目标修订与旧任务恢复是两条明确的状态路径。

审批执行与完成投影都读取`ConversationState.accepted_approval`的同一份记录。假设批准集合有两个operation_key，只有一个成功结果，不能因另一个缺失就把集合缩成单项并判完成；空集合也不能通过all([])得到“完成”。Manager逐成员对照结果并使用Board覆盖判断，未知效果保留对账状态。

### Checkpoint与旧数据现在怎样处理

当前checkpoint必须包含正确的计划指纹、带作用域结果和显式WorkPlanPolicy。恢复不接受旧指纹，不将policy字典猜成新合同，也不在解码时补缺失policy。新建WorkPlan使用类型化默认值是合法构造，与为旧持久记录补猜是两件事。

旧数据仍保存，但不兼容的执行记录会明确拒绝；本次没有自动迁移或删除旧数据。BusinessObservation也要求生产者给齐恢复字段，投影层不再补齐。即使LangGraph能找到thread，也要通过这些应用合同检查；checkpoint仍不能代替业务账本证明副作用发生。

### 如何验证这次简化

9a50ea1对应报告记录708项组合检查通过，最终补充97项合同检查通过，包含PostgreSQL；另一个扩展检查249通过、4个既有测试调用错误。检查集合有重叠，不能相加。没有新τ³、没有新增模型调用，也不据此提高任务成功率数字。

重点用性质和状态组合验证：不同完成顺序的语义结果一致、重复重放不增结果、同身份冲突拒绝、跨计划结果拒绝、缺批准/缺成员结果不完成、消费等待后权限不扩大、旧checkpoint拒绝。相同结论还要覆盖正常执行、恢复、取消和缓存完成的读取路径。本次网页更新只做文档构建与展示检查，不重复宣称运行了这些应用测试。

源码与验证：[application/turn_planning.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_planning.py)、[application/work_item.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/work_item.py)、[application/result_board.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/result_board.py)、[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/orchestration_runtime.py)、[application/target_conversation_manager.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_conversation_manager.py)、[application/conversation_state.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_state.py)、[infrastructure/langgraph_checkpoint.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/langgraph_checkpoint.py)、[plans/work-plan-single-contract-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/plans/work-plan-single-contract-2026-09-09.md)、[tests/test_work_plan_execution_contract.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_work_plan_execution_contract.py)。
