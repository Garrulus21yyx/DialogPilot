## 12. LangChain / LangGraph 基础与执行机制

### Q93：Chain、Agent和Graph有什么区别？

**短答：**Chain按既定组合处理输入；Agent由模型选择下一步工具；Graph定义状态和控制流，节点可以是模型也可以是普通代码。

**展开：**例如固定“检索→生成”可以是一条链；客服需要根据返回结果决定查政策还是查订单，用Agent工具循环；等待用户、并行依赖、业务写入和结果发布由Graph连接。三者是不同抽象，不是每加入一个名词就多调用一次模型。当前领域create_agent建立在LangGraph上，主任务图仍由应用显式定义。

**继续追问：**选型看流程是否固定、是否有动态决策、是否需要持久等待和跨任务状态。简单查询可DIRECT，不必套领域Agent。

**项目定位：**[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)、[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/orchestration_runtime.py)。

### Q94：invoke、ainvoke、stream、astream和batch怎么选？

**短答：**invoke返回一次完整结果，ainvoke是异步接口；stream/astream逐步产出；batch处理多个输入，具体并发能力和限制由实现决定。

**展开：**本项目主规划通过ainvoke读取完整原生模型响应，领域Worker通过astream(stream_mode="values")保留最近完成的状态，以便下一次模型失败时保留已执行工具结果。values是状态快照，updates是节点增量，messages用于模型消息流。它们与HTTP是否SSE是两层接口，内部流式不意味着每个中间状态都能发送给客户。

**继续追问：**模型token输出不能直接代表批准或业务完成；客户公开结果仍过Publication边界。不要把私有图状态直接透传。

**项目定位：**[infrastructure/target_conversation_provider.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_conversation_provider.py)、[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)。

### Q95：bind_tools是否会自动执行工具？StructuredTool和Pydantic各做什么？

**短答：**bind_tools把可用工具及参数协议交给模型；模型返回调用意图，实际执行由Agent或应用负责。

**展开：**StructuredTool提供名称、说明、参数schema和调用适配；Pydantic验证字段类型和约束。例如字符串订单ID符合schema，不代表属于当前用户，也不代表允许退款。主规划把tool calls转成提案再过Policy；领域工具注入可信context，在执行侧校验授权和业务状态。类型合法、身份合法、业务可行要分开。

**继续追问：**模型知道工具名不构成权限。额外JSON修复也不能自动补造授权字段，参数不完整应返回类型化错误或请求必要信息。

**项目定位：**[application/conversation_actions.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/conversation_actions.py)、[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)、[core/auth.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/auth.py)。

### Q96：State、Reducer、Send和Command如何配合？

**短答：**State保存图数据，Reducer规定更新合并方式，Send派发带输入的节点，Command可携带状态更新与控制跳转或恢复指令。

**展开：**并行节点不能都无约束覆盖同一字段。当前图结果用PlanScopedAgentResult，身份包含plan/item指纹与局部ID。同身份同内容重放合并一次，内容冲突报错；读取时还需匹配当前WorkPlan。仅按work_item_id归并会把跨计划同名任务混在一起。本项目调度器通过Send发就绪任务，ResultBoard负责结果语义；部分状态使用Overwrite明确替换。框架提供合并机制，应用决定事实身份及合法更新。

**继续追问：**合并函数的结合性、顺序依赖和重复输入行为需要验证。不是所有状态都要求交换律，但若宣称并行完成顺序无关，就要有对应性质测试。

**项目定位：**[application/orchestration_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/orchestration_runtime.py)、[application/result_board.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/result_board.py)。

### Q97：checkpoint、Store和业务数据库为什么不是一回事？

**短答：**checkpoint记录一次图执行进度，Store提供按命名空间访问的数据，业务数据库保存有业务含义的状态和回执。

