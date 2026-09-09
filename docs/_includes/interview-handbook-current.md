# DialogPilot 面试追问：原理、源码与技术取舍

> 2026-09-09校准至9a50ea1。保留框架、后端、RAG与简历追问，并同步操作集合、策略受众隔离和工具字段展示。每题先练短答，再展开源码与验证边界。Q131—Q134讲结果展示简化；Q135—Q142讲WorkPlan、Compiler、TaskGraph、ResultBoard与LangGraph。

## 1. 项目定位与架构防守

### Q1：用一分钟介绍项目，怎么避免报技术栈？

**短答：**我做的是智能电商客服，支持政策咨询、订单查询与售后处理，重点解决多轮需求理解、跨来源取证和业务操作协作的问题。系统把请求编译成有依赖的任务，领域Agent在权限内取证，写操作经审批与对账，回答依据证据核验后持久交付。

**展开：**用“查配送、问拆封能否退、暂不提交”串起来：配送是实时工具事实，能否退需要政策条件，暂不提交约束执行。多Agent分别处理领域任务，ResultBoard保留缺失和部分成功。突出你的工程贡献是主从职责、上下文、证据和执行边界的设计，不是把几个Prompt连起来。

**继续追问：**若问业务效果，分别报检索开发对照和业务验收，不拿内部verified代替真实完成率。入口：[application/target_chat_application.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_chat_application.py)。

### Q2：哪些是自己实现，哪些来自框架？

**短答：**模型调用、消息协议、标准工具循环和图持久化复用框架；任务合同、权限、动作审批、事实与证据归属、结果汇总、发布和评测适配是项目实现。

**展开：**LangChain的create_agent提供模型—工具循环，项目把WorkControl、结果归档、交互边界和预算接成middleware。LangGraph提供Send和checkpoint，项目决定哪些任务就绪、哪些可以并行以及恢复时哪些进度仍有效。这样个人贡献能定位到输入输出与不变量，不必声称自研模型或调度底层。

**继续追问：**给一个难点：超时无法证明退款未提交，需要业务回执/对账而非框架retry。源码：[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)、[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)。

### Q3：为什么不是一个大Agent拿全部工具？

**短答：**单Agent适合简单任务；本项目的复合客服涉及不同事实、权限和等待状态，分领域能缩小上下文与工具范围，并允许独立任务并行和局部恢复。

**展开：**一个大Agent同时看到账号、订单、政策和写操作，更难追踪哪项需求完成了。主Agent直接处理简单读取，将完整业务修改目标委派给领域Worker，后者调查条件、补齐字段并准备动作，ResultBoard汇总。代价是更多上下文转换、可能重复取证和额外模型调用，所以明确查询允许DIRECT绕过Worker，不是所有请求都启动六个Agent。

**继续追问：**不能仅凭架构复杂声称更强；应与同工具同模型单Agent做任务成功率、成本和遗漏率对照。源码：[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/orchestration_runtime.py)。

### Q4：代码目录为什么分application和infrastructure？

**短答：**前者定义业务含义和合法状态，后者把它接到PostgreSQL、框架和模型协议，降低替换技术时对业务语义的影响。

**展开：**WriteOutcomeStatus定义COMMITTED/NOT_COMMITTED/UNKNOWN是应用合同；如何用SQL CAS写记录属于基础设施。若HTTP超时，上层不能直接宣称退款失败，因为真实业务结果仍由工具/对账确认。不是所有文件都严格无依赖，项目也有历史模块，所以讲实际装配而不是宣称完美六边形架构。

**继续追问：**换数据库主要改Store adapter与迁移；合法转移、审批绑定和回执语义仍要保持。源码：[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)、[infrastructure/postgres_target_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_target_runtime.py)。

### Q5：三个图会不会是重复造轮子？

**短答：**Run生命周期、轮次处理、任务依赖和Worker内部工具循环处于不同粒度，承担不同恢复边界。

**展开：**Run负责谁持有请求及最终状态；TurnRuntime负责准备、执行、提交进度和组织答复；OrchestrationRuntime按WorkPlan派发；create_agent负责一个领域任务里的下一次工具选择。主图不重新解释工具输出，Worker也不私自修改全会话审批。层数的合理性取决于职责，而不是图越多越好。

**继续追问：**一个简单查询可走DIRECT，成本不等于所有层都各调用一次LLM。源码：[application/turn_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_runtime.py)、[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)。

### Q6：为什么选PostgreSQL＋Redis，没有直接全放Redis？

**短答：**请求、版本、审批和发布需要事务约束与可恢复事实记录；Redis提供快速窗口和可重建投影，不能成为另一套业务真相。

**展开：**PG保存Conversation、Invocation、Knowledge版本、操作记录和答复。Redis丢失可由事实重建；投影有watermark/status，不能因缓存空而认为用户没有待审批动作。PG也承担pgvector与词法检索，减少额外服务，但SQL词法统计与向量检索仍需性能验证。

**继续追问：**是否绝对强一致？只能说明具体事务与CAS边界，跨业务工具不能凭PG事务实现分布式原子提交。源码：[infrastructure/postgres_conversation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_conversation.py)、[infrastructure/target_turn_context.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_turn_context.py)。

## 2. 意图识别经典范式与当前选择

### Q7：意图识别有哪些经典范式？

**短答：**常见有规则、传统监督分类、预训练编码器分类、语义检索/原型匹配、LLM分类或原生动作选择，以及分类器与LLM的级联。选型要同时看标签稳定性、数据量、多轮上下文和拒识风险。

**展开：**规则精确且便宜，但维护复杂、泛化弱；TF-IDF＋线性分类器适合明确词面和低成本基线；BERT类编码器能学习语义边界但依赖可靠标签；embedding与样例近邻适合增量类别但相似不等于业务可执行；LLM擅长复合需求和上下文，成本与稳定性更难控。级联让简单高置信输入由小模型处理，其余由主Agent理解。

**继续追问：**本项目主路径是带状态的原生动作选择，可选Encoder只分领域且默认关闭。经典候选不是全部在线同时运行。源码：[application/target_understanding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_understanding.py)、[application/target_encoder_understanding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_encoder_understanding.py)。

### Q8：为什么不用关键词规则完成路由？

**短答：**客服表达里否定、纠正和追问决定语义，关键词只适合确定性格式与结构化信号，不能承担全部理解。

**展开：**“不是退款，是问发票”“退款先别办，查一下配送”都有退款词，却不能发起退款。规则适合校验interaction_id、字段类型、目标版本与显式审批信号；自然语言“好”要结合当前等待状态和问题理解。项目StateBound路径处理已绑定的结构化状态，其他表达交给主Agent。

**继续追问：**不用规则是不是完全依赖LLM？权限、状态转移、参数schema仍由确定性程序控制。源码：[application/deterministic_resolution.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/deterministic_resolution.py)、[application/target_understanding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_understanding.py)。

### Q9：TF-IDF、BERT分类和向量近邻有什么区别？

**短答：**TF-IDF强调词频统计；BERT分类通过监督学习直接区分类别；近邻方法比较输入与已有样例或标签描述的语义距离。

**展开：**TF-IDF低成本且可解释，弱点是不同说法；BERT分类头学到的是训练标签分布，能微调但会受类别不平衡和模板重复影响；近邻加新类容易，但阈值受向量模型和样例密度影响，相近意图可能互相吸引。相同backbone也能分别用于句向量检索或分类，不能因为叫BGE就以为这里一定跑embedding nearest neighbor。

**继续追问：**本项目当前TargetDomainEncoder是AutoModelForSequenceClassification＋softmax，不是线上KNN。源码：[infrastructure/target_domain_encoder.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_domain_encoder.py)。

### Q10：为什么Encoder只选领域，不直接预测工具？

**短答：**领域比具体动作更稳定，而且动作需要参数、上下文、权限和审批，分类标签不足以证明可执行。

**展开：**“退款怎么样了”可以归billing_refund，但可能查到账、问政策或继续前轮审批。当前分类接受只产生DELEGATE_TASK，允许领域Agent在原Policy内取证和提案；业务写仍经批准流程。相比旧能力分类器，这减少模型要同时学标签与动作授权的耦合。

**继续追问：**这样节省的是一次主规划机会，不是省掉全部LLM；领域Worker仍需推理。源码：[application/target_encoder_understanding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_encoder_understanding.py)。

### Q11：当前Encoder为什么不开启？你是不是没训练好？

**短答：**训练与接线已经有实现，但最新候选未表现出可靠领域区分，因此保持关闭。我会如实把实现完成和效果达标分开。

**展开：**审校数据固定试训的中英文模型都只预测billing_refund；后来固定权重在四份独立校准/评测子集各5/35正确、macro-F1=.0357，仍未得到合法接受阈值。不能说只是阈值过严，也不能把多数域命中当有效路由。当前主Agent本来就承担未接受请求，因此关闭候选不需要造新业务分支。

