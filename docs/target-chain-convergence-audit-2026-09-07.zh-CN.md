# Target 主链收敛审查：职责、框架复用与交付语义

日期：2026-09-07。基线：7f57ed8，相关工作树修改单独注明。本次仅静态读取源码和官方文档，未运行测试、模型、τ³ 或故障注入，未修改生产代码。以下是整改建议，不是业务闭环或恢复可靠性的验收证明。

## 1. 结论与实际结构

保留现有主链，不重建 Agent 框架。主要问题是协议处理重复、交互结果缺少一致的组合语义，以及执行成功、答案可信和交付成功混用了状态。

```text
Admission / 后台 Run
  → Manager 加载状态与上下文
  → 确定性续接 / Encoder / ConversationAgent.plan
  → Policy + Compiler
  → LangGraph WorkPlan
      DIRECT：工具
      DELEGATED：create_agent + 共享 middleware
      WORKFLOW：受控操作
  → ResultBoard / 待处理交互
  → 模板、领域候选或按需 compose
  → 答案检查
  → Publication / Delivery / Transcript
```

装配见 [target_runtime_composition.py](../infrastructure/target_runtime_composition.py)，领域循环已经使用 [create_agent](../infrastructure/target_framework_agent.py:106)。不用再迁一次“所有 Agent 到 LangGraph”，也不需要每轮强制多 Agent。

审查采用架构与 Agent 模式 skill 做职责检查，不采用其示例中的自写执行引擎或事件总线。用户现有框架优先的约束决定了取舍。

## 2. 证据与整改决定

### A. 结构化模型调用：交给现有 SDK，保留业务校验

[target_conversation_provider.py:142](../infrastructure/target_conversation_provider.py:142) 直接调用供应商 messages.create，手动要求 stop_reason、唯一 tool block、工具名，再验证 JSON。claim_verification 另有类似结构化调用协议；领域 Agent 则已使用框架。

影响：同一种模型协议错误在多个入口有不同处理，规划、合成与核验也不容易共享 callback。

建议：规划和必要的结构化评判使用当前模型集成提供的 structured-output 能力；核对锁定版本和实际供应商兼容性后复用，而不是另造通用解析器。保留计划语义、权限和参数来源校验。普通子 Agent 回复保持自然语言，不重新强制包装 DomainOutcome。

### B. 在线回复核验：过重且边界不一致，应简化

[response_assembly.py:99](../application/response_assembly.py:99) 对非知识分支的 compose/pass-through 也执行语义核验，可能再生成和再核验；失败统一进入 ANSWER_SAFE_FALLBACK。[claim_verification.py:125](../services/claim_verification.py:125) 要求精确文本 span、证据路径和文本覆盖。这能校验评判产物的形式，但不能证明模型判断正确。

同时，[turn_runtime.py:79](../application/turn_runtime.py:79) 遇到待补输入或对账会跳过组装，追问走另一条出口。因此不是“核验越多越安全”，而是普通表达承担复杂协议、交互表达却用不同检查边界。

建议：

- 普通回复和追问允许原生文本；不要为了说一句话强制生成段落、引用定位与评判 JSON。
- 业务授权、对象绑定、操作状态、Receipt 和来源撤回检查保持确定性。
- 需要时检查金额方向、政策结论、执行承诺等重要声明与权威证据的一致性；不要通过删检查把幻觉变成可发布结果。
- 细粒度全文 judge 优先用于离线评测或 shadow 审查。是否在线启用，以风险和实际收益决定。
- 模型不可用、输出格式错误、确有不支持声明分开记录；能交付的独立结果应保留。

这是针对当前代码的工程建议，并非官方要求所有低风险回答一律免检。

### C. 公开质量状态：必须修正含义

[target_chat_application.py:370](../application/target_chat_application.py:370) 从 requirement 缺失和冲突计算 verifier_status；[同文件:491](../application/target_chat_application.py:491) 再映射 complete、verified、grounded。答案检查失败后的安全模板也可能标 PASS。

根因：任务证据覆盖、答案检查、用户目标完成度被折叠成一个 PASS。安全地说明失败，不等于业务成功；没有 requirement 缺失，也不证明回复 grounded。

建议：复用已有执行 outcome、检查 verdict、Delivery 状态各自的 Owner，公开字段忠实投影，禁止从另一维推导。没有实际检查就记录未检查，不能包装成通过。不需要新建状态平台。

### D. 追问、审批和部分成功：补组合语义，不按业务写分支