**展开：**同样落在PostgreSQL，不等于可以混用。thread_id找到的是图历史，operation_key指向的是一次业务操作；恢复节点可能重入，所以恢复图不证明副作用只发生一次。当前checkpoint必须具有显式policy、当前计划指纹和带作用域结果；不再接受旧指纹或补齐旧合同。项目用PG checkpointer保存执行位置，业务状态与审批由应用Store和操作账本保存，工具结果归档使用自己的范围与校验规则。

**继续追问：**只修改checkpoint把图标成成功不会真的退款。跨线程记忆也不能覆盖业务系统的最新状态。

**项目定位：**[infrastructure/langgraph_checkpoint.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/langgraph_checkpoint.py)、[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)、[infrastructure/target_result_archive.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_result_archive.py)。

### Q98：Middleware顺序、异常边界和重试为什么重要？

**短答：**Middleware会改变模型与工具调用前后的行为，顺序决定模型实际看见什么、失败时还能保留哪些结果。

**展开：**当前Worker接入版本检查、结果归档、交互边界、进展检查、压缩、输入预算与调用限制。归档需在证据被压缩前保存原文，预算需考虑最终system和工具schema；审批终止段不能被后续普通回答覆盖。SDK自动重试、模型重试和业务重试若叠加，成本可能成倍增加，副作用语义也会混乱。

**继续追问：**模型超时和业务写超时不能同处理：前者要保存已完成工具状态，后者可能进入结果未知/对账。并非所有异常都retryable。

**项目定位：**[infrastructure/target_agent_middleware.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_agent_middleware.py)、[infrastructure/target_context_compaction.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_context_compaction.py)、[core/framework_models.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/core/framework_models.py)。

## 13. 如何防止 Agent 死循环

### Q99：怎么完整回答“你怎么防止Agent死循环”？

**短答：**用进展判定促使策略调整，再用调用次数、图步数、时间与输入预算限制资源消耗；业务副作用由独立状态合同管理。

**展开：**领域AgentProgressMiddleware消费新的ToolMessage，以观察身份判断是否得到新证据。连续两轮无新观察会注入调整策略提示，下一轮仍无进展就结束当前段。另有模型和工具thread_limit=item.max_steps、recursion_limit=item.max_steps*8+10及asyncio.timeout(item.timeout_seconds)。主层的直接观察与缺证据补充也有各自进展/去重逻辑。

**继续追问：**这能限制已覆盖的无进展模式，不保证识别所有语义死循环。必须测重复读取、交替读取、改写不变证据、真正新证据和正常审批等待。

**项目定位：**[application/execution_progress.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/execution_progress.py)、[infrastructure/target_agent_middleware.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_agent_middleware.py)、[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)。

### Q100：“两轮无进展”从哪一轮开始计数？给个例子。

**短答：**首次得到新观察不算停滞；之后连续两个有效工具批次都没有新观察，才发一次策略调整提示。

**展开：**假设首次返回证据A，计数为0；随后仍A，计数1；再A，计数2并warning=true；下一次有效批次仍只有已见证据，blocked=true。若调整后得到B，则连续停滞计数重置。空批次不占恢复轮次；同一个tool_call_id只消费一次，防止模型节点重入时把旧ToolMessage重复计数。

**继续追问：**当前策略基于观察的新颖性，不是完整业务价值评估；很多新但无用的结果仍可能绕过新颖性判断，因此保留硬预算。

**项目定位：**[application/execution_progress.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/execution_progress.py)、[infrastructure/target_agent_middleware.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_agent_middleware.py)。

### Q101：模型换个query或工具返回顺序，怎么避免骗过进展检测？

**短答：**成功知识结果按证据项身份判断新颖性，单纯换query或重新组合旧证据不算新知识。

**展开：**knowledge_progress_identity形成可比较身份，middleware为每个证据项生成摘要；成功知识结果不把改写参数本身作为进展。假设先返回A/B，再返回B/A或A/B的重新打包，集合里没有新项。普通工具还结合工具范围、参数及结果；错误和效果状态也参与观察，不能只比较自然语言表面。

