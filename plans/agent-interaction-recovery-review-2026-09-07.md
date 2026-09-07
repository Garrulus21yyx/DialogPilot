# Agent 交互、观测与失败恢复收敛

状态：review_in_progress；本文件记录诊断与实施合同，不代表实现或验收完成。

2026-09-07 观测收敛（implemented / remote_verification_pending）：撤掉 ModelDiagnostics/UserModelDiagnostics 通用采集器，模型/工具记录交给已锁定 Langfuse 4.15.1 官方 CallbackHandler；沿用业务 TraceSink，统一配置与关闭。官方 mask 扩展点应用项目脱敏策略。本地评测保留官方成绩、业务结果与 Langfuse session 关联。当前环境未配置 Langfuse 凭据，服务端发送/读取验收不可声称完成。

## 范围与证据

用户要求简化回复/追问，补齐内部记录与失败反馈，并处理无进展循环；不引入另一套 Agent 引擎、商品类别规则或全局重置。

- v14 两条 τ³ 开发任务未完成换货写入，官方 reward=0；存在普通 end_turn 无 DomainOutcome。
- TargetFrameworkAgent 强制 ToolStrategy(DomainOutcome)，适配器缺少该结果即 TERMINAL_FAILURE。框架退出与应用终态合同不一致；不将其全部归因于模型或预算。
- evaluation/tau3_full_adapter.py 的 ModelDiagnostics 仅记录 stop_reason、usage、tool_names、has_content；无法重建失败步正文。core/tracing.py、PostgresTraceSink 已存在，不能称整个项目没有埋点。
- TargetAgentMiddleware 已有目标版本检查与上下文预算检查；ModelCallLimit/ToolCallLimit 是成本上限，不是进展检测。
- _tool_output 将工具失败反馈给模型，但跨执行失败诊断、已尝试策略与恢复进度没有形成统一续接合同。不能将 retryable 字段当作已经执行重试。
- 第 1 条轨迹反复查询并输出失败文案，用户要求人工未完成实际转接。第 2 条已复用事实并计算差价；后续草稿金额方向自相矛盾。未知原模型正文不能用草稿代替。

## 正向合同与 Owner

1. 模型正常最终文本是 ResponseCandidate，不要求模型额外提交终态 JSON；业务完成由事实、操作 Receipt 与需求覆盖决定，不由一句文本或模型停止决定。
2. request_user_input(question) 表达交互意图。运行时注入目标、版本和身份；既有 PendingInteraction/Publication 负责正式追问。用户回答恢复同一子任务工作消息，不只是重新发原目标。正式审批保持独立绑定。
3. create_agent/LangGraph 继续拥有模型—工具循环与 checkpoint；不得并存另一套自写 resume。子图 interrupt 不能被宽泛异常捕获转成业务失败。父图须支持并行未受影响任务、统一问题提交与持久恢复。
4. 原始工作消息保存在框架执行持久记录；Trace 保存模型调用、工具调用、交互、异常与恢复关联引用。受控诊断保存模型可见正文/工具请求结果（按敏感数据策略），不导出隐藏推理、凭证或完整客户资料到公开评测文件。缓存/摘要不替代原记录。
5. 失败反馈由产生失败的工具/验证器给出：失败种类、可修正条件、原调用引用、副作用是否已提交。Agent 负责改变方案；执行层只重试明确可重试的暂态故障。未知写入走原操作对账，取消/SUPERSEDED 不重试。
6. 无进展由现有工作状态、有效事实、待解决问题与 Receipt 的变化提供信号；不按自然语言相似度或调用次数假定进展。相同参数重复查询是信号，不自动代表失败：显式刷新、轮询、有效期及前一故障均需考虑。
7. 连续重复无新增有效结果时，先向当前 Agent 投递已做事项/错误/约束，允许有预算的方案修正；仍无进展则返回可诊断阻塞并将控制交回会话层。目标级范围，不清空全局会话、不抛弃成功任务、无重试层数相乘。
8. 金额来自业务数据；派生计算用确定性代码（Decimal/最小货币单位），无需新增外部服务。真实应收/应退金额和方向以业务报价/结算政策为权威；回复不得把有符号差额改为相反方向。负差额不在无政策证据时自动承诺退款，实际执行以 Receipt 为准。