**继续追问：**下一步先审数据来源、标签边界、类别/语义族分布与学习动态，固定其他变量做对照，不能保证只加epoch就解决。证据：[domain-encoder-reviewed-trial-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__domain-encoder-reviewed-trial-2026-09-08.zh-CN.md.txt' | relative_url }})。

### Q12：softmax最高就可以接受吗？阈值怎么选？

**短答：**最高概率只是在已知类别中相对最大，并不保证分布外正确。需要拒识类、逐类阈值、与次高/DEFER的margin及独立数据校准。

**展开：**当前比较top domain与max(次高域,DEFER)，margin至少.08；manifest约束启用类的校准/开发接受数量与精度。训练/校准流程还有风险覆盖与Wilson下界诊断，不能把加载器和所有实验门槛混成一个常数。候选全部拒绝时接受精度未定义，应同时报告覆盖率，不能报“零错误所以100%准确”。

**继续追问：**阈值提高通常降低覆盖，但不修复分类语义；先画风险—覆盖曲线。源码：[application/domain_encoder.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/domain_encoder.py)、[evaluation/domain_calibration_audit.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/evaluation/domain_calibration_audit.py)。

### Q13：多轮意图的输入怎么构造，怎样防泄漏？

**短答：**输入包含当前消息和此前角色化历史、当前目标，不使用未来消息或整段对话的最终摘要作为当前轮标签依据。

**展开：**TargetDomainEncoder渲染history/objectives/current_user，训练推理共享输入合同。相同“是”在不同前文是不同样本，不能只对当前句去重。split按原对话或语义族划分，模板替换和翻译关联也要识别。摘要输入未校准时当前入口会defer，不拿原有阈值强吃新输入类型。

**继续追问：**知识类判断不等于完整检索query，仍需保留具体对象、否定、日期和条件。源码：[application/encoder_input.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/encoder_input.py)、[infrastructure/target_domain_encoder.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_domain_encoder.py)。

### Q14：当前主 Agent 如何规划，为什么收缩了它的职责？

**短答：**主 Agent 负责自然对话、读取信息、知识查询和领域委派；新业务写操作由领域专家调查与准备，已绑定审批仍由原状态续接。

**展开：**`planning_actions` 生成当前可选工具 schema，Provider 通过 `bind_tools` 接收原生调用，action_proposal 转成内部提案后再过 Policy 和 Compiler。没有工作时可以原生文本结束，不要求造一个 reply 工具。退款、改地址、取消订单、冻结账户不再是主 Agent 的直接准备快捷入口。用户目标明确就能委派，不必先在主层收齐所有业务字段。

**继续追问：**这不是去掉业务能力，而是把准备权集中到领域任务。主 Agent 保留适用业务政策，领域保留操作前置条件；缩小职责本身没有证明审批展示已正确。源码：[application/conversation_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_agent.py)、[application/conversation_actions.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_actions.py)。

### Q15：意图数据选什么？BANKING77、CLINC、Bitext能直接拿来吗？

**短答：**它们可作为识别或OOS基线的来源，但标签体系和客服目标不同，必须映射、审阅和按来源隔离，不能直接当项目六域Gold。

**展开：**历史500分层集中的意图层使用BANKING77/CLINC子集，标注是auto_mapped。Bitext是英文客服意图来源，但单轮样本不证明多轮理解。CSDS是中文客服对话/摘要资源，不能拿全篇摘要标中间轮而泄漏未来。项目独立双语合成样本能否定当前候选，但不等于真人客服泛化证明。

**继续追问：**新数据应覆盖纠正、否定、并存、撤回和短回复，并审核相邻领域边界；训练行数不等于语义多样性。证据：[evaluation-500.zh-CN.md]({{ '/assets/handbook/evidence/docs__evaluation-500.zh-CN.md.txt' | relative_url }})、[domain-encoder-next-method-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__domain-encoder-next-method-2026-09-08.zh-CN.md.txt' | relative_url }})。

### Q16：few-shot和微调你怎么取舍？

**短答：**few-shot适合快速验证指令与边界，微调适合稳定任务和足够可靠数据，两者都要与无示例基线比较，并检查污染与成本。

**展开：**项目规划示例对照有条件污染，未采用；Encoder微调未过验收；reranker小规模微调24/40→24/40也没有收益，保持暂停。这些结果说明不能用“做了训练”替代“解决了问题”。固定示例增加每次输入成本，动态示例还引入检索和泄漏风险；微调增加数据/模型版本及重新校准成本。

**继续追问：**要先证明错误来自可学习边界而非数据标错、候选根本缺失或错误接口。证据：[planning-evidence-selection-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__planning-evidence-selection-2026-09-08.zh-CN.md.txt' | relative_url }})、[rag-reranker-finetune-2026-09-07.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-reranker-finetune-2026-09-07.zh-CN.md.txt' | relative_url }})。

## 3. 框架选型与多Agent机制

### Q17：为什么同时用LangChain和LangGraph？

**短答：**LangChain提供标准Agent循环与工具/模型接口，LangGraph提供应用任务图与持久恢复，两者在不同层协作。

**展开：**TargetFrameworkAgent调用create_agent并装middleware；OrchestrationRuntime创建StateGraph，用Send按依赖分发，TurnRuntime保存轮次阶段。create_agent本身也构建图，所以是统一框架基础上的不同抽象。项目不依赖框架解释审批或证据权威，这些仍是应用合同。