**继续追问：**同参数但订单状态真正改变可能是新观察。失败处理、对账和新用户明确刷新有不同边界，不能全局禁止重复调用。

**项目定位：**[application/knowledge_tool_contract.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_tool_contract.py)、[application/execution_progress.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/execution_progress.py)、[infrastructure/target_agent_middleware.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_agent_middleware.py)。

### Q102：recursion_limit、max_steps和timeout为什么都要？

**短答：**它们分别限制图执行步、模型/工具调用数量和墙钟时间，彼此不能替代。

**展开：**一次图super-step可能包含并行节点，一个模型步骤也可能产生多个工具调用；因此recursion_limit不是LLM次数。工具一直等待时调用数很少但时间超限；大量便宜节点循环可能图步超限。非写执行故障按类型记录，取消/框架中断和损坏的执行合同继续传播；写异常由原账本/接管处理。DIRECT受声明timeout约束，领域保留流式状态和自身预算。可恢复读取故障进入现有主Agent观察重规划，默认4步并检查停滞。用户等待输入或审批应持久暂停，不应靠一直轮询占满预算。

**继续追问：**asyncio超时通常通过取消协程生效，不能证明远端写入被撤销；忽略取消的阻塞操作也不能靠async关键字变成可中断。

**项目定位：**[infrastructure/target_framework_agent.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_framework_agent.py)、[application/turn_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/f5fcfa6b276c3e9090223dbb6a4ecc7c5d9a947c/application/turn_runtime.py)、[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)。

## 14. FastAPI、异步与服务生命周期

### Q103：为什么用FastAPI，和直接写一个LLM脚本有什么不同？

**短答：**FastAPI把模型能力放进有鉴权、输入合同、生命周期和错误接口的HTTP服务。

**展开：**项目需要JWT身份、附件上传、任务提交、响应查询、ACK、指标和管理入口。Pydantic负责请求结构校验，Depends复用身份和权限依赖，lifespan管理启动装配与关闭。一个脚本能跑模型，但不会自动提供多用户隔离、请求幂等、持久任务与断线重放。

**继续追问：**选框架不是性能保证。真正的瓶颈还可能是模型延迟、同步SQL和数据库池等待，需要分阶段测量。

**项目定位：**[api/main.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/api/main.py)、[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)。

### Q104：async def真的能提高并发吗，什么时候会阻塞？

**短答：**协程在可等待的I/O点让出事件循环，适合模型和网络等待；同步阻塞调用放进async函数仍会阻塞。

**展开：**FastAPI调用普通def路由或依赖时可使用线程池，但async路由内部直接调用的普通工具函数不会自动全部卸载。仓库既有异步Redis和模型调用，也有同步psycopg连接池，不能看到async入口就宣称全链无阻塞。CPU推理或大量文本处理还需要独立执行资源与并发上限。

**继续追问：**应沿实际调用点确认是否在线程、进程或异步驱动运行。GIL、CPU占用和事件循环延迟是不同问题；不要一律靠增加worker解决。

**项目定位：**[api/main.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/api/main.py)、[infrastructure/retrieval_postgres.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_postgres.py)、[memory/conversation_memory.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/memory/conversation_memory.py)。

### Q105：lifespan和Depends分别解决什么？

**短答：**lifespan管理应用级资源，Depends管理请求处理中的依赖与校验。

**展开：**连接池和模型客户端适合在启动时构建、退出时关闭，避免每个请求重复连接。鉴权依赖读取Bearer token，返回可信Principal；管理接口再要求scope。应用级对象不等于无限共享可变业务状态，每次请求的用户身份与任务上下文应独立传递。

**继续追问：**多进程部署时每个worker有自己的lifespan、连接池和内存缓存，因此总连接数通常随worker数增长。不能把进程内单例当全局唯一业务Owner。