## 最小实施顺序

- implemented A（SDK 集成）：API 与 τ³ 使用同一 LangfuseTraceSink 配置入口，为 create_agent 注入官方 CallbackHandler；保留 work/control/revision/invocation metadata 和 session。删除自写模型/工具回调，不在本地评测另存一套通用调用记录。mask 覆盖字典、消息模型、Command 数据类和嵌套列表；SDK 负责采集、层级、序列化与导出。未配置凭据时不自动外发，旧轨迹缺失正文无法补回。脱敏规则不是任意自然语言 PII 的完备识别器。
- pending B：迁移 DomainOutcome 消费者为正常文本候选与显式交互工具；连同 PendingInteraction、子图恢复、父图结果合并、Publication 和测试一起迁移，删除旧强制出口。禁止只删失败分支。
- pending C：连接类型化错误反馈和目标级失败恢复上下文，再加入有限的无进展响应。保留既有成本预算；不新增每步 LLM supervisor。
- pending D：梳理现有金额报价/计算/执行/表达的数据归属，检查方向与币种；无业务结算工具的外部基准不伪造报价服务或按题目增加规则。

## 可证伪验收

第一项替代验证：真实 Langfuse SDK + OpenTelemetry InMemorySpanExporter 执行框架工具循环，验证正常/缺少 DomainOutcome 两条路径均导出可见正文、generation/tool/agent 层级和任务关联，且秘密与 reasoning 被过滤；这不是自写模拟 callback。与 PostgreSQL Trace、框架、工具安全回归组合 40 passed；官方 τ³ 适配测试另测。没有 Langfuse 服务端凭据，不能提供真实服务端 trace 链接，也未重新跑 τ³ 性能实验。下一项 B 仍 pending。

- 普通文本可形成候选，假称业务写入成功仍不能发布；文本未包含终态 JSON 不直接导致任务失败。
- ask -> 进程重启 -> answer 返回原暂停任务，仍有效查询只执行一次；与目标修正/取消分开。
- 对错误参数反馈后可以修正调用；重复相同错误有界停止；不同结果的合法多步查询不误判无进展。
- A/B 查询循环无新证据时收敛；用户明确刷新允许再次读取；并行无关任务结果保留。
- 模型异常、工具失败、验证失败与恢复使用同一关联身份，可回查实际正文而非 has_content；脱敏测试阻止密钥和敏感身份泄漏。
- 正/负/零差额、币种差异、费用与折扣、提交前报价变化用参数化/属性测试；金额及方向表达不偏离权威报价。
- 两条现有任务仅作开发回归；另用未参与修复的任务与独立复核验证，不把一次成功宣布为闭环。

## 参考与取舍（检索日期 2026-09-07）

- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts — 暂停/恢复机制，不替代业务审批和 Publication；当前依赖版本需本地核对，不直接采用最新文档的新 API。
- LangChain middleware: https://docs.langchain.com/oss/python/langchain/middleware/built-in — 复用调用预算和适用的错误反馈/重试扩展点；并非通用业务进展检测器。
- LangChain tracing: https://docs.langchain.com/langsmith/trace-with-langchain — 框架调用关联，优先接现有观测设施，不能因有 checkpoint 就断言可观测闭环。
- Reflexion: https://arxiv.org/abs/2303.11366 — 失败反馈参与后续尝试；不是无证据的多次“再想想”。
- Structured Reflection (v3, 2026-04-15): https://arxiv.org/abs/2509.18847 — 训练与基准研究，不能宣称加入提示词便复现其收益。
- Progress mirage (2026-07-27): https://arxiv.org/abs/2607.25152 — 小规模预注册研究，对外部结果与自评的差异提供证据；不作为普适 SOTA 保证。
- Effective harnesses: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents — 继承进度与外部验收；编码场景经验只作设计参考，不直接迁入重型工程框架。

选型依据是职责、恢复语义和可验证行为；当前不存在经同预算电商基准证明本项目方案为 SOTA 的证据。