**继续追问：**为什么不用手写async循环？可以，但消息配对、工具批次、中断、回放和middleware生命周期要自己维护；选择框架减少通用机制维护，不消除业务复杂度。[官方Agent说明](https://docs.langchain.com/oss/python/langchain/agents)。源码：[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)。

### Q18：具体用了哪些LangGraph API？

**短答：**StateGraph定义状态与节点，条件边决定下一步，Send派发工作，interrupt/Command支持暂停续接，PostgreSQL checkpointer保存执行位置。

**展开：**工作图initialize后算ready wave，发送execute_work_item，merge_results后继续调度、await_resume或finish。聚合结果使用明确合并逻辑，避免并行更新覆盖。`thread_id`是加载正确checkpoint的关键；恢复同一工作位置还要验证任务控制版本，不能只拿任意thread继续。

**继续追问：**interrupt后可能重新进入节点，之前的副作用不能无保护执行；细节见[官方interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts)。源码：[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/orchestration_runtime.py)、[infrastructure/langgraph_checkpoint.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/langgraph_checkpoint.py)。

### Q19：PydanticAI、直接SDK、其他Agent框架为什么没统一替代？

**短答：**比较点是需要的控制面和迁移成本。当前图与Worker已依赖LangGraph/LangChain生命周期，重写没有已证明收益；PydanticAI只在受限grounded结构化边界保留。

**展开：**直接SDK适合小而固定的单轮/循环，自己负责工具配对和恢复；PydanticAI强调类型化输出与依赖注入，适合明确结构结果；协作型框架可快速表达角色，但仍需核对其状态、失败和审批语义。requirements有PydanticAI不代表主/chat用它驱动：GroundedAnswerGenerator在独立生成/评测路径，主回答经ResponseAssembler。

**继续追问：**迁移清单包括消息/工具schema、回调、checkpoint兼容、middleware顺序、异常分类和回归，不只是替换Agent构造函数。源码：[mcp/grounded_answer_generator.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/grounded_answer_generator.py)、[api/main.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/api/main.py)。

### Q20：为什么不用多个Agent互相辩论来做客服？

**短答：**客服需要可确认的事实和操作，不同角色重复争论不能创造订单回执，反而可能共享同一错误。

**展开：**本项目按领域/权限划分Worker，共用框架循环；任务间通过依赖结果和FactRecord传信息。Verifier做表达支持性检查，不扮演全知仲裁。需要比较替代方案时，用同模型预算的单Agent、主从调度和辩论方案测真实完成、成本与误操作，而非以对话长度评价智能程度。

**继续追问：**multiagent的收益主要来自职责隔离和可控协作，不能在没有消融时宣称模型能力必然变强。源码：[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/orchestration_runtime.py)。

### Q21：如何防止Worker越权调用别的领域工具？

**短答：**模型可见工具先按领域与任务裁剪，执行时ToolManager再次校验可信principal和允许集合；提案也要经Policy。

**展开：**仅在Prompt里写“不能退款”不够。`tools_for_agent`受allowed_tool_ids约束；StructuredTool注入可信context而不是让模型填tenant/user。动作prepare检查action是否属于当前work envelope，再产待审批结果。读权限和写授权分开，模型知道工具名也不构成授权。

**继续追问：**动态裁剪是减少误选和token，不是唯一安全边界；执行检查不能省。源码：[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)、[mcp/tool_manager.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/tool_manager.py)、[infrastructure/target_action_preparation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_action_preparation.py)。

### Q22：并行任务为什么不会把结果合乱？

**短答：**图结果先按计划与任务指纹绑定、合并和校验，再由Board按局部ID/Owner检查配对；依赖关系而非完成先后决定就绪。

**展开：**调度只给Worker声明依赖的结果，事实有requirement和来源。测试枚举三个任务结果的所有排列，验证snapshot相同；失败只阻断依赖者，独立成功可保留。写任务因单审批槽串行，不能把读取并行策略无条件复制到退款。

**继续追问：**并行减少关键路径时间但可能增加资源竞争，要测墙钟时间、池等待和p95，不仅看async语法。源码：[tests/test_target_orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_target_orchestration_runtime.py)。

### Q23：怎么检测Agent无进展，不是单纯限制轮数？

**短答：**有界预算是最后防线，项目还用进展middleware和证据请求去重防止没有新事实的循环。

**展开：**工作图对NEEDS_EVIDENCE记录已经请求的requirement/provider；补充后事实未变化就返回缺证据状态。Worker有AgentProgressMiddleware、模型/工具调用限制和timeout。它们解决不同问题：重复调用、状态不变、资源耗尽和外部慢响应。实际按观察身份统计：连续两轮无新观察提示调整，提示后仍无进展结束当前段；成功知识按证据项而非query改写判定新颖性。具体例子见Q99—Q102，不套用GUI项目的Monitor/推理升级名称。

**继续追问：**相同工具参数不总是重复错误，例如对账可能合法；要结合效果和阶段。源码：[infrastructure/target_agent_middleware.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_agent_middleware.py)、[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/orchestration_runtime.py)。

### Q24：框架默认retry为什么关掉，失败怎么恢复？

**短答：**避免SDK、Agent和Run多层重试相乘，统一暴露可重试模型错误，由已有Run恢复边界处理；业务写另走回执和对账。

**展开：**framework_model设置max_retries=0，异常携带stage和retryable。归档、预算、模型协议和业务结果未知不能统一“再来一次”。重试模型生成可能安全，重复退款可能不安全；恢复需保留已完成工具结果和旧请求身份。超时后不能根据没有收到回复推断没有提交。

**继续追问：**也不是永远不重试，要有错误类型、次数/时间预算、状态恢复点和完整成本统计。源码：[core/framework_models.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/framework_models.py)、[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)。

## 4. 上下文、记忆与压缩

### Q25：上下文管理与记忆管理有什么区别？

**短答：**记忆决定保存什么、来源和有效期；上下文管理决定这次模型调用看到哪些信息、以什么顺序和预算进入。

**展开：**PostgreSQL里的会话事件、用户事实、ServiceEpisode可以比窗口长得多。TargetTurnContext加载相关近期消息、摘要和证据，Worker再按任务范围组装。摘要是历史理解的辅助表示，不能变成当前订单事实。缓存、长期存储和模型输入各自拥有不同生命周期。

**继续追问：**更多历史不一定更好，旧事实可能过期，模型也可能被相似旧任务带偏。源码：[infrastructure/target_turn_context.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_turn_context.py)、[application/service_episode.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/service_episode.py)。

### Q26：你的上下文预算具体算哪些部分？

**短答：**按每次实际模型输入计算system、工具schema、当前任务、历史与工具结果，并预留输出和协议空间。

**展开：**TargetFrameworkAgent先构建tools/system并估计overhead，再准备任务prompt。ContextBudgetManager允许删明确声明可裁剪的历史列表；删完仍装不下就返回预算超限。不能只把history限制为N条，因为一条工具结果可能几十页，工具定义本身也占token。当前估计器有近似误差，因此有protocol reserve，需用真实usage校准。

**继续追问：**摘要模型本身也受预算；把超长历史全丢给摘要模型只是把溢出移动了。源码：[application/context_budget.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/context_budget.py)、[core/provider_context_budget.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/provider_context_budget.py)。

### Q27：工具结果为什么归档，不直接截前几千字？

**短答：**任意截断可能删掉金额、例外和执行回执。归档原始结果后给有界引用，模型能按需回读，原始事实仍完整。

**展开：**ToolResultPersistence存artifact/content，将引用写回状态；ContextCompaction在知道整个工具批次和prompt预算后决定清理或卸载。read_tool_result读取原快照，可按offset或evidence_id翻页，不重查订单、不再执行退款。归档按可信租户、用户、会话和任务scope隔离。

**继续追问：**如果只提供不可发现的opaque ID模型不会用，所以pointer保留结果状态和证据目录入口。源码：[infrastructure/target_result_archive.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_result_archive.py)、[tests/test_archive_evidence_navigation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_archive_evidence_navigation.py)。

### Q28：摘要到底保留什么，如何验证没丢？

**短答：**保留用户约束和否定、当前目标、完成/未完成操作、不确定结果、待决事项及结果引用，同时保护最新工具批次和当前任务。

**展开：**结构检查保证AI工具调用与返回成对、原文未被修改、原始归档可恢复、预算不过限。语义评审检查required_preserved、no_invention、authority_preserved、progress_preserved。已保存开发报告16次都通过，但使用合成历史和模型judge，不是长业务E2E或独立人工保证。

**继续追问：**摘要遗漏怎么定位？查compaction_records的原始引用，比压缩前后对同一约束的表达。源码：[scripts/run_context_compaction_eval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/scripts/run_context_compaction_eval.py)、[tests/test_target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_target_context_compaction.py)。

### Q29：为什么保护最新工具批次，超大结果怎么办？

**短答：**刚执行的动作与效果是下一步判断的直接依据，优先保留；只有保护后缀本身也超过预算才卸载大结果。

**展开：**代码先清理旧ToolUses，再决定摘要截点，不把最新AI tool_calls与多个ToolMessage拆开。若后缀还过大，按结果大小转换成可回读pointer，直至能装入；仍不够则类型化预算失败。这样避免旧历史太多时过早把新证据全部变成只有引用。

**继续追问：**不是总保留最后三条消息，工具批次大小可变，需要按call关系保护。源码：[infrastructure/target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_context_compaction.py)。

### Q30：归档或摘要失败时会不会丢数据？

**短答：**归档失败时保留当前可用原始结果并停止该段；摘要失败不覆盖原历史，不把“失败提示”当作摘要继续推理。

**展开：**ToolResultPersistence记录archive_failed，before_model结束段，保留inline artifact和错误信息；并行失败的结果通过状态合并留存。StrictSummarization拒绝空、截断或超预算输出。原始存储是证据来源，压缩消息是模型视图，两者不能相互替代。

**继续追问：**这保证失败可解释，不代表外部业务回滚。源码：[infrastructure/target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_context_compaction.py)、[tests/test_target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_target_context_compaction.py)。

## 5. RAG原理与工程追问

### Q31：RAG离线和在线分别做什么？

**短答：**离线准备可追溯资料和索引；在线形成问题、检索、排序、打包、生成并核验。

**展开：**离线SourceRevision→chunk/offset→embedding/词法项→generation projection→索引→激活。在线query带身份和范围，经过Dense/Lexical、RRF、精排与packer到EvidencePack，再进入实际ToolMessage。在线可见证据不能靠离线scorer偷偷补齐，最终来源版本还需要复验。

**继续追问：**更新知识要考虑缓存和正在使用的旧revision，不能只重新embed。源码：[infrastructure/postgres_knowledge_store.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_knowledge_store.py)、[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_retriever.py)。

### Q32：BM25和向量检索为什么混合？

**短答：**词法善于精确术语和编号，向量善于异词语义，客服同时需要两者。

**展开：**“寄回运费”与“退货邮费”需要语义补充，产品编号和渠道名则不能只靠近似语义。BM25通过IDF、词频饱和和长度归一化排序；Dense用学习到的向量空间。两者都不能单独保证例外与否定推理正确，所以还需要多证据覆盖和生成核验。

**继续追问：**PG ts_rank_cd不是BM25；当前Knowledge声明PG_BM25_ZH_V1，代码用SQL统计与打分。源码：[infrastructure/hybrid_retrieval_backend.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/hybrid_retrieval_backend.py)。

### Q33：为什么用pgvector，不用独立向量数据库？

**短答：**项目已有PostgreSQL管理身份、版本和来源，在同一存储体系中做向量与词法检索便于保持来源约束，部署也更简单。

**展开：**独立向量服务在大规模检索、分片、混合索引等方面可能更合适，但会增加来源数据库与索引的一致性、运维和故障面。本项目先用PG满足当前规模；优势是架构和部署取舍，不是未经对照就声称性能更高。PG BM25查询统计也可能是热点，需要看执行计划。

**继续追问：**迁移向量库时仍需保留generation、filter、provenance、typed outcomes和回归。源码：[infrastructure/retrieval_postgres.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_postgres.py)。

### Q34：HNSW为什么快，牺牲了什么？

**短答：**通过多层近邻图逐步接近目标，减少逐个向量计算，换来额外内存、建图成本和近似召回误差。

**展开：**上层稀疏导航，底层细搜。M是连接规模，ef_construction是建图候选宽度，ef_search是查询探索宽度；不要把三者都叫TopK。当前建图M16、ef_construction64，按generation创建cosine索引，查询是否走它需EXPLAIN验证。

**继续追问：**exact topK仍可能语义错误；ANN recall和证据Recall不同。过滤可能减少有效候选，应测试不同scope选择性。[官方说明](https://github.com/pgvector/pgvector)。源码：[infrastructure/hybrid_retrieval_backend.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/hybrid_retrieval_backend.py)。

### Q35：cosine、内积、L2有什么区别？

**短答：**cosine比较方向，内积受方向和向量长度共同影响，L2比较欧氏距离；应匹配模型训练方式和索引算子。

**展开：**若向量都单位归一化，内积与cosine排序一致，L2平方等于2减2倍内积，排序也等价。但不能假设任何provider都归一化。generation保存distance与embedding profile，当前Knowledge使用cosine；换模型或预处理要重建匹配索引。

**继续追问：**同维度不代表同语义空间，不能把hash与BGE混存当同一个generation。源码：[application/hybrid_retrieval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/hybrid_retrieval.py)、[infrastructure/bge_m3_embedding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/bge_m3_embedding.py)。

### Q36：RRF怎么计算，为什么不用归一化分数相加？

**短答：**RRF对每路排名计算w/(k+rank)求和，减少不同评分尺度难以校准的问题。

**展开：**BM25分数和cosine不能天然可比；归一化分数可行，但min-max等受每次候选分布影响，还需校准。RRF稳健且简单，代价是丢掉分差。当前k10更重视前列，Dense/Lexical默认.5/.5；要拿固定预算对照证明选择，不把常用k60当必然标准。

**继续追问：**一篇文档两路都靠前通常有更大贡献；单路独有证据仍可能在截候选时被丢。源码：[mcp/rank_fusion.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/rank_fusion.py)、[core/rag_policy.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/rag_policy.py)。

### Q37：你的两个0.25/0.75到底哪一个？

**短答：**一个历史上指Dense/Lexical融合，另一个指raw/standalone查询融合，不能混为同一参数。

**展开：**当前Dense/Lexical默认.5/.5；raw/standalone默认常量.20/.60，无扩展时归一化成.25/.75。环境示例直接写.25/.75。若standalone与原句相同或不可用，权重合并到raw。线上最终值受Bundle与环境影响，实验应记录policy fingerprint。

**继续追问：**为什么偏重rewrite？希望完整查询主导，同时保留原句锚点；目前是开发采用方案，不是已证全局最优。源码：[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_retriever.py)、[core/rag_policy.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/rag_policy.py)。

### Q38：rewrite和query expansion、HyDE一样吗？

**短答：**rewrite把已有需求表达完整；expansion产生多个查询表达；HyDE生成假想答案帮助向量检索。它们的风险和成本不同。

**展开：**“那运费呢”改写成具体产品与退货原因是补上下文；产生邮费/退货运输等变体是扩展；先生成一段退货政策再检索是假想文档。后两者可能增加噪声，HyDE内容绝非真实政策。项目默认扩展关闭，RESOLVED不无条件二次改写。

**继续追问：**rewrite也可能把假设变事实、上一轮主题带入当前问题，要同时检查query和metadata过滤参数。源码：[mcp/query_transformer.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/query_transformer.py)、[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_retriever.py)。

### Q39：chunk、overlap、parent-child怎么定？

**短答：**围绕必要条款是否完整和最终token预算比较，而不是固定背一个长度；当前示例结构切块512/64。

**展开：**小块更准确但可能割裂例外，大块上下文多但噪声和成本高。overlap保边界也制造重复。parent-child能扩大上下文，也会挤掉其他来源。项目较小chunk和父内检索的历史尝试没有一致收益，保持已有方案并记录负结果。

**继续追问：**同K不同chunk大小不公平，至少控制最终token和gold映射。源码：[mcp/document_chunker.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/document_chunker.py)、[mcp/context_packer.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/context_packer.py)。

### Q40：为什么加reranker，embedding更好不就行了？

**短答：**embedding适合大库低成本召回，reranker用完整query和候选做更细的相关性比较，两阶段分摊计算。

**展开：**bi-encoder文档可预计算，cross-encoder每个query-document对联合计算，更贵但能看交互；listwise LLM还能同时比较候选，但延迟和格式风险更高。本项目支持本地BGE reranker与listwise，实验明确模型身份。若候选没有gold，精排不可能把它创造出来。

**继续追问：**精排每块独立高分仍可能漏多证据集合中的一项，所以看完整必要覆盖，不只首条相关。源码：[infrastructure/local_knowledge_reranker.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/local_knowledge_reranker.py)、[infrastructure/knowledge_retriever_adapters.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/knowledge_retriever_adapters.py)。

### Q41：为什么扩大TopK反而可能变差？

**短答：**更多候选可能增加相似干扰，最终正文预算固定时，高分重复片段会挤掉互补证据。

**展开：**项目历史20→80候选对照中候选完整覆盖223→244，但最终pack208→207。这不是大K理论无效，而是说明candidate recall不等于最终输入完整性。诊断要看rerank、去重、pack、wire各级；不能无限加K逃避集合排序问题。

**继续追问：**是否用MMR？可比较相关性与多样性，但要防止把必要相似条款当冗余，尚无证据时不宣称已采用。证据：[rag-optimization-status.md]({{ '/assets/handbook/evidence/plans__rag-optimization-status.md.txt' | relative_url }})。

### Q42：知识缓存为什么不能只用query作key？

**短答：**同一句话在不同用户权限、语料版本、模型、过滤和预算下结果可能不同。

**展开：**项目分别缓存transform、candidates、rerank、pack，每层key包含所需上下文与版本身份。命中后还验证source有效性，避免引用被撤回或变更资料。缓存key漏模型身份会让换reranker仍读旧排序；漏scope更可能越权。缓存提升速度，不能成为来源有效性的Owner。

**继续追问：**防击穿只减少重复计算，不等于业务幂等；要区分缓存lease与写操作ledger。源码：[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_retriever.py)、[infrastructure/retrieval_cache.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_cache.py)。

### Q43：metadata是硬过滤还是加权提示？

**短答：**身份/授权是硬边界；业务适用条件按受支持合同处理；软hint只辅助相关性，不能授予权限。

**展开：**tenant/user来自系统，模型不能覆盖。渠道、地区、产品、时间如果进入硬过滤，必须有来源依据，否则模型编一个日期会把正确证据排空。soft hint可帮助定位但也会带偏，需记录实际参数和权重。缺失条件应保持未知或澄清，不擅自补齐。

**继续追问：**查询主题准确不代表options准确，评测要审完整ToolCall。源码：[infrastructure/knowledge_applicability.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/knowledge_applicability.py)、[application/knowledge_tool_contract.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_tool_contract.py)。

### Q44：为什么EvidencePack要保存那么多来源字段？

**短答：**为了把模型看到的文字对应回真实版本和位置，并在缓存、回读、核验和发布时验证没有换证据。

**展开：**source revision、checksum、chunk坐标、rank、generation和policy fingerprint回答“来自哪里、当时用哪版、怎么选中”。引用ID只说明指向一个证据，不证明句子被它支持，语义核验仍要做。归档分页若只返回正文不带证据身份，模型可能无法正确引用，因此阅读接口要保留关联。

**继续追问：**查到来源后发生撤回怎么办？发布前复验有效性，不能依赖最初检索时的检查。源码：[mcp/evidence_pack.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/evidence_pack.py)、[application/response_assembly.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/response_assembly.py)。

## 6. 生成、核验与业务事实

### Q45：Synthesizer是什么，为什么不能直接拼Worker回答？

**短答：**它把多项结果组织成面向用户的一次答复，同时保留部分失败、未完成项和审批问题；不能只是拼接几段可能冲突的文本。

**展开：**项目由ResponseAssembler选择渲染/compose，ConversationProvider使用SYNTHESIS角色。输入包含结果板、事实、上下文和待输入/审批状态；输出是候选文字，不是业务事实来源。简单类型化结果可直接渲染，复杂场景用模型表达，避免不必要的固定多次调用。

**继续追问：**为什么拼接有问题？一个Worker说“符合条件”，另一个等待用户确认，拼接容易让用户误以为已执行。源码：[application/response_assembly.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/response_assembly.py)、[infrastructure/target_conversation_provider.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_conversation_provider.py)。

### Q46：Verifier与Synthesizer分开有什么价值，哪些路径不需要？

**短答：**生成负责解释，核验对照证据检查支持性与需求覆盖；已经由代码完整定义的范围和字段不再重复交给模型判断。

**展开：**当前claim_verification不再包含approval_terms_complete。审批卡由已绑定操作集合渲染，受支持业务状态/字段也可直接展示；知识解释和复杂综合仍共享原始证据快照进行核验。模型不负责授予权限，代码也不能仅凭引用存在就证明语义正确。

**继续追问：**分开有额外成本和共有模型偏差，因此应评误放行、误拒绝和调用开销，不能把每条回复都套相同链路。 源码与证据：[services/claim_verification.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/claim_verification.py)、[application/response_assembly.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/response_assembly.py)。


### Q47：规则校验、Coverage、Verifier分别查什么？

**短答：**规则查身份/schema/版本/合法引用，Coverage查任务与必需事实是否满足，Verifier查文字含义是否受证据支持且回答需求。

**展开：**存在合法订单回执不代表生成句子没把“待处理”写成“已到账”；语义上像正确也不代表有批准或有效来源。三层检查各自的确定性范围。调用失败和语义拒绝也不同，UNKNOWN不能伪装成REJECT或PASS。

**继续追问：**不能用LLM替代所有确定性校验，也不能用引用存在性替代语义支持。源码：[application/result_board.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/result_board.py)、[services/answer_verifier.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/answer_verifier.py)。

### Q48：为什么核验失败只修订一次？

**短答：**给候选一次根据明确问题修正的机会，同时限制延迟、成本和反复改写污染。

**展开：**ResponseAssembler把原答案和assessment反馈交给compose，仍基于同一board，不重执行业务工具，然后核验新文本。若仍不通过走类型化安全结果或升级。不是让模型不断尝试直到裁判说PASS，这会挑选随机误通过。

**继续追问：**修改答案必须重新绑定核验，旧答案的PASS不能复用。源码：[application/response_assembly.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/response_assembly.py)、[services/answer_verifier.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/answer_verifier.py)。

### Q49：Verifier也会错，你怎么证明它有用？

**短答：**用同证据的正确/错误候选比较误放行、误拒绝和成本，不能用它自己的PASS率当正确率。

**展开：**最近12条合成开发探针包含6组成对答案；生产verify_claims各调用一次，6正确全部接受、6错误全部拒绝，API/schema错误0。每条核验延迟中位2023ms，合计输入19921、输出1053Token。标签由编码助手编写，非独立人工；均衡样本不代表真实错误分布，拒绝也不代表已经修正答案。

**继续追问：**这支持对明确语义矛盾保留针对性核验，不支持所有回复加judge；某条拒绝理由仍有过度解读。 源码与证据：[plans/verifier-value-probe-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/verifier-value-probe-2026-09-09.md)。


### Q50：为什么“查询不到”不能直接回答“没有”？

**短答：**没观察到结果可能是不存在，也可能权限、超时、查询条件或数据源故障；只有工具合同明确证明不存在才可作事实断言。

**展开：**RAG无证据不是政策不存在；订单查询超时不是没有订单；退款receipt未知不是退款失败。不同typed outcomes决定继续查、澄清、对账或服务提示。Synthesizer应保留不确定性，Verifier检查是否从“不知道”升级成否定事实。

**继续追问：**来源权限使用户看不到数据，也不能泄漏是否存在其他人的订单。源码：[application/agent_result.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/agent_result.py)、[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)。

## 7. 审批、持久化与可靠性

### Q51：审批到底批准什么，用户说“好”算批准吗？

**短答：**批准的是准备好的特定操作、参数和目标版本，不是某个领域的长期权限；自然语言回复要结合当前交互理解。

**展开：**TargetActionPreparation生成绑定operation_key的pending action，必要时先读readiness和版本。结构化approval_id/version与当前状态匹配后才可恢复。模型不会因为看到“好”就获得通用写权限；此前若问的是配送方式，它是补答，不是同意退款。用户更改金额或对象时原审批不能悄悄迁移。

**继续追问：**τ³ transport不会自行把yes译成approved，应用主链拥有其含义。源码：[infrastructure/target_action_preparation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_action_preparation.py)、[tests/test_tau3_full_adapter.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_tau3_full_adapter.py)。

### Q52：如何防止退款重复执行？

**短答：**业务操作有稳定身份与参数绑定，ledger用CAS取得执行权，已提交重放回执，结果未知先对账。

**展开：**重复HTTP请求与同一业务动作是不同层幂等：request_id控制准入，operation_key控制业务操作。相同key不同参数应冲突；COMMITTED直接返回receipt；执行中断进入UNKNOWN/RECONCILING，查询真实结果。数据库记录防重必须与业务工具支持的幂等/对账合同协作。

**继续追问：**这不是任意外部支付的exactly-once承诺。若外部服务既无幂等也无查询，无法凭本地锁证明只提交一次。源码：[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)、[infrastructure/target_workflow_execution.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_workflow_execution.py)。

### Q53：Checkpoint和业务账本为什么不能合并？

**短答：**Checkpoint回答代码执行到哪，业务记录回答操作是否提交及回执是什么，恢复位置不是提交证明。

**展开：**工具远端已提交、写checkpoint前进程死掉，重入节点可能重复请求。反过来图节点结束也可能只得到UNKNOWN。业务ledger拥有操作状态与CAS，checkpoint拥有模型消息和图位置，发布记录拥有答复。分开后能处理部分成功而不伪造原子性。

**继续追问：**旧checkpoint类型不能重建时应明确版本不支持或重新协调，不默认None后继续。源码：[infrastructure/langgraph_checkpoint.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/langgraph_checkpoint.py)、[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)。

### Q54：客户端断线后为什么不重新生成答案？

**短答：**已正式选择的答复应该按response_id/seq重放，重新生成可能改变内容、成本和业务含义。

**展开：**请求准入、执行和publication持久化，客户端按序号续取；ACK表示selected/delivered/read的单调推进。HTTP超时只说明客户端未收到，不说明业务未完成。若还在执行返回Accepted或查询运行状态；若已有publication读取同一结果。

**继续追问：**read不是支付完成，不能把送达状态当任务结果。源码：[infrastructure/postgres_response_delivery.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_response_delivery.py)、[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)。

### Q55：异步worker重复领取、租约过期怎么办？

**短答：**持久run通过claim、renew与ownership检查限定执行者，失去租约者不能继续选择终态；副作用仍需单独幂等。

**展开：**heartbeat维持运行所有权，执行阶段检查claim。租约机制处理进程崩溃后的再次调度，但无法撤销已经发出的远端请求。因此需要操作ledger与对账，不能把“一个worker active”当业务只执行一次的证明。

**继续追问：**测试应覆盖失去租约后写终态、超时后恢复和已提交回执重放。源码：[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)、[infrastructure/postgres_target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_target_run.py)。

### Q56：用户改需求或取消后，旧Worker结果怎么办？

**短答：**任务有control/revision绑定，执行前后和工具边界检查是否仍有效；旧任务结果不能覆盖新意图。

**展开：**取消一个退款目标不应该取消独立的产品查询，依赖它的后续则需要关闭。并行任务完成时间不可预测，所以仅在派发时检查一次不够。WorkControlGuard返回superseded状态，让结果板保留明确的生命周期，而非把旧回答当本轮成功。

**继续追问：**取消是停止继续工作，不会自动回滚已提交业务；已执行部分仍需回执和解释。源码：[application/work_control.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/work_control.py)、[application/target_conversation_manager.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_conversation_manager.py)。

## 8. 评测、数据与优化闭环

### Q57：为什么选Doc2Dial、MTRAG、WixQA？

**短答：**分别检查文档条款依据、多轮问题保持和产品帮助指引，覆盖客服知识工作的不同需求，而不是凑三个数据集名称。

**展开：**Doc2Dial能映射文档span，适合判断必要条款；MTRAG按collection有passage qrel，能暴露追问改写偏移；WixQA有帮助文章和产品支持问答，适合文档定位与步骤依据。它们不能验证中文订单写操作，所以另建电商模拟集并接τ³业务交互。

**继续追问：**三个数据的gold单位不同，结果分别报告，不能平均成“RAG准确率”。详见[RAG数据专题]({{ '/rag-study.html' | relative_url }})。

### Q58：外部语料怎样转换，怎么避免评测作弊？

**短答：**保留官方ID、来源和hash，gold只给评分器；检索端只读语料、当前及此前对话和允许的元数据。

**展开：**Doc2Dial span映射当前chunk时要保存原坐标；MTRAG官方rewrite实验与本系统实际query分开；WixQA文章级qrel不被改成随结果选择的chunk标签。导入、切块、检索、pack和wire都有可追踪身份。看到heldout后修复，该数据就应标为已消费回归。

**继续追问：**相关标签不完备时qrel未命中不必然整段答案错误，但需独立证据审查，不能主观加gold美化成绩。证据：[rag-official-benchmark-protocols-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-official-benchmark-protocols-2026-09-08.zh-CN.md.txt' | relative_url }})。

### Q59：中文模拟集怎样设计才有用？

**短答：**从业务失败类型构造必要证据和合法结果，按规则族分组，加入相似但条件不同的负例，而不是只做同一句话改写。

**展开：**政策基础条件、质量例外、运费承担分布到三份来源；同产品异渠道、旧版本作为干扰。追问和假设测试主体/条件保持；混合订单取证另需工具fixture和业务断言。120复杂题最初按30规则族分40开发/80留存；后续80题已完成Metadata对照并消费为回归，不能再称未见集，也不能把体裁共享说成完全未知分布。

**继续追问：**17chunks而candidate20必然接近全库，Top20满分没有大库意义；扩库后必须重测。证据：[ecommerce-complex-rag-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__ecommerce-complex-rag-2026-09-08.zh-CN.md.txt' | relative_url }})。

### Q60：Recall、MRR、nDCG和完整必要证据覆盖分别是什么？

**短答：**Recall看相关证据找回多少，MRR看第一条相关位置，nDCG看整体排序质量，完整覆盖要求每题所有必需证据都到达指定阶段。

**展开：**需要A/B/C，检索到A/B：证据单元Recall=2/3，完整覆盖=0；A第一则MRR=1。nDCG还依赖相关性等级和理想排序。必须说明@K、文章/片段/span、candidate/rerank/pack/wire哪个阶段，以及无相关标签如何处理。

**继续追问：**业务通过率另看任务结果和约束，不从MRR换算。详见[RAG指标]({{ '/rag-study.html' | relative_url }})。

### Q61：τ³到底测什么，和RAG评测有什么不同？

**短答：**τ³通过模拟用户与环境工具检验交互式业务任务，RAG评测主要检查问题、知识证据和答案。

**展开：**Tau3TargetAgent把用户输入交给实际Coordinator，工具调用交官方环境执行，回传结果继续同一应用。不是在adapter里读取expected动作后手写答案。项目当前τ³适配是retail单领域，不能把其成绩解释成六域Encoder收益。代码import仍用tau2命名空间，应按实际上游版本/manifest解释，不凭包名猜benchmark代际。

**继续追问：**ENV、ACTION、ALL分别保存；官方总分出错不能用局部通过拼一个官方总分。最新操作集合task22回归无evaluator errors，ENV=0、ACTION=1、ALL=0；初始批次已执行，后续对话造成最终状态偏离，不能解释成评分器未运行。源码：[evaluation/tau3_full_adapter.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/evaluation/tau3_full_adapter.py)、[scripts/run_tau3_full.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/scripts/run_tau3_full.py)。

### Q62：为什么不能把Completed或verified当成功？

**短答：**Completed表示应用完成一次回复，verified表示内部核验通过，业务成功需要独立任务判定。

**展开：**“请补充信息”可以是Completed，却没有满足用户需求；错误主题的回答也可能被内部Verifier放行。官方环境状态、必需动作、用户限制与独立答案审阅才提供质量依据。项目已有这类失败报告，所以指标按运行、核验、任务结果分层。

**继续追问：**合理澄清本身可能是当前轮正确行为，应在任务合同里定义，不应一律罚失败或当全任务成功。证据：[rag-three-dataset-final-2026-09-08.zh-CN.md]({{ '/assets/handbook/evidence/docs__rag-three-dataset-final-2026-09-08.zh-CN.md.txt' | relative_url }})。

### Q63：对照、消融、配对、重复运行分别是什么？

**短答：**对照比较方案，消融拿掉一个模块看贡献，配对逐题比较，重复运行检查随机稳定性。

**展开：**同题旧版失败新版成功为救回，反之误伤；净提升是两者之差除题数。若每版各100题跑3次是600个任务run，不是300。消融时保持模型、输入、token和工具权限可比；去掉历史制造弱基线不能公平证明rewrite有效。

**继续追问：**重复同题不等于新增独立样本，置信区间应考虑对话族相关。当前某些实验只有一次采样，不能补写“三轮”。

### Q64：你真正做过哪些优化，有失败方案吗？

**短答：**均衡融合是已采用的开发选择；改写、候选扩大、父内检索、few-shot和微调都有独立实验记录，未改善的保留失败并不采用。

**展开：**三集同预算.25→.5有净覆盖收益；复杂中文小库改写完整证据25/40→31/40，后续扩库不能沿用小库收益；Metadata贯通的80题对照完整可见覆盖32.5%→77.5%；扩大候选却使最终pack下降，父内检索救回3误伤7；reranker微调24/40未变；Encoder独立复核失败保持关闭。每个数字只属于原实验，不是同一端到端版本的加总收益。

**继续追问：**闭环是定位哪一级丢失、只改相关变量、同题核验救回误伤、未见验收，而不是尝试次数多。证据：[rag-optimization-status.md]({{ '/assets/handbook/evidence/plans__rag-optimization-status.md.txt' | relative_url }})。

### Q65：怎样区分数据问题、模型问题和工程bug？

**短答：**从原始输入到最终输出逐边界核对事实，先找gold/信息在哪一级消失，再判断原因。

**展开：**gold不在语料或标错是数据问题；候选缺失可能召回/过滤；候选有而wire无可能打包/序列化；wire完整但改写主题或答案漏条件是语义行为；verifier读了错误版本是绑定bug。环境超时与模型语义错误单列，不能通过重跑隐藏。

**继续追问：**一个反例只能证伪局部，不证明全局修好；修复后需性质/状态测试和未见案例。源码：[evaluation/rag_full_chain_probe.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/evaluation/rag_full_chain_probe.py)。

### Q66：Token和延迟怎么报才有意义？

**短答：**区分每请求、每成功任务和每阶段，包含失败与修订成本，延迟说明是否含模型与重放。

**展开：**每成功任务摊销Token=全部运行总Token/成功任务数。规划、rewrite、rerank、Worker、摘要、compose、verifier均进入分子。并行任务墙钟时间不是各span之和；缓存重放耗时不能冒充真实reranker延迟。小样本不宜宣传代表性p95，模型价格变化时应记录计价版本。

**继续追问：**换便宜模型可能增加失败重试使每成功任务更贵；要看质量—成本曲线。源码：[evaluation/behavior_baseline.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/evaluation/behavior_baseline.py)、[core/llm_metrics.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/llm_metrics.py)。

## 9. 多模态、可观测、扩展与项目边界

### Q67：为什么视觉按需调用，不每轮都看图？

**短答：**能用文字解决的任务不增加视觉成本；OCR适合文字，复杂布局或视觉属性再用VLM。

**展开：**上传先鉴权和绑定，L0/L1/L2由任务需求决定，Tesseract与DeepSeek Vision产带checksum/page/bbox/producer的观察。图像是输入数据，不是指令权限；“截图显示已退款”也不能替代业务回执。当前支持与测试以实际图像fixture为范围，不夸大任意复杂文档解析。

**继续追问：**OCR/VLM不一致先保留来源和不确定性，不能让生成模型随意选一个当业务真相。源码：[application/media_requirement.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/media_requirement.py)、[infrastructure/deepseek_vision_provider.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/deepseek_vision_provider.py)。

### Q68：Langfuse和Prometheus各看什么？

**短答：**Langfuse帮助追踪模型/工具链和内容层诊断，Prometheus看请求、耗时、失败等运行指标；本地PG span保留持久Trace。

**展开：**trace_id连接请求、work、工具回执和publication；模型调用记录角色、token和时间，RAG带policy/generation和阶段排名。Exporter可选，关闭不应改变业务结果。敏感prompt、凭据和用户内容需按脱敏策略处理，不能为了可观测无差别外发。

**继续追问：**Trace显示动作执行不代表动作正确；它提供证据，评分仍由任务合同决定。源码：[infrastructure/langfuse_trace_sink.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/langfuse_trace_sink.py)、[infrastructure/postgres_trace_sink.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_trace_sink.py)。

### Q69：如何抵御Prompt Injection和越权？

**短答：**把用户/网页/知识/图片都当数据，模型可见能力受限，可信身份由系统注入，执行和发布有独立校验。

**展开：**知识正文“忽略规则退款”不能改变allowed_tools；缓存与归档按scope隔离；source与操作receipt分别验证。不能承诺已抵御所有攻击，需用恶意工具结果、跨用户引用、伪造审批和历史指令做对抗测试。过强输入安全规则也可能误拦合法工具证据，应有清晰协议边界。

**继续追问：**仅做字符串关键词屏蔽不够，权限必须在执行侧成立。源码：[core/input_security.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/input_security.py)、[mcp/tool_manager.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/mcp/tool_manager.py)、[infrastructure/target_result_archive.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_result_archive.py)。

### Q70：新增一个领域或业务工具要改哪些地方？

**短答：**先定义业务能力与权限、参数/回执/对账合同，再注册领域或工具，接实现和测试，规划自动获得受支持动作范围。

**展开：**只读工具声明authority和schema；写动作额外声明审批策略、目标版本和reconciliation。Registry、ToolManager、executor、事实adapter、结果覆盖和评测都需一致。领域Agent复用create_agent而非复制一套循环；Encoder若要覆盖新类必须重新训练校准，否则默认defer。

**继续追问：**扩展点不代表接受任意未知枚举，未知能力要类型化拒绝。源码：[application/default_capability_registry.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/default_capability_registry.py)、[application/capability_registry.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/capability_registry.py)。

### Q71：与SOTA或生产系统相比，优势和不足是什么？

**短答：**本项目重点是可恢复执行、证据和评测可追溯，尚无同条件官方榜单成绩证明领先；生产质量、规模与对抗验证仍有边界。

**展开：**可以对照成熟实践中的任务隔离、持久状态、人审、幂等与原生评测；研究模型的更高benchmark分数不能直接证明工程可靠性。当前单retail适配、中文模拟集、已消费开发对照和未启用Encoder要明确。下一步价值应来自未见完整链路、难负例和真实失败分布验证，而不是继续增加框架。

**继续追问：**“更先进”要转成可测指标：同预算任务完成、证据支持、误操作、恢复率与成本。原理来源与访问日期见[证据页]({{ '/handbook-evidence.html' | relative_url }})。

### Q72：讲一个最能体现能力的STAR案例。

**短答：**可以讲候选召回提高但最终证据更差的案例，说明你如何把优化从单一Recall推进到实际模型可见证据。

**展开：**场景：复杂政策需要多处依据；任务：提高完整证据覆盖且不超预算；行动：固定数据、候选与最终token合同，分别记录candidate、rerank、pack、wire，发现扩大候选引入竞争而最终pack下降；结果：保留失败，不采用大K，转向融合与排序的同预算对照，明确开发收益和未见验收边界。这比说“我调了很多参数”更能讲出因果过程。

**继续追问：**若选择上下文案例，讲先归档再压缩、保护最新工具批次、可分页回读，以及测试证明没有二次执行业务工具。数字引用原报告，不添加未做的线上提升。源码：[tests/test_target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_target_context_compaction.py)、[rag-optimization-status.md]({{ '/assets/handbook/evidence/plans__rag-optimization-status.md.txt' | relative_url }})。

## 10. 状态、长期记忆与后端扩展深挖

### Q73：意图分类、槽位抽取与DST有什么区别？

**短答：**意图确定需求类型，槽位抽取找到对象与参数，Dialogue State Tracking持续维护多轮目标和字段的有效状态；分类正确仍可能执行错对象。

**展开：**经典任务对话系统可用分类头预测intent、序列标注预测slot（BIO标签，CRF是可选结构约束），再由状态跟踪更新已知槽位。LLM能联合输出意图/参数，但同样要处理纠正与过期。项目Encoder只分领域，实体绑定保存source、scope、版本和类型选择，ConversationState维护待补答/审批与活动目标，Policy验证参数。比如订单号与图片编号形似，正则提取只得到候选，不能直接证明它属于用户订单。

**继续追问：**历史里两个订单都相关时应AMBIGUOUS，不能按最近出现者自动批准退款。源码：[application/entity_binding.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/entity_binding.py)、[application/conversation_state.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_state.py)。

### Q74：长期记忆检索和知识RAG能共用权重吗？

**短答：**可复用检索基础设施，但目标不同：知识找依据，服务经历既可能帮助指代，也可能提供历史证据，时间和唯一性规则不同。

**展开：**ServiceEpisodeRetriever区分REFERENCE_RESOLUTION和HISTORICAL_EVIDENCE；前者需要唯一绑定margin，后者允许多个相关经历。freshness有硬窗口、锚定或近期、仅排序等策略。MemoryRetrievalPolicy默认vector=.30、lexical=.60、recency=.10、RRF k60，与Knowledge .5/.5/k10不是同一套权重。旧服务经历不能替代实时订单回执。

**继续追问：**时间新不代表语义相关，先有相关候选再按用途处理新鲜度；需要分别评唯一绑定准确性与历史证据召回。源码：[application/memory_retrieval_policy.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/memory_retrieval_policy.py)、[application/service_episode_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/service_episode_retriever.py)。

### Q75：Commitment服务承诺是什么，和普通记忆有什么不同？

**短答：**它是有明确来源、到期时间和履约状态的业务对象，不是一段模型摘要中的“我们会处理”。

**展开：**PG保存承诺及事件，CAS约束修改；到期未履约产生违约，后续带业务回执可记录迟到履约并保留违约历史。不能因LLM生成一句承诺就静默新增义务，也不能让用户自然语言“已经办好了”替代履约凭据。它让转人工时看到仍未解决的承诺和风险。

**继续追问：**为何保留breach？迟到完成与按期完成不是同一事实，覆盖旧状态会丢失服务质量历史。源码：[infrastructure/postgres_commitment_service.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_commitment_service.py)、[services/commitment_service.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/commitment_service.py)。

### Q76：转人工为什么不是返回一句“请联系人工”？

**短答：**真正升级需要持久工单和可继续处理的上下文，包括诉求、已核实事实、尝试过的动作、缺失材料及回执。

**展开：**HandoffDraft由业务边界接受后，受控工具创建ticket；PG事务保存状态、事件和outbox，幂等身份避免重复建单。用户拒绝某一动作不一定必须转人工，Verifier UNKNOWN也不意味着任意场景都已自动创建工单，要看当前策略与实际工具回执。

**继续追问：**把整个prompt发给人工既冗余又有隐私风险，应保留可追溯摘要和必要事实。源码：[application/handoff_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/handoff_runtime.py)、[infrastructure/postgres_ticket_service.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_ticket_service.py)、[infrastructure/target_workflow_execution.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_workflow_execution.py)。

### Q77：仓库有evolution模块，是自动自我进化吗？

**短答：**有基于失败组生成受限Bundle候选的能力，不等于模型能在线改代码或自动发布高质量策略。

**展开：**BadCase与attribution记录失败，proposal_generator以失败组生成受限配置/提示候选，Bundle与registry保存版本及资格。是否运行、评测通过及启用仍需要对应证据。原理上类似反思生成候选再评估，但不能因模块注释写GEPA-lite就宣称完整复现某研究算法或取得论文效果。

**继续追问：**让同一个judge反复挑最喜欢的候选会过拟合，必须冻结开发条件、保留未见验收和失败分布。源码：[services/evolution/proposal_generator.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/evolution/proposal_generator.py)、[services/evolution/bundle.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/evolution/bundle.py)、[services/badcase_registry.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/badcase_registry.py)。

### Q78：为什么规划用便宜模型，生成和核验用更强模型？

**短答：**按角色分配模型可以控制成本，把预算留给复杂表达和语义审查；是否值得仍要做同题质量—成本对照。

**展开：**ModelPolicy源码默认INTENT/WORKER/REWRITE/RERANK等为Flash，SYNTHESIS/VERIFIER/JUDGE为Pro，运行环境和实验profile可覆盖。模型是兼容端点上的实际ID，不能因用了Anthropic适配就说一定调用Claude。参数、reasoning设置、输出上限与retry都会影响表现，报告应记录角色profile，而不只写模型系列名。

**继续追问：**更强Verifier也可能与生成模型共错；增加延迟是否值得需要误放行与误拒绝数据。源码：[core/model_policy.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/model_policy.py)、[core/framework_models.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/framework_models.py)。

### Q79：部署、性能和容量你能承诺什么？

**短答：**当前是Docker Compose本地可复现后端，有应用、PG、Redis、Nginx和监控；没有证据就不承诺高并发生产SLA。

**展开：**CPU同步SQL、embedding/GPU、模型API和连接池都是潜在瓶颈。async接口不自动让阻塞操作无成本；并行检索也要受连接与线程预算限制。扩库遇过HNSW构建共享内存不足和一次PG不可用，前者有环境处理记录，后者底层原因未确定，不能说全是模型错误。压测应固定语料、缓存热度、并发和模型模式，报告队列/池等待与真实墙钟延迟。

**继续追问：**可先读only回放定位本地耗时，再做明确预算的真实API抽样；本轮文档没运行压测。源码：[infrastructure/bounded_retrieval_executor.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/bounded_retrieval_executor.py)、[infrastructure/retrieval_postgres.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_postgres.py)。

### Q80：什么测试才能证明架构，不只是mock通过？

**短答：**单元测试验证局部合同，性质/状态机测试验证跨输入不变量，真实PG验证持久化，实际Agent与环境测试验证语义任务；它们互补。

**展开：**并行结果全排列验证汇总顺序无关；不同批次大小验证tool消息配对和归档可读；审批版本、取消、重复请求与未知结果验证合法转移；PG重开与HTTP测试覆盖持久边界。模型语义仍需未见输入和独立判定，不能用数百pytest证明问答正确。目录中的历史测试也不自动证明当前已在线装配，需追启动调用链。

**继续追问：**反复关闭又重开时应回到共享Owner和合同找共同根因，用生成/状态测试与未见案例检验，而不是每个反例加分支。源码：[tests/test_target_orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_target_orchestration_runtime.py)、[tests/test_target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_target_context_compaction.py)、[tests/test_domain_action_approval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_domain_action_approval.py)。

## 11. 最新架构调整：主从职责与审批展示

### Q81：为什么不让主 Agent 直接准备退款、取消订单和改地址？

**短答：**主 Agent 要维护整段对话与跨领域目标，领域 Worker 更适合在明确任务范围内调查业务条件并准备动作。

**展开：**旧入口允许主 Agent 选新写操作快捷动作，使它同时承担接待、业务字段收集和准备协议理解。现在删除这些快捷项，保留读取、知识、delegate_task 和已绑定的 review_action/supply_input 等能力。用户说“帮我改地址”可直接委派，领域再查对象、资格和所缺信息。allow_action_proposals=true 只允许提出方案，不批准业务写入。

**继续追问：**代价是明确写任务也可能多一次领域调用；收益是职责与上下文边界更清楚。没有同模型端到端对照，不能宣称一定省Token或提高成功率。 源码与证据：[application/conversation_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_agent.py)、[docs/conversation-responsibility-boundary.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/conversation-responsibility-boundary.md)。

### Q82：主 Agent 不看完整写工具协议，会不会丢失业务规则？

**短答：**短路由卡片、业务政策和写工具操作协议是三种信息，分别投影给需要它的角色。

**展开：**主Agent看到domain_capabilities与显式conversation_policy，保留身份、隐私、证据和交互约束；business_policy与business_operation_reference交给领域。回答作者与Verifier消费对话约束和实际证据，不再注入整份执行者政策。缺少conversation_policy时不回退到business_policy，策略作者需明确维护两种受众。

**继续追问：**只比较提示词长度不足以证明更好，要检查完整目标保留、是否正确委派、后续准备与客户实际收到的答复。 源码与证据：[application/agent_instructions.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/agent_instructions.py)、[application/capability_registry.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/capability_registry.py)、[infrastructure/target_model_context.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_model_context.py)。

### Q83：既然有工具调用，为什么还保留原生文本回复？

**短答：**普通对话和真正的意图澄清不需要启动任务，使用模型原生文本即可。

**展开：**Provider 接收原生消息，检查截断、refusal 和无效参数后交给 action_proposal。有调用时运行工具对应工作，调用前说明不当业务结果发布；无调用时文本结束本次决策，不自动安排后台工作。旧的回复工具会让“说一句话”和“执行动作”都套成调用，增加理解与协议负担；当前入口没有重新引入它。

**继续追问：**原生文本只代表无需派发新工作，不代表绕过回答边界；也不能用“我去查一下”的文字代替实际读取。 源码与证据：[infrastructure/target_conversation_provider.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_conversation_provider.py)、[application/conversation_actions.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_actions.py)、[application/turn_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_runtime.py)。

### Q84：用户说“修改账户和订单地址”，为什么不能拆成两个独立动作？

**短答：**它们可能共享业务状态、一次性修改能力或前置条件，应先让领域检查完整目标的可行性。

**展开：**委派保留两个目标和用户要求的顺序。operation_plan 表达剩余操作、依赖和期望结果，领域检查前一步效果是否破坏后一步前提。一个DAG无环不代表业务可行。确认独立且准备完整的成员可以同段形成操作集合；有顺序依赖的成员则准备首步，取得回执后重新评估剩余目标，不能盲目按最初计划连续写入。

**继续追问：**若工具临时留下某个状态，不能把它自动当成用户最终要求；若用户明确规定顺序，也不能为满足工具约束擅自换序。 源码与证据：[application/operation_plan.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/operation_plan.py)、[application/agent_instructions.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/agent_instructions.py)、[application/action_approval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/action_approval.py)。

### Q85：准备工具名称为什么也成了架构问题？

**短答：**模型可见 schema 和运行时验证必须使用同一个可调用名称集合。

**展开：**历史案例把执行工具 modify_pending_order_address 填进需要 prepare_modify_pending_order_address 的计划字段。宽泛字符串schema接受表达，后续验证却拒绝；模型接到笼统错误后甚至删掉用户剩余目标。现在准备工具枚举来自当前任务范围，调用协议与校验一致。修复落在生成schema和验证的边界，而不是补一个地址别名。

**继续追问：**工具名正确仅证明协议有效。目标是否保留、状态前提是否满足、审批展示是否准确，还需要独立检查。 源码与证据：[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)、[application/operation_plan.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/operation_plan.py)。

### Q86：为什么准备操作集合，而不是批准整个未来计划？

**短答：**用户只能批准参数、前提都已确定且实际展示的操作，未来计划不是可执行授权。

**展开：**同一领域内独立ready set可同时准备并一次展示；ApprovalOperation保存各成员身份/版本/参数。部分同意或改参数需等待或修订，不能静默删改已消费集合。有依赖的后续写操作先等回执再准备，operation_plan.remaining_steps不产生权限。

**继续追问：**批次批准不意味着跨业务原子事务；部分结果分别保存，未知效果仍先对账。 源码与证据：[application/approval_operation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/approval_operation.py)、[application/action_approval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/action_approval.py)。


### Q87：为什么要拆 requested_objective 和 observed_segment？

**短答：**用户想完成的整个目标，与当前一段 Worker 的结果不是同一件事。

**展开：**用户要求两项修改，当前段可能仅准备第一项。requested_objective 保留完整诉求，observed_segment 记录本段状态和原因，pending_actions 标明本次真正准备的动作。作者与Verifier消费同一快照，内部accepted评审不再成为业务证据。否则可能把“本段成功”扩成“全部已准备”，或因为还存在剩余目标就再次询问全部批准。

**继续追问：**需求覆盖要求解释剩余目标，不意味着可以把它们扩大成当前批准范围。 源码与证据：[application/response_assembly.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/response_assembly.py)、[services/answer_verifier.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/answer_verifier.py)。

### Q88：为什么单独标记 continues_after_reply=false？

**短答：**持久化的未完成任务表示以后可以继续，不代表本轮回复后真的还有后台工作。

**展开：**TurnRuntime 的回答节点接下来只提交状态并结束，因而显式提供 turn_execution 的 REPLY阶段、等待输入/审批标志和continues_after_reply=false。生成与核验使用同一生命周期信息，避免工具失败后仍说“我会继续处理，请稍候”。这是由运行时给出的执行事实，不应该靠模型猜。

**继续追问：**如果将来支持真正的后台调度，必须由实际调度与持久状态产生该事实，不能仅修改提示词把false改成true。 源码与证据：[application/turn_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_runtime.py)、[application/action_approval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/action_approval.py)。

### Q89：补充信息、批准动作和批准后继续有什么区别？

**短答：**补信息填业务缺口，批准决定当前已准备动作能否执行，继续执行由运行时恢复精确绑定的工作。

**展开：**缺少州名就问州名，不能顺便问“你确认让我修改两处吗”；当前仅准备账户修改时就只展示这一项的目标和后果。用户批准后review_action绑定原审批，不再次准备或收集预批准。用户拒绝一项保留其他独立目标，过期也不等于拒绝。主从职责明确后仍可能生成错误措辞，所以结构授权与语言质量要分别验证。

**继续追问：**已有任务在等待审批时，用户问另一件事不应反复展示原批准请求；这需要同时追踪准备状态与真实发布状态。 源码与证据：[application/action_approval.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/action_approval.py)、[application/turn_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_runtime.py)、[application/conversation_actions.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_actions.py)。

### Q90：最近task22到底暴露了什么，怎么讲？

**短答：**最新已记录回归验证了初始两项修改的一次审批执行，但后续对话偏离目标，最终任务仍失败。

**展开：**7c41b7e上的已消费task22、seed300、80步预算，先一次展示并批准账户和订单地址修改，两次写入成功。随后用户只要求恢复账户地址，主Agent两轮选RESPONSE未委派。模拟用户后来扩大为两处恢复，经过新的批准后两处都改回，订单最终状态错误。ENV=0、ACTION=1、ALL=0，evaluator errors为空。

**继续追问：**额外订单修改在生成对话中已获明确请求和批准，不是集合越权；问题是对话未保留原任务目标。随后策略隔离与字段展示尚无新同任务成绩。 源码与证据：[plans/tau3-task22-operation-scope-rerun-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/tau3-task22-operation-scope-rerun-2026-09-09.md)。


### Q91：Verifier更严格是不是就能解决审批问题？

**短答：**审批范围已经交回代码生成与精确绑定，继续堆模型布尔判断解决不了权限归属。

**展开：**之前作者漏项、judge误拒和空schema都可能阻塞确认。现在清单来自准备好的操作集合，在线approval_terms_complete移除；独立知识回答仍走支持性路径。确定性卡片保留范围，模型文字不能把未来操作加入批准。

**继续追问：**自然提问仍可能措辞不佳，运行时授权正确与交互质量分别验证。 源码与证据：[application/approval_presentation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/approval_presentation.py)、[services/claim_verification.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/services/claim_verification.py)。


### Q92：简化对旧挂起任务有什么影响？

**短答：**操作集合、策略和工具字段都参与各自合同或身份，旧任务仍受版本与Registry检查约束。

**展开：**7c41b7e引入操作集合；9a50ea1进一步移除旧执行兼容，不再接受裸图结果、旧指纹、缺policy解码和恢复范围补猜；c91eae2字段展示不改checkpoint形状，但工具输出版本和展示声明进入Registry身份。不能将不同阶段概括成“都没改格式”。

**继续追问：**网页更新不执行应用迁移，也不会重绑原批准或重放业务写入。 源码与证据：[plans/approval-scope-delivery-convergence-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/approval-scope-delivery-convergence-2026-09-09.md)、[plans/execution-response-simplification-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/execution-response-simplification-2026-09-09.md)。


{% include framework-backend-handbook.md %}

## 18. 简历五条经历的连续追问

这一组把简历、实现和验证串起来，配套[逐条讲述]({{ '/project-pitch.html#resume-map' | relative_url }})。

### Q121：“状态优先续接”具体优先什么，为什么不是每轮重新规划？

**短答：**先解释已有待补输入、当前审批和活动目标，保留对应身份与版本；再处理本轮独立新需求。

**展开：**用户一句“好，另外查一下运费”可能既包含已绑定批准，又新增知识请求。先把“好”交给精确审批决定，运费作为独立查询保留。全量重新规划可能重复准备动作、漏掉批准或把原目标替换成局部查询。状态续接不意味着所有消息都必须恢复旧任务，明确纠正或新目标仍由当前状态合同处理。

**继续追问：**简历里的Encoder只帮助适用的领域判断；当前默认关闭，不能让分类结果解释批准。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-collaboration' | relative_url }})。

### Q122：如何证明“部分结果交付”真的有价值？

**短答：**要在同一任务内区分独立成功、依赖失败、待补输入和未完成目标。

**展开：**例如订单读取成功而政策检索超时，可以陈述已确认的订单状态与政策暂不可用，但不能由订单状态推出可退。ResultBoard保留每项来源和状态，回答核验失败也不应抹掉已提交回执。验证应改变并行完成顺序、故障节点和用户后续动作，检查可交付事实相同，相关下游正确阻断。

**继续追问：**部分交付是输出能力，不等于整体成功；整体任务的必需目标仍由独立评分决定。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-collaboration' | relative_url }})。

### Q123：简历写结构断言与语义评审，各用什么证据？

**短答：**结构检查消息、引用与控制关系；语义检查要求、否定、来源与进展的含义。

**展开：**同一段历史保留压缩前内容、归档指针、压缩后消息和参考要求。结构检查调用与结果配对、页边界、原文可回读、没有重跑业务工具。语义检查required_preserved、no_invention、authority_preserved、progress_preserved。评审模型可能错，16次合成开发通过不证明长期对话普遍无损。

**继续追问：**摘要不必逐字一致；删掉无关重复是正常压缩，删掉“先不要退款”是语义错误。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-context' | relative_url }})。

### Q124：32%输入Token降低和41%重复查询减少，分母是什么？

**短答：**需要同任务配对统计，并先固定成本范围与重复查询定义；本轮未找到支持这两个数字的真实报告。

**展开：**Token下降可以定义为1−新方案总输入/旧方案总输入，但任务、模型、完成条件和摘要/回读调用纳入方式必须一致。重复查询下降用两版无效重查次数比较，必须排除合法刷新、对账和用户改变目标。若任务中途失败导致少调用，也不能直接说更高效。PDF两项沿用此前模拟口径，不能用摘要测试数量证明收益。

**继续追问：**可同时报告成功率、成功任务摊销成本、归档回读次数与语义保留，避免只优化一个数字。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-metrics' | relative_url }})。

### Q125：三个外部集的分数为什么不能合成总体准确率？

**短答：**它们的gold粒度和每题相关证据数不同，测的是不同客服需求。

**展开：**Doc2Dial300题69.33%是完整必要证据覆盖，约208题完整；MTRAG35题44.14%是片段Recall聚合；WixQA20题67.5%是文章Recall。后两项不是简单题通过数，所以不能算成15.449题或13.5题成功。实验也使用固定查询、语料和预算，不等于真实Agent自己提出完整query的端到端结果。

**继续追问：**跨集一致改善支持选型，但同一个真实业务任务还要评价查询生成、答案与工具效果。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-rag' | relative_url }})。

### Q126：77.5%证据覆盖和71.25%回答质量之间差了什么？

**短答：**前者62/80题有完整可见证据，后者57/80题回答严格完整且有据，是两个阶段的判据。

**展开：**该批23题未通过严格答案标准，记录为2题上游超时、16题可见证据不完整、5题证据完整但生成漏答或不当弃答。候选里有、Top5里有、打包里有、实际ToolMessage里有、答案使用了，是五个位置。必须沿原记录归因，而不是看到答案错就重新调召回。

**继续追问：**参考与审阅非独立人工Gold，80题已消费为回归；不能把该比例包装为真实电商生产准确率。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-rag' | relative_url }})。

### Q127：为什么“业务已完成”“回答已发布”“客户已看见”分开？

**短答：**它们属于副作用、正式输出和送达三个不同状态，需要不同记录证明。

**展开：**退款回执证明业务操作，Publication证明生成的正式回答已保存，ACK的delivered/read表示客户端报告的送达或已读。连接断开不能撤销退款；回答已保存也不能保证客户看到。恢复时先查原操作和已发布响应，而不是重新退款或重新生成一个可能不同的结果。

**继续追问：**客户端ACK是客户端声明的交付状态，不是对用户理解程度的证明，也不能作为新的业务批准。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-execution' | relative_url }})。

### Q128：最新审批改造补了简历中哪个没讲透的点？

**短答：**补足已准备范围如何准确展示、批准和逐项执行，进一步把确定业务字段展示交回工具合同。

**展开：**操作集合已由7c41b7e提交，不再是工作区方案。每项保留绑定和回执，清单由代码生成。c91eae2又将已注册金额、地址和状态按事实/回执直接展示，避免模型重复解释确定数据。

**继续追问：**完整对话质量仍需单独验收，不能把264项局部测试说成任务成功率。 源码与证据：[plans/execution-response-simplification-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/execution-response-simplification-2026-09-09.md)。


### Q129：一个Trace失败怎么沉淀成回归，而不是改Prompt再跑？

**短答：**先固定输入与事实，定位谁产生了错误含义，然后用对应层的可证伪检查验证修复。

**展开：**保存原公开对话、任务状态、工具输入输出、作者/Verifier快照与代码身份。协议错误用schema和绑定检查；状态丢失用持久化与后续轮测试；语义错误用原完整证据和正反例检查；最后再跑预注册的新任务。不能只修改假数据让mock通过，也不能在同个失败任务上反复调到一次成功就称泛化。

**继续追问：**旧任务恢复、新任务质量和总体通过率分开报告；失败记录继续保留。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-evaluation' | relative_url }})。

### Q130：简历50条客服任务76%如何支撑面试追问？

**短答：**先找到明确50任务的清单、代码/模型/seed、原生评分项与原始结果。本轮尚未定位匹配报告。

**展开：**若定义为单次二元成功，76%应对应38/50；如果是重复run均值或奖励平均，要说明不同公式。环境失败、无评分、用户退出和中断如何计入必须预先固定。已核对task22的最终ENV/ALL为0不意味着总体一定不是76%，但也不能替总体作证。网页保留为简历声明待对应，不虚构50条来源或重试轮数。

**继续追问：**能复算的报告才是讲数字的依据；没有报告时先讲确有证据的适配机制和失败归因。 对应[简历讲述与证据]({{ '/project-pitch.html#resume-metrics' | relative_url }})。

## 19. 简化后的架构取舍

### Q131：为什么把conversation_policy和business_policy分开？

**短答：**协调者需要对话约束，领域专家需要执行规则，两种受众不能共用整段指令。

**展开：**实际task22输入曾同时包含协调者职责和完整retail执行政策。现在AgentDefinition分别声明两类策略：主规划、回答和核验快照使用conversation_policy；领域工具调查与准备继续使用business_policy。缺少对话策略就不额外注入，不能退回执行策略，也不增加一个LLM提取政策。

**继续追问：**这是输入职责冲突的修复，尚不能仅凭合同测试保证模型不再漏委派。 源码与证据：[plans/conversation-policy-scope-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/conversation-policy-scope-2026-09-09.md)、[application/capability_registry.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/capability_registry.py)。


### Q132：金额和地址不经模型审核，怎么避免显示错？

**短答：**由工具声明公开字段、单位和语义，再核对事实来源、版本及当前操作回执。

**展开：**result_fields_text要求VERIFIED_STATE、producer_id、authority、output_schema_version与注册一致；execution_status_text进一步要求事实source_ref和requirement匹配本次operation_key对应回执。钱用Decimal和声明scale换算，差额方向由charge_delta合同定义。内部候选文字不在这条路径发布。

**继续追问：**声明本身也要测试：金额正负/零、币种/比例、双语、版本错配、旧读取事实、部分结果和无作者/judge调用。提交不是到账。 源码与证据：[application/result_field_presentation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/result_field_presentation.py)、[application/execution_presentation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/execution_presentation.py)、[tests/test_result_field_presentation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/tests/test_result_field_presentation.py)。


### Q133：为什么不用一个小模型决定要不要Verifier？

**短答：**当前可直出的条件由类型、注册字段与回执身份直接决定，新增路由模型只会增加一层不确定性和成本。

**展开：**ResponseAssembler只在没有待审批、补问、保留审批及独立候选等冲突时尝试执行结果渲染。结果集需全是符合合同的ACTION/WORKFLOW；未知字段、缺项、混合任务和不匹配回执返回原解释路径。ResultBoard继续保留独立目标，不按重复事实丢任务。

**继续追问：**将来若有无法确定分类的语义场景，要先定义数据和误分类成本再实验；当前没有新增这类模型。 源码与证据：[application/response_assembly.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/response_assembly.py)、[application/execution_presentation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/execution_presentation.py)。


### Q134：这次简化怎么证明有效，能写省了多少Token吗？

**短答：**可以证明特定输入不再调用作者/judge；整体成本和成功率仍需同任务实测。

**展开：**最新提交报告264项相关测试通过，含PG，覆盖字段身份、金额方向、回执关联、保留结果和无模型调用。另12条Verifier探针说明核验存在开销，但两者输入范围不同，不能直接相减得到全系统节省率。应冻结任务/模型/预算比较完成率、调用数、Token和延迟，同时保留失败。

**继续追问：**本次网页更新没有运行新模型或应用测试，也不改简历历史RAG数字。 源码与证据：[plans/execution-response-simplification-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/execution-response-simplification-2026-09-09.md)、[plans/verifier-value-probe-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/plans/verifier-value-probe-2026-09-09.md)。

## 20. WorkPlan、任务图与恢复合同

### Q135：Compiler和主Agent都规划，是不是重复？

**短答：**主Agent提出命令，Compiler是确定性编译代码，不再调用模型。

**展开：**RoutePolicy先校验身份、权限、参数和恢复来源；Compiler核对状态与Registry指纹，将命令ID依赖映射为WorkItem ID，生成WorkPlan及状态变更。普通回复可以有TurnPlan但没有WorkPlan。

**继续追问：**不能把编译器理解成优化LLM答案的模型，也不能跳过Policy直接执行工具。 源码与证据：[application/turn_planning.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_planning.py)。

### Q136：TaskGraph、WorkPlan与LangGraph到底什么关系？

**短答：**TaskGraph是任务依赖DAG，WorkPlan保存它和执行合同，LangGraph是运行设施。

**展开：**当前没有额外TaskGraph类；WorkItem.dependencies与execution_waves表达依赖。OrchestrationRuntime的StateGraph处理准备、分发、执行和合并，不是每次请求再生成一套框架节点。一次工作图可承载不同WorkPlan。

**继续追问：**固定运行流程和动态任务依赖是两层图；不等于两个规划模型。 源码与证据：[application/work_item.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/work_item.py)。

### Q137：WorkPlanPolicy为何放进计划，不让调度器默认决定？

**短答：**依赖、写串行、旧结果保留与部分交付是计划含义，所有消费者应读同一份合同。

**展开：**Compiler固定SUCCESS_WITH_COVERAGE、BLOCK、CONTROL_REVISION、DELIVERABLE_OUTCOMES_ONLY及写串行规则；policy进入计划指纹。Runtime负责调度，Board负责结果语义，不再各自猜默认。新建类型化默认有效，但旧记录缺policy不自动补。

**继续追问：**改policy就是改合同身份；不能继续接受旧指纹而按新规则解释。 源码与证据：[application/work_item.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/work_item.py)。

### Q138：为什么Reducer不能只按work_item_id去重？

**短答：**局部任务ID可能在多个计划重复，必须同时绑定计划和任务指纹。

**展开：**Worker返回AgentResult，运行时封装PlanScopedAgentResult。Reducer接受同身份相同重放、拒绝同身份冲突；读取时对照当前WorkPlan检查，再把AgentResult交给Board。跨计划记录即使不撞Reducer键，也不能进入当前结果计算。

**继续追问：**Reducer列表保留首次出现顺序，不是字节级排序保证；要验证完成顺序不改变Board语义。 源码与证据：[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/orchestration_runtime.py)。

### Q139：complete、coverage_complete和task_completed有什么差别？

**短答：**已有结果、证据齐全和任务成功是三种判断。

**展开：**complete表示当前任务都有结果记录，失败和BLOCKED也可能计入；coverage_complete检查事实/回执缺项及冲突；task_completed还要求保留项有结果且所有结果SUCCEEDED。partial_delivery_allowed只允许先交付有效部分。

**继续追问：**即使上述任务合同完成，也不能代替τ³外部最终状态评分，更不代表答案文字必然准确。 源码与证据：[application/result_board.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/result_board.py)。

### Q140：缺少恢复范围时，为何不根据active goal补回来？

**短答：**目标文字不包含已经批准的完整工具、参数与版本合同，补猜可能扩大权限。

**展开：**恢复必须来自持久等待/批准或resolver确认的任务范围，消费等待后原范围继续传递。模型自己提交resumed envelope不能把权限加回来；新目标必须按修订路径处理。checkpoint缺policy或旧结果格式明确拒绝。

**继续追问：**旧数据保留与旧数据可执行是两件事；本次没有自动迁移旧任务。 源码与证据：[application/turn_planning.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/turn_planning.py)。

### Q141：批准两个动作只得到一个结果，怎么防止误判完成？

**短答：**执行和完成投影使用同一份accepted_approval记录，按批准成员逐项检查。

**展开：**Manager不能从现有结果反推批准集合。每个operation_key都要有对应结果并通过Board覆盖检查；空集合或缺成员不能让all([])变成完成，只有SUCCEEDED标签也不够。未知效果保持对账，已提交成员回执保留。

**继续追问：**一个原批准集合不自动缩成有结果的子集，也不等于跨服务原子事务。 源码与证据：[application/target_conversation_manager.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_conversation_manager.py)。

### Q142：怎么验证计划作用域和恢复，不只测一个案例？

**短答：**用完成顺序、重放、冲突、跨计划和状态组合验证正向合同。

**展开：**9a50ea1报告708组合检查、97补充合同检查通过，含PG；集合重叠不能相加。覆盖Reducer组合/重放、25种两成员状态、缺批准/结果、消费后权限扩大、checkpoint往返和旧格式拒绝。扩展检查另有4个既有调用错误，如实保留。

**继续追问：**本轮无新τ³和模型调用，合同通过不宣称业务成功率提升；网页更新也没有重跑这批应用测试。 源码与证据：[plans/work-plan-single-contract-2026-09-09.md](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/plans/work-plan-single-contract-2026-09-09.md)。