[action_approval.py:31](../application/action_approval.py:31) 新审批选择 proposed[0]；已有审批时按 checkpoint 挂接其他任务。[turn_runtime.py:89](../application/turn_runtime.py:89) 任一 NEEDS_USER_INPUT / RECONCILING 会跳过整轮组装。

当前并行执行支持多个结果，但交互层没有同等清晰的组合约定。不能据此断言所有剩余结果丢失；能确认的是表达、排队和恢复依赖分支优先级。

目标合同：

- 子 Agent 提出问题及其任务绑定；主对话层只在合并、消歧或统一表达需要时参与。
- 一个已完成结果加一个待输入结果，公开回复同时保留已完成部分和问题。
- 多个待处理动作要么显式排队并保存身份，要么在派发前按声明限制串行；不能靠汇总时取第一个定义行为。
- 用户可以拒绝或修正待执行动作；必要参数变化使旧授权失效。已发出的写入转向 Receipt / 对账。
- 等待、拒绝、取消、改目标分别恢复目标任务，无关任务不受影响。

这些是少数通用交互语义，不是每类商品、退货理由各写一个 Flow。

### E. 审批说明：机械约束与自然语言应分离

[response_assembly.py:495](../application/response_assembly.py:495) 用合成的 Approval description 需求接入全文 question coverage；发布再绑定通过核验的文本 hash。这让确认是否可交付依赖额外的措辞/引用协议。

保留 action_ref、确定参数、金额/方向、operation_key、revision 和确认有效期。用户看到的自然语言或确认卡片从同一待执行动作生成；展示条款与授权必须指向同一版本。框架负责中断恢复，业务边界负责是否有权提交，不让一句模型“验证通过”成为授权事实。

原始 JSON 是机器合同，不是用户文案。采用 SDK 不会自动得到合格的电商确认说明；这里仍需要薄的展示适配，但不应再维护一套通用工具参数解析协议。

### F. 阻塞、重试与无进展：已有机制，错误反馈需收敛

[target_agent_middleware.py](../infrastructure/target_agent_middleware.py) 已有框架调用预算、重复观察检测、目标版本检查；[conversation_agent.py:166](../application/conversation_agent.py:166) 已有一次有界恢复建议。因此不应新增另一套 supervisor 重试引擎。

仍有两个问题：重复结果 hash 只能说明“观察重复”，不能证明业务无进展；[target_framework_agent.py:163](../infrastructure/target_framework_agent.py:163) 的通用异常被归到 AGENT_PROVIDER_FAILURE，可能掩盖适配或程序错误。[answer_verifier.py:193](../services/answer_verifier.py:193) 也丢失较细的错误位置。

建议：在原异常边界保留失败阶段、工具/操作身份、是否可重试、已完成结果及下一步需要什么。基础设施的短暂读取失败由执行层有限重试；模型只接收可行动反馈并决定换办法、问用户或交回。写入结果未知只对账。停止旧循环不应清空有效进度。

工作树 mcp/tool_manager.py 与 τ³ binding 已有 ToolRejected 未提交改动：业务拒绝与未知写结果的区分方向合理，但本次没有验证，也不能写成 HEAD 已完成能力。

### G. 持续记忆与恢复：保留已有机制，核对持久化接缝

[postgres_memory_projection.py:77](../infrastructure/postgres_memory_projection.py:77) 已在投影落后或缓存不可用时补读原始 Transcript。不能沿用“PG 写、Redis 读，所以必然忘记上一轮”的旧诊断。

[orchestration_runtime.py](../application/orchestration_runtime.py) 续接同时涉及 checkpoint、continuation facts 与 working_messages；事实会筛有效期，但历史工具消息仍可能包含旧结果。历史可保留，不应自动成为当前事实。

需进一步证明的性质：业务状态先提交、图 checkpoint 后提交的窗口重放是否幂等；恢复只保留有效证据且不重复执行已完成写入；正式 Transcript 与工作消息不相互冒充。这里是待验证的恢复合同，不在未跑重启测试时宣称已发生数据丢失。

### H. Run / SSE：结构已合理，不在此次换服务平台

[target_run.py](../application/target_run.py) 有租约、心跳和执行接管；[api/main.py:1890](../api/main.py:1890) SSE 读取持久公开事件并支持 Last-Event-ID，断开只结束订阅。

保留，不因它含有 while 循环就称为自写 Agent 运行时。Agent Server 是以后可评估的服务层替代，不是本次必须引入的依赖。真实崩溃、租约争用和重复送达仍需另行验证。

### I. RAG：检索与来源治理保留，表达链合并