**项目定位：**[api/main.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/api/main.py)、[core/auth.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/auth.py)、[infrastructure/target_runtime_composition.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_runtime_composition.py)。

### Q106：JWT、401、403以及CORS分别是什么？

**短答：**JWT验证身份声明，401通常表示身份缺失或无效，403表示已识别但无权限；CORS是浏览器跨源访问规则。

**展开：**项目JWT固定HS256算法并核验issuer、audience和必要声明，建立Principal后再检查scope；用户在请求体填user_id不能冒充可信身份。JWT通常是签名而非加密，payload不宜携带秘密。CORS放行某来源也不替代认证，非浏览器客户端不会因此受限。

**继续追问：**短期token过期与主动撤销是另一层设计；不能因为使用JWT就声称已有refresh token轮换、单点退出或防重放系统。

**项目定位：**[core/auth.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/core/auth.py)、[api/main.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/api/main.py)。

### Q107：BackgroundTasks能不能直接跑整个客服Agent任务？

**短答：**它适合响应后的轻量进程内工作，本身不提供持久队列和宕机恢复保证。

**展开：**客服长任务涉及多轮工具、审批和副作用，项目采用持久准入和Run worker。HTTP接收与最终完成分开，进度及结果有持久记录。若只把coroutine塞进BackgroundTasks或create_task，进程退出后任务可能丢失，客户端也难以可靠查询原请求的结果。

**继续追问：**不必据此宣称项目部署了Celery或Kafka；现有持久工作领取机制和这些系统是可比较选项，不是同名实现。

**项目定位：**[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)、[infrastructure/postgres_target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_target_run.py)。

### Q108：连接池、超时、限流与背压怎么一起考虑？

**短答：**连接池限制资源并复用连接；超时界定等待；限流控制准入速率；背压限制在途任务和排队。

**展开：**一次请求可能并行发多个检索与工具调用，因此每秒请求数不等于数据库并发数。连接预算要计入应用worker数、各池上限与管理开销；监控队列等待、池等待、模型耗时、SQL耗时和p95，不能只看总平均延迟。超时后也要辨别任务是否仍在执行。

**继续追问：**滑动窗口、令牌桶、信号量和有界队列是不同控制方式，属于可选设计；没有部署证据时不要说本项目已做完整生产限流。

**项目定位：**[infrastructure/retrieval_postgres.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_postgres.py)、[infrastructure/bounded_retrieval_executor.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/bounded_retrieval_executor.py)、[application/target_run.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/target_run.py)。

## 15. Redis、缓存与一致性

### Q109：Redis在项目中保存什么，为什么不能保存唯一业务真相？

**短答：**Redis用于会话窗口及可重建投影，PostgreSQL保存会话、审批、操作回执与正式答复。

**展开：**读投影时带watermark和projection_status，可识别数据落后或不可用。Redis缺数据不能推出没有待审批动作，也不能让历史缓存中的“可退”覆盖最新业务状态。缓存服务恢复后应从权威事实重建，而不是让两个存储彼此覆盖、争夺哪个状态算最新。

**继续追问：**这不是说Redis不能持久化，而是本项目主动选择了数据所有权。RDB/AOF也不自动等价于跨业务工具的事务与确认语义。

**项目定位：**[infrastructure/target_turn_context.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_turn_context.py)、[memory/conversation_memory.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/memory/conversation_memory.py)、[infrastructure/postgres_conversation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_conversation.py)。

### Q110：缓存穿透、击穿、雪崩怎么区别，怎么处理？

**短答：**穿透是请求不存在的数据仍频繁访问后端；击穿是热点key失效引发并发回源；雪崩是大量key或缓存服务同时失效。

**展开：**可选方法分别包括输入校验/短期负缓存，单key合并回源或互斥重建，TTL抖动、错峰过期和有界降级。但知识和用户私有信息还必须按身份、scope和generation区分key；不能把某用户的“无结果”缓存给另一用户。负缓存时间过长也会掩盖刚新增的资料。