[knowledge_retriever.py:696](../application/knowledge_retriever.py:696) 已有缓存 TTL，[同文件:608](../application/knowledge_retriever.py:608) 有精排降级记录。证据来源、适用范围和撤回语义属于业务，不应为了少写代码交给通用 LLM 判断。

应合并的是知识结果与其他结果的用户表达、失败说明和可信度投影，而不是把自有来源规则删掉。内部调用无需为架构美观强制绕 MCP。依赖级熔断效果和缓存收益不属于这次静态审查已证明的结论。

### J. 观测与评测：统一观察实际调用，不另造埋点引擎

领域 framework Agent 与原生 planning/compose/verifier 的调用入口不同。需要同一 invocation 下的阶段、输入输出摘要、异常类型和 token/latency 关联；采用已有官方 callback/SDK 扩展点，保留脱敏。不能把某阶段没有出现在某次 session 查询中直接断言为全局没有 trace。

τ³ 是环境适配下的业务任务评测，不自动覆盖生产 HTTP、SSE、多领域派发或重启。ERROR/未评分、官方得分 0、安全拒绝、已成功写入应分别报告。既有运行只能作为失败证据，不能充当此次改动后的新成绩。

## 3. 共享根因与边界

这些问题不是独立地少几个 if：

1. **协议层重复**：多个原生模型入口各自解释输出，放大格式错误并割裂观测。
2. **结果含义混合**：执行、待处理交互、答案质量和交付被不同分支二次解释。
3. **以形式替代效果证明**：严格 JSON/span 合同和局部回归通过，并不能证明任务推进、自然回复与真实业务终态。

模型确实可能产生错误业务推断，不能全部归罪核验；核验拒绝也可能正确。上述静态证据不支持“删掉 Verifier，τ³ 就会成功”的结论。

## 4. 与官方成熟实践的对照

截至审查日期，以下是参考实践，不是统一 SOTA 排名：

- LangChain 提供 ProviderStrategy / ToolStrategy 管理结构化输出；其价值在于接管模型协议，不替代业务事实校验。[Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)
- HITL 按工具策略暂停并恢复，动作与决定有明确对应；这支持将通用等待交给框架，应用保留授权含义。[Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- Guardrails 区分确定性与模型检查，并强调战略边界；模型检查有额外延迟和成本。选择性在线核验是本项目据此作出的设计取舍。[Guardrails](https://docs.langchain.com/oss/python/langchain/guardrails)
- Checkpointer 保存线程图状态，Store 服务跨线程数据；已有 Transcript / Receipt 仍有独立业务价值。[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- 从最简单可用方案增加复杂度，开放任务由模型动态选择工具，固定工作流用于明确步骤。[Anthropic engineering](https://www.anthropic.com/engineering/building-effective-agents)

版本注意：当前锁定依赖与在线最新版不同，例如当前文档标注条件 HITL when 需要 langchain>=1.3.3。不能直接复制最新参数；先核对兼容能力，再决定小范围升级，不能为避免核对另写整套 SDK。

## 5. 最小整改顺序与退出条件

| 阶段 | 改动 Owner | 正向合同及后续验证 |
|---|---|---|
| 1. 状态与诊断 | Result/Verification/Publication 投影、模型调用边界 | 执行失败不会投影成业务成功；未检查不会投影成 grounded；失败保留可定位阶段 |
| 2. SDK 收敛 | 现有 ConversationProvider 与结构化评判入口 | 同一 SDK 解析模型产物，业务 schema 不丢；普通文本不强制复杂输出合同；不保留第二套自动 fallback |
| 3. 回复与交互 | ResponseAssembler、已有 Pending 状态/approval owner | 单结果可直出，多结果按需组织；完成+等待+失败有明确组合；确认与精确动作绑定 |
| 4. 续接闭合 | Manager、LangGraph 边界、操作记录 | 新输入只恢复目标任务；过时结果不成为当前事实；重复恢复不产生重复副作用 |
| 5. 运行证明 | 现有测试与 benchmark adapter | 状态组合/property 测试、真实 PG 重启、代表性生产链路，再做固定预算 fresh τ³；区分实现完成与验收通过 |

当前不执行第 5 阶段。后续测试应跨 Owner 证明性质，而非每次挑一条失败用例补一个特化分支。独立新上下文复核及新样本通过前，不恢复“全部闭环”状态。

非目标：不换多 Agent 架构、不扩增商品类别 Skill、不重写 RAG/Memory、不新建调度器/事件总线、不强制所有内部工具 MCP 化、不因文件行数拆成微型接口、不迁移后台服务平台。