**继续追问：**这些是经典方案，不应仅因项目用了Redis就声称全部实现。回源保护需要成本与负载证据，不能靠超长TTL掩盖数据过期。

**项目定位：**[infrastructure/retrieval_cache.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_cache.py)、[application/knowledge_retriever.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/knowledge_retriever.py)。

### Q111：数据库更新后删缓存就强一致了吗？

**短答：**不能。并发读回填、删除失败和传播延迟都可能留下旧值，需要按一致性需求设计版本与重建。

**展开：**典型竞争是读请求拿到旧数据库值，写请求提交并删缓存，随后旧读又回填。知识缓存可以把generation和来源版本放进身份；会话投影跟踪watermark，从持久事件重建。版本帮助识别过期，不意味着Redis与PG成为一个原子事务。审批和写入最终仍读权威状态。

**继续追问：**延迟双删是缓解手段，不能证明所有竞争都消失。需要强一致的关键决策应在权威存储和同一合同上完成。

**项目定位：**[infrastructure/target_turn_context.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/target_turn_context.py)、[infrastructure/retrieval_cache.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_cache.py)、[infrastructure/postgres_projection.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_projection.py)。

### Q112：Redis事务、Pipeline、WATCH和Lua有什么差别？

**短答：**Pipeline主要减少往返；MULTI/EXEC把命令按事务队列执行；WATCH提供乐观条件检查；Lua在服务器端原子执行一段逻辑。

**展开：**Pipeline是否事务化取决于客户端参数，不能只看调用名。WATCH后被监视键改变，EXEC可能不执行，要处理冲突；Redis事务不像关系数据库那样对所有运行期错误自动回滚。Lua适合短小原子更新，但长脚本会阻塞服务。仓库会话记忆层使用事务pipeline等机制，不代表整个客服流程在Redis事务里。

**继续追问：**跨Redis与PostgreSQL没有因为用了Lua就自动原子。业务状态仍由原持久Owner定义，投影操作只是更新缓存。

**项目定位：**[memory/conversation_memory.py](https://github.com/Garrulus21yyx/DialogPilot/blob/c91eae259f2c3f96a981acbac42a56de0c60b33e/memory/conversation_memory.py)。

### Q113：Redis分布式锁能保证退款只执行一次吗？

**短答：**不能。锁租约可能先于远端操作结束，旧执行者仍可能继续，因此需要业务幂等与回执。

**展开：**经典锁用带唯一token的SET NX PX获取，并校验token再释放；仅DEL可能误删别人的锁。即使释放正确，暂停、网络延迟或租约过期也可能出现旧执行者。需要资源侧识别版本/fencing或幂等身份；本项目业务写以PG操作记录、CAS和业务回执为依据，不把Redis锁当唯一执行证明。

**继续追问：**Exactly-once是明确边界内的结果保证，不是部署一个锁就获得的全局性质。不同operation_key是否代表同一业务动作仍需业务合同决定。

**项目定位：**[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)、[infrastructure/postgres_target_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_target_runtime.py)。

## 16. SQL、PostgreSQL与持久任务

### Q114：ACID、MVCC与事务隔离级别怎么联系项目讲？

**短答：**ACID约束事务行为，MVCC通过版本与快照支持并发；隔离级别决定事务能观察到哪些并发变化。

**展开：**PostgreSQL默认Read Committed通常每条语句取新快照，不能把先查后改自动视为不可竞争。Repeatable Read提供更稳定快照，Serializable更强但可能要求重试序列化失败。项目的审批和状态转移要结合唯一约束、条件更新和必要行锁，不依赖“我在事务里读过一次”。

**继续追问：**更高隔离级别不解决外部HTTP副作用原子提交。重试整个数据库事务时必须确认其中有没有已经发生的远端写入。

**项目定位：**[infrastructure/postgres_conversation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_conversation.py)、[infrastructure/postgres_target_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_target_runtime.py)。

### Q115：乐观锁CAS和SELECT FOR UPDATE怎么选？

**短答：**CAS在UPDATE条件里检查旧版本或状态；FOR UPDATE先锁住目标行，后续竞争者需要等待或按策略跳过。

**展开：**示意SQL：UPDATE operations SET status=:new, version=version+1 WHERE id=:id AND version=:expected。影响0行说明前提不成立，不能当成功。行锁适合需要读取再更新同一聚合的短事务；持锁期间调用LLM会让锁等待膨胀。项目PG存储使用条件状态转换与必要FOR UPDATE，具体隔离边界按方法核对。

**继续追问：**锁顺序不一致可能死锁；减少事务时长、统一访问顺序并处理数据库报告。不要在应用层无限重试冲突。

**项目定位：**[infrastructure/postgres_target_runtime.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_target_runtime.py)、[infrastructure/postgres_conversation.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_conversation.py)。

### Q116：请求幂等、操作幂等、唯一索引分别做什么？

**短答：**请求幂等防止重复提交同一请求；操作幂等防止恢复时重复执行同一业务副作用；唯一约束阻止数据库写出重复身份。

**展开：**用户重发同一request_id不应产生第二次独立任务；同一任务恢复时复用operation_key与已提交回执。仅先SELECT再INSERT有并发窗口，唯一约束和冲突处理才由数据库裁决。ON CONFLICT DO NOTHING也不能直接解释为“此次完成”，要读取原记录并核对其状态与内容。

**继续追问：**同key不同参数需要显式处理，不能悄悄复用错误结果。不同key的重复业务意图是否合并属于业务层问题。

**项目定位：**[infrastructure/postgres_admission.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_admission.py)、[application/write_workflow.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/application/write_workflow.py)。

### Q117：事务Outbox解决什么，是否保证消息只投递一次？

**短答：**它让业务记录与待发送事件在同一数据库事务中保存，避免业务提交了但事件根本没记录。

**展开：**发布器之后读取outbox并发送；若发送成功后、标记前进程崩溃，可能重复发送，所以接收侧要幂等或去重。项目持久准入、工单等路径有事件/outbox概念，不能把每个外部系统都描述成已接可靠消息中间件。关键是状态与待办先原子落库。

**继续追问：**Outbox通常解决可靠记录与至少一次交付边界，不自动带来全局严格顺序或跨服务Exactly-once。

**项目定位：**[infrastructure/postgres_admission.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_admission.py)、[infrastructure/postgres_ticket_service.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_ticket_service.py)。

### Q118：B-tree、GIN、HNSW各适合什么，联合索引怎么考虑？

**短答：**B-tree常用于等值、范围与排序；GIN适合多值项的倒排匹配；HNSW用于近似向量邻居搜索。

**展开：**会话按conversation与seq读取可考虑匹配过滤和排序的联合B-tree；知识lexical_terms可利用GIN找到词项命中集；向量相似度使用pgvector索引。联合索引顺序要结合等值范围、排序和选择性验证，不能把“最左前缀”口诀当所有计划的完整解释。索引增加写入、空间和维护成本。

**继续追问：**创建索引不保证查询使用它，也不保证更快。ANN还需测试近似召回损失和过滤后的结果数量。

**项目定位：**[infrastructure/retrieval_postgres.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_postgres.py)、[infrastructure/postgres_knowledge_store.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_knowledge_store.py)、[infrastructure/postgres_response_delivery.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_response_delivery.py)。

### Q119：EXPLAIN ANALYZE怎么看，项目慢SQL怎么定位？

**短答：**先对比估计与实际行数、节点loops、扫描/过滤、排序和buffer，再区分数据库执行与连接池等待。

**展开：**cost是优化器相对代价，不是毫秒；ANALYZE实际执行查询，写语句不能当纯只读诊断。项目BM25曾因完整scope关联行数被严重低估而反复回读；先保持范围与评分语义，改变词项命中集访问路径，仍有常见词统计成本。原生FTS更快但另一批Wix质量回退，说明性能与排序语义需要各自验收。

**继续追问：**不要把禁止nested loop、延长timeout、删过滤作为默认修复。先找错误估计、重复工作或真实数据量，再做同结果性能对照。

**项目定位：**[infrastructure/retrieval_postgres.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/retrieval_postgres.py)、[docs/rag-bm25-planner-diagnosis-2026-09-09.zh-CN.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/rag-bm25-planner-diagnosis-2026-09-09.zh-CN.md)、[docs/rag-native-fts-acceptance-2026-09-09.zh-CN.md](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/docs/rag-native-fts-acceptance-2026-09-09.zh-CN.md)。

### Q120：分页、响应序号、迁移和备份恢复有哪些容易追问的点？

**短答：**增量读取适合基于稳定游标或序号；迁移变更结构，备份恢复验证数据可还原，两者不能互相替代。

**展开：**按response_seq继续读取比深OFFSET更贴近断线续取，但游标需带会话范围与稳定顺序，不能跨用户查。Alembic迁移要考虑已有数据、锁和应用兼容；dump成功不等于restore成功，应在隔离库恢复并对账。项目有响应序号/ACK和恢复演练入口，但历史演练不代表当前版本或生产RPO/RTO。

**继续追问：**迁移事务也可能造成长锁；线上索引构建、回填和切换要有专门方案，不能把本地空库迁移命令直接当零停机升级。

**项目定位：**[infrastructure/postgres_response_delivery.py](https://github.com/Garrulus21yyx/DialogPilot/blob/9a50ea1391e19517335fe2ff00ecc0c702904197/infrastructure/postgres_response_delivery.py)、[scripts/run_postgres_migrations.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/scripts/run_postgres_migrations.py)、[scripts/rehearse_x_t01_restore.py](https://github.com/Garrulus21yyx/DialogPilot/blob/89feac2e63b31113530864814188f0a4a61708bc/scripts/rehearse_x_t01_restore.py)。

## 17. 官方原理与继续阅读

本专题在2026-09-09核对官方机制；项目实现已核对至f5fcfa6，通用机制引用保留原核对日期，通用设计不自动等于项目已实现。

- [LangChain模型与工具调用](https://docs.langchain.com/oss/python/langchain/models)
- [LangGraph图、状态与Reducer](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [FastAPI异步并发](https://fastapi.tiangolo.com/async/)、[生命周期](https://fastapi.tiangolo.com/advanced/events/)、[后台任务](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [Redis锁的正确性边界](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)
- [PostgreSQL事务隔离](https://www.postgresql.org/docs/current/transaction-iso.html)、[执行计划](https://www.postgresql.org/docs/current/using-explain.html)

RAG继续读[离线在线、HNSW、权重、Rewrite、精排与评测专题]({{ '/rag-study.html' | relative_url }})；原有追问Q31—Q44讲检索机制，Q45—Q50讲生成核验，Q57—Q66讲数据与评测。

## WorkPlan与框架如何连接

[Compiler、WorkPlan、TaskGraph、ResultBoard与LangGraph逐层讲述]({{ "/architecture.html#workplan-contract" | relative_url }})包含三任务执行示例、Reducer身份、四种完成口径、批准记录与恢复检查；[追问Q135—Q142]({{ "/interview-guide.html" | relative_url }})练习为什么没有第二个TaskGraph类、为什么恢复不能补猜任务范围。

## 故障重规划与局部续接

[完整机制与失败分类]({{ "/architecture.html#worker-recovery" | relative_url }})区分SDK传输重试、主Agent重新决策与业务对账；[多目标待答保留]({{ "/architecture.html#objective-conservation" | relative_url }})解释ConversationState、理解层、Manager和checkpoint如何一起保留独立任务。
