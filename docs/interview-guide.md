---
layout: default
title: DialogPilot 面经校准与追问手册
---

# DialogPilot 面经校准与追问手册

> 独立面试作战页：以旧 EchoMind 面经为题库，逐项对照当前 DialogPilot 代码。只讲仓库能证明的实现，工程初值不包装成实验最优，未实现的生产能力明确标出。

## 面经使用说明

| 标记 | 含义 | 回答方式 |
|---|---|---|
| **CURRENT** | 旧答案仍与当前代码一致 | 讲 Owner、链路、取舍和边界 |
| **CHANGED** | 旧版后已改造 | 先讲现状，再讲旧问题与修复 |
| **UNPROVEN** | 数字或能力无法复现 | 直说未建 benchmark，不编结果 |
| **NEW** | 旧面经没有，当前已实现 | 用合同、失败语义和测试证据回答 |

> 建议答题模板：先一句结论，再讲数据流，然后说决策理由，最后主动交代代价和未实现边界。

## 旧面经差异总账

| 旧口径 | 当前代码事实 | 结论 |
|---|---|---|
| EchoMind、3 个业务 Agent | 已更名 DialogPilot；General / Technical / Billing / AccountSecurity / Escalation 五个真实 Worker | **CHANGED** |
| 并行回答直接拼接 | `TaskGraph → typed outcomes → CoverageGate → ResultSynthesizer → AnswerVerifier` | **CHANGED** |
| 消息超过 15 条压缩 | 6000 Token 预算、70% 触发；原始事件追加保留，摘要块覆盖固定 seq 范围，CAS 只推进 checkpoint | **CHANGED** |
| 长期记忆只检索摘要 | 检索 1200 字符/120 overlap 原始片段，摘要只是背景 metadata | **CHANGED** |
| 知识库是 BM25 + 向量 + RRF | 这是长期记忆；知识 RAG 仍是 rewrite + Chroma 多路向量 + 去重 + LLM rerank | **CORRECTED** |
| Agent 单次生成、无完整 Trace | Worker 内最多 4 步 ReAct，allowlist、持久审批 Resume、脱敏 audit/TraceId | **CHANGED** |
| Bad Case 后人工直接改 Prompt | 版本归因 → 不可变 Bundle 候选 → Graduation/Pareto → Shadow/Canary/回滚 | **NEW** |
| 准确率 91.3%、综合分 0.89 | 当前有 500 条分层候选集、25 篇 corpus 和 265 项回归测试，但仍无 human-reviewed gold | **UNPROVEN** |
| 完整 MCP Server / LangGraph | 是内部 ToolManager 与直接 Python 编排；没有远程 MCP Server，没用 LangGraph | **UNPROVEN** |

## 项目开场与完整链路

### Q1：用 60 秒介绍这个项目。

**CURRENT。** DialogPilot 是 Python/FastAPI 多 Agent 客服后端。它固定请求级 AgentBundle，读取分层记忆，用 LLM、本地字符 n-gram 和规则识别意图，按需检索业务知识，再生成带依赖、上下文范围、Owner、风险和验收条件的 TaskGraph。领域 Worker 按拓扑波次执行，内部用有界 ReAct 调白名单工具，写操作可持久等待审批并恢复。结果经覆盖、融合和 fail-closed Verifier 后才发布；失败进入带版本归因的 Bad Case，候选必须离线晋级和灰度。

### Q2：你个人改了什么？

说“接手已有客服原型”，不说从零原创。可用 commit 证明的改造包括：命名与仓库收敛、持久工单与事务 outbox、客户端回答 ACK/断线续取、Token/CAS 压缩、typed synthesis、TaskGraph/CoverageGate、混合长期记忆、用户输入 Guard、工具权限/Trace、有界 ReAct 与审批 Resume，以及不可变 Baseline/AgentBundle、受限候选、Graduation/Pareto 和灰度回滚。

### Q3：`/chat` 端到端经过哪些节点？

`TraceId → 用户输入 Guard → 固定 Rollout/Bundle → Redis 工作记忆/混合情景记忆/画像 → 意图与实体 → PlanningDisposition；CLARIFY/OUT_OF_SCOPE 直接发布固定策略回复，只有 EXECUTE 继续有条件 RAG → TaskGraph 波次 → Worker/ReAct/审批 Resume → Coverage → Synthesis → Verifier → 工单+outbox（按需）→ 持久化 response_id/seq → 写客服会话答案 → HTTP 返回 → 客户端 ACK → 失败版本归因。`

### Q4：为什么不用 LangChain / LangGraph？

**CURRENT。** 当前领域和状态代数闭合，直接 Python 更容易看清 TaskGraph、预算、outcome 和失败语义。现在已经有 DAG 和任务级持久 checkpoint/审批恢复；若未来需要跨进程长图、分布式调度和大量动态节点，再按恢复语义、可观测性和迁移成本评估 LangGraph。

### Q5：项目最重要的模块是哪个？

是“合同边界”而非单个模型调用。TaskGraph 拥有执行意图与依赖，RunStore 拥有暂停/恢复，ToolManager 拥有授权和副作用，CoverageGate/Verifier 拥有发布资格，RolloutManager 拥有版本指针。没有这些 Owner，多 Agent 只是多调几次模型。

### Q6：多轮中途改意图会丢历史吗？

不会。工作记忆按认证 Principal 的 `subject + conv_id` 保留，意图每轮重新识别。每个完整发布轮次写入 Redis 后立即以稳定 ID 幂等进入情景索引；老信息超 Token 预算后压缩。`EXECUTE` 轮次还会原子更新会话级 L1 事实任务，默认累计 3 轮或空闲 5 分钟后提取；短会话 finalize 补齐摘要/checkpoint、索引 metadata，并立即尝试 flush 事实。请求体 `user_id` 不再拥有身份，冲突会 403。

## 意图识别与路由

### Q7：意图识别方案是什么？

**CURRENT。** 从闭合 `IntentCategory` 枚举选择，不让 LLM 自由创类别。`INTENT_SIMILARITY_MODE=ngram` 时是 LLM 0.70 + 本地字符 n-gram 0.20 + 规则 0.10；`disabled` 时是 LLM 0.85 + 规则 0.15。模式与 provider base URL 解耦，融合低于 0.5 归 `OTHER`。

### Q8：为什么不能把 n-gram 叫 Embedding 模型？

它是可运行的轻量词面相似度基线，不是语义 embedding 服务。应准确说“本地字符 n-gram 向量相似度”，否则模型名称、维度、训练数据一追问就会暴露。

### Q9：意图和 Agent 路由是同一件事吗？

不是。意图描述用户主要要做什么；路由将意图、关键词和实体转成领域 score 和 TaskGraph。因此一个主意图仍可产生 Technical + Billing 两个独立子任务，并显式表达其依赖和上下文范围。

### Q10：关键词会覆盖 LLM 意图吗？

不是直接覆盖，两者累加到领域 score。但旧面经的担心成立：“我不是说扣钱问题”仍可命中“扣钱”。当前启发式未完整处理否定和引用；改进应让解析层输出肯定/否定 domain evidence，路由器只消费该结构。

### Q11：低置信度时是否启动所有 Agent？

不应该。模糊不等于多领域，无差别 fan-out 只是让多个模型一起猜。没有明确证据时追问，有多个可分离领域证据时才并行。

### Q11.1：用户没有恶意，只是问了无关客服的问题，哪个 Agent 负责？

没有“拒答 Agent”。`PromptInjectionGuard` 只判断恶意输入，`IntentRecognizer` 提供 `OTHER + confidence`，最终处置 Owner 是 Orchestrator。低置信度 `OTHER` 返回 `CLARIFY`，高置信度 `OTHER` 返回 `OUT_OF_SCOPE`；两者都没有 TaskGraph，也不启动 GeneralAgent、RAG、工具、Verifier 或工单。固定范围回复由代码策略发布，越域内容不进入客服长期记忆。明确问候仍由 GeneralAgent 简短回应。

追问“为什么不让 GeneralAgent 自己拒答”时，可以回答：Prompt 是软约束，而且 Verifier 的相关性判断只说明答案是否回应用户问题，不能证明它符合产品范围。把范围处置做成 Planner 的类型化终态，才能确定性证明零 Worker、零工具和零记忆写入。

### Q12：怎样测意图识别？

当前有 Accuracy/Macro-F1、版本化 intent layer 和公开数据 adapter。180 条外部意图是 `auto_mapped`，320 条项目合同是 `provisional`；正式结果必须来自 human-reviewed、新鲜 group-safe heldout，并报告 confusion matrix、每类 precision/recall/F1、置信度校准、拒识质量与复合/否定难例 slice。

## TaskGraph 与 Multi-Agent 编排

### Q13：是“主 Agent plan，子 Agent 执行”吗？

**CHANGED。** 外层是 planner/worker 分工，但 Planner 是确定性 Python 规则，不是 LLM 自主规划。它生成 TaskGraph；同一拓扑波次的领域 Worker 可并发，依赖失败的后继得到类型化阻塞，Worker 内部才用 ReAct 选工具。

### Q14：TaskGraph 比 Agent 列表多了什么？

每项有 `task_id`、唯一 Owner、`depends_on`、`context_refs`、evidence spans、副作用上界、required、risk 和 success criteria。图在执行前拒绝环和悬空边；下游按 task_id 对齐 outcome，可检测 missing、duplicate、unexpected、failure 和 blocked dependency。

### Q15：复合请求怎样触发多 Agent？

领域 score 最高者为 primary，其他非 General 领域达 0.45 成为 supporting。“登录 401 还重复扣款”包含两份可分离证据，所以生成 Technical 与 Billing 两项 task。阈值是工程初值，尚无消融证明最优。

### Q16：为什么不所有请求都多 Agent？

多 Agent 增加模型调用、尾延迟、上下文割裂、冲突和归因成本。只在子问题可分离、Owner 不同且可并行时 fan-out，单领域只跑一个 Worker。

### Q17：为什么用 asyncio 而不是线程池跑子 Agent？

Agent 主要等模型和存储 I/O，`asyncio` 是主并发模型；同步 tool handler 才放线程池。线程池不会自动解决超时、共享 deadline、部分成功和取消语义。

### Q18：一个 Agent 失败怎么办？

每项收敛为 `SUCCESS / TIMEOUT / ERROR / BUDGET_EXCEEDED / BLOCKED_DEPENDENCY / AWAITING_APPROVAL`，不让一个异常抹掉其他成功结果。CoverageGate 标出 required task 未闭合；Synthesizer 保留可用证据，Verifier 不把部分结果冒充完整发布。

### Q19：当前执行预算是多少？

默认共享请求 deadline 20 秒，单 Agent 15 秒，每请求最多 3 个 Agent。超出 max-agents 的任务产生 `BUDGET_EXCEEDED`，不静默丢弃。当前还没有 token、模型次数或金额预算。

### Q20：为什么不直接 LLM Planner？

领域少、风险明确时，代码 Planner 更便宜、可复现、易测。长尾数据证明规则无法覆盖时，可让 LLM 输出同一 TaskGraph schema，但依赖校验、风险、预算和 CoverageGate 仍是确定性边界。

## 领域 Agent 与 Skills

### Q21：不同 Agent 真正区别在哪？

它们有不同任务范围、风险、成功标准、Prompt/Skill、工具 allowlist 和升级条件。Technical 追求可执行排障；Billing 不能声称已执行未发生的财务操作；AccountSecurity 优先保护账户并要求验证/审批。

### Q22：General 怎么区分物流、订单和普通咨询？

前置意图和知识 RAG 已提供细粒度意图、实体和相关规则。General 处理低风险共性流程，Skill 约束行为而不当主分类器。某领域复杂度、工具权限或量级上升时再拆独立 Owner。

### Q23：Skill 是 SOP 还是知识库？

Skill 是处理策略、SOP 和安全边界，解决“怎么做”；知识库解决“业务事实是什么”。退款时效等会变事实不应写死在 Skill，否则出现两个事实 Owner。

### Q24：一个子 Agent 有多少 Skill？

当前仓库共 4 个 `SKILL.md`：general_customer_service、technical_support、billing_support、account_security，主要按 Agent 类型一对一加载。不编造“每 Agent 几十个 Skill”；重点是解决的行为约束。

### Q25：账单 Agent 的规则逻辑是什么？

先确认订单、渠道、时间和金额；再根据检索政策说明条件与下一步；真实退款、补偿、调账或敏感数据要升级。它不能承诺已到账，也不能把模型判断当财务审批。当前是 Skill/Prompt 约束，不是独立财务规则引擎。

### Q26：Skill 调试最容易出什么问题？

过度注入增噪声，规则冲突丢优先级，纯关键词漏口语，业务事实写入 Skill 会过期。要记录加载了什么 Skill，做有/无 Skill 消融，同时看规则遵循率、Token 和延迟。

## 知识 RAG 与长期记忆

### Q27：知识 RAG 链路是什么？

对需要业务事实的意图，ToolManager 产生原 query 加改写 query，并行调用 KnowledgeBase。KnowledgeBase 支持 vector、BM25 和 RRF，但当前 80 条 provisional dev 消融选择 BM25-only 默认值；多 query 结果合并后由 chat LLM rerank 取 Top-K。rewrite 失败退原 query，rerank 失败退检索排序。

### Q28：知识 chunk size 和 overlap 是多少？

**CURRENT。** 默认估算 Token 上限 360、overlap 48，优先段落/句末/空白边界；超长单句强制二分硬切。`chunk_id` 贯穿候选排序与证据投影，最终才按父 `document_id` 去重。切片参数仍是工程基线；检索权重则已有 dev 消融：BM25-only Recall@5 0.95 / MRR 0.8575，优于旧 RRF 的 0.9125 / 0.7479。

### Q29：怎样实验 chunk 参数？

固定文档、query、embedding、Top-K 和 reranker，比较 256/360/512 Token 与 0/32/48/64 overlap。检索层看 Recall@K、MRR/nDCG、边界证据完整率与 chunk 投影一致性；端到端看 grounded answer、拒答率、延迟、索引体积和 Token 成本。

### Q30：为什么 LLM rerank 不用 cross-encoder？

当前 LLM rerank 是依赖少、可重用 provider 的工程取舍，不是证明它更好。cross-encoder 往往延迟更可控、结果更稳定；应在同一 judged query-document 集比 nDCG/MRR、P95、成本和错误 slice。

### Q31：query rewrite 的价值和风险？

它将一种用户表达扩展成多种检索表达，提高候选覆盖；代价是 LLM 延迟和 query drift。要做原 query / rewrite only / rerank only / rewrite+rerank 四组消融。

### Q32：长期记忆与知识库有什么区别？

知识库是公共业务事实；长期记忆是某个用户的历史情景，必须按 user 隔离。两者不共享排名管线，也不能互相充当权威事实。

### Q33：混合长期记忆怎么做？

每个完整发布轮次立即以稳定消息 ID upsert 原始片段；检索只在签名 `user_id` 内并排除当前 `conv_id`，同时召回 Chroma vector Top-20 和用户内最多 200 条 BM25 语料，再以 vector 0.30、BM25 0.60、recency 0.10 的 RRF 排序。命中保留 `message_id/event_seq/role/chunk_index`，最相关的两个旧会话从 Redis 原始事件各展开前后两条消息，总窗口上限 1200 estimated tokens；展开失败才退回孤立命中原文。

**NEW。** 原始历史按 1200 字符、120 overlap 写入 episodic collection。在用户边界内分别取 vector 和 BM25 候选，只对候选并集做 recency，最后用 RRF `k=60`、vector/BM25/recency 权重 `0.30/0.60/0.10` 融合。

### Q34：为什么 BM25 权重更高？

订单号、错误码和金额精确 token 对词法召回更敏感。`0.60/0.30/0.10` 是工程初值，不是最优值；要用 held-out query/relevant-id 集调参，同时看精确实体 slice 和延迟。

### Q35：为什么 recency 不能单独召回？

“最新”不等于“相关”。recency 只能重排 vector/BM25 已召回的候选，不能把无关的新记忆拉进候选池。

### Q36：压缩记忆的当前参数？

6000 Token 预算，估算达 70% 触发，摘要视图上限 1200 Token。普通压缩保护最近 2～5 条原文，其余从最旧未覆盖事件开始形成有界 seq 范围摘要；Prompt 仍由 `ContextAssembler` 按总预算裁剪。提交只 WATCH checkpoint，新消息拥有更大 seq，不会让已固定范围失效。

### Q37：记忆错了怎么办？

组件不可用时降级无记忆；内容不准时，实时订单/退款/账户工具结果最高，TicketService 未关闭事项状态其次，当前用户输入再用于纠正背景，历史原文、画像和摘要不能覆盖这些 Owner。已有用户隔离、当前会话排除、Top-K、邻居窗口、原始片段和 rank 证据；未有记忆编辑/删除 UI、事实有效期和持久反馈学习。

## ReAct、工具权限与 Trace

### Q38：ReAct 在哪一层？

**NEW。** ReAct 只在已领取 TaskSpec 的 Worker 内，用于选择和观察工具，最多 4 步。它不拥有总计划，不能改写 TaskGraph。外层确定性、内层有界推理是核心分层。

### Q39：怎么防止越权工具？

工具发现只暴露 Agent allowlist，执行时 ToolManager 用同一 allowlist 再校验。模型编造工具名会得到 `denied`。授权不是 Prompt 约定，而是执行边界的权威决策。

当前生产工具共 9 个：公共知识 `knowledge_search`、用户记忆 `memory_search`、工单列表/详情/创建，以及订单查询、退款资格检查、退款申请创建和账户安全事件查询。旧评测里的 `refund_write` 仍是 fixture；真正的 `refund_request_create` 由 Billing Agent 调用，宿主审批后在 SQLite 业务 Owner 内重验订单版本与资格并返回 receipt。它表示“申请已提交”，不表示支付渠道“资金已到账”。

### Q40：模型能伪造 `approved=true` 吗？

不能。`approved` 不在暴露给模型的 input schema；写调用先持久化为 `WAITING_APPROVAL`。持有 `tool:approve` scope 的宿主通过 Resume endpoint 批准，RunStore 重新校验用户、task、Bundle、工具和参数绑定，再用 CAS 与唯一 call ID 防止并发重复提交。

### Q40.1：用户直接写“忽略系统指令”怎么办？

`PromptInjectionGuard` 在消息进入 Memory、RAG、Intent LLM 和 Worker 前做 NFKC/不可见字符规范化，检测系统规则覆盖、提示词窃取、角色伪造、授权伪造和编码逃逸。高置信命中直接返回稳定 400，并只记录类别、风险分、哈希和 TraceId，不把攻击正文写进记忆或日志；普通客服继续通过，部分明确教学语境只记录不阻断。若规则漏检，所有 Worker 的共同 system policy 仍把用户、历史、检索和工具输出声明为不可信，身份/审批只认服务端上下文和 receipt。

**追问：正则能彻底防住吗？** 不能。它只负责拦截已知高置信直接注入。真正的损害控制仍靠可信身份、最小工具权限、写审批、业务 Owner 重验、receipt 与发布 Verifier；后续要用版本化攻击集评误报/漏报，并可接独立 learned detector，而不是把规则命中率叫安全率。

### Q41：工具并发如何处理？

同一 ReAct 步中只读工具可并行，潜在写工具串行，避免顺序未定义的副作用。这不是分布式事务；当前工单创建和本地退款申请已有业务幂等 key 与 typed receipt，退款还使用 `order_version` 防止资格检查后的 TOCTOU。支付渠道退款、调账仍需外部授权与补偿合同。

### Q42：怎么防 ReAct 死循环？

默认最多 4 个 model/tool step，`tool_use` 与 `tool_result` 按 call_id 配对。超限收敛为 `MAX_STEPS`，不让 General 文本覆盖该失败。

### Q43：Trace 记录了什么？

`TraceId` 通过 `contextvars` 从 HTTP 传到 asyncio Worker、ReAct step 和 Tool span；工具记录 call_id、Agent、状态、延迟、参数哈希/形状和截断结果元数据。不保存完整敏感参数/输出，避免把可观测系统变成泄漏面。

### Q44：这是完整 MCP 吗？

**UNPROVEN。** `MCPToolManager` 是内部工具运行时的历史命名，有注册、schema、超时、缓存、熔断、fallback、权限和 audit，但没实现远程 MCP transport/protocol server。

## 融合、评测与生产边界

### Q45：多 Agent 结果怎样融合？

先按 TaskGraph 检查覆盖，再对成功候选按主辅去重和冲突检测。Synthesizer 还单独表达 `awaiting_approval`；文本仍只是 candidate，只有 Verifier `PASS` 才有发布资格，审批等待也不能被普通 fallback 抹掉。

### Q46：当前评测链路是什么？

有两条链。运行时链对 intent 算 Accuracy/Macro-F1，对 dialog 真实调 Orchestrator 并用 LLM Judge；确定性链对版本化 intent/routing/retrieval/stateful prediction 分别算分类、Owner/task、Recall@K/MRR/nDCG 和 assertion 指标。报告带 dataset version/checksum/split/review scope。

### Q47：Judge 分高就代表路由正确吗？

不代表，错 Agent 也可能碰巧生成流畅答案。要同时记 expected/actual Owner、TaskGraph、依赖波次、fan-out、coverage、budget、工具授权和发布状态，分开评“结果好”与“过程合同正确”。

### Q48：91.3%、0.89 等旧数字怎么回答？

**UNPROVEN。** 旧数字没对应数据版本、切分、运行产物和 commit，已移除。当前可证明的是 265 项回归测试、500 条分层候选集、25 篇 corpus、Stateful fixture 和隔离 RAG producer；因为 gold 仍为 0，不能报项目准确率。独立审核并运行新鲜 heldout 后才报均值、方差、slice 和置信区间。

### Q49：多 LLM 调用怎么降延迟？

先用 trace 分解 intent、rewrite、retrieval、rerank、Worker、Verifier 的 P50/P95/P99。已有按需 RAG、多 query 并行、有条件 fan-out、超时/缓存/熔断与共享 deadline。当前 Trace 尚未覆盖全链 span，不宣称已解决生产 P99。

### Q50：最大生产缺口是什么？

此前最大缺口是可信身份，不是 Prompt；现在 JWT `sub` 是用户身份，chat/knowledge/admin scope 控制接口，CORS 默认显式来源。剩余生产缺口是外部 IdP/JWKS、密钥轮换、tenant claim、resource/action ABAC、撤销与持久审计。

### Q51：Trace/审批还缺什么？

Trace 缺 OpenTelemetry exporter、持久存储、全链 span、采样与保留策略。审批已有 pending call、批准 scope、过期时间、checkpoint 和 task-level resume；仍缺完整多 Agent 图跨服务恢复、外部支付授权、补偿与渠道 receipt。当前是闭合任务级恢复，不是通用人工工作流平台。

### Q52：100 万条知识怎么检索？

不能将小型单 collection 结论直接外推。生产先用 tenant/ACL/业务类别 metadata pre-filter，再 ANN + 词法候选，用便宜 reranker 缩小后精排。还要测 shard/索引构建、更新一致性和 P99；当前仓库没有百万规模压测。

## STAR 表达与简历钩子

### Q53：如何用 STAR 讲 Multi-Agent 改造？

**S：** 旧链路只有 Agent 列表并拼文本，复合请求的子任务可静默丢失，依赖任务也可能误并发。**T：** 保留独立任务并行，让依赖、上下文、失败和超时可证明。**A：** 引入 TaskGraph/TaskSpec、唯一 Owner、DAG 拓扑波次、context refs、六态 outcome、ExecutionBudget、CoverageGate 与 typed synthesis。**R：** API 返回执行波次、覆盖缺口和预算证据；测试覆盖环/悬空边、依赖阻塞、上下文隔离、部分失败与 fan-out，不编线上业务指标。

### Q54：如何用 STAR 讲混合记忆？

**S：** 旧版检索压缩摘要，订单号/错误码可消失。**T：** 保留精确事实并支持语义改写，让排名可解释。**A：** 以原始重叠片段为事实载体，用户内融合 BM25/vector/recency ranks，两路独立降级，输出 source ranks 和 Recall@K/MRR/nDCG。**R：** 精确 token 用例不再只依赖向量，相关不变式有测试；权重仍需真实数据调优。

### Q55：如何用 STAR 讲 ReAct 权限与 Trace？

**S：** 模型能调工具后可能编造名称、越权写、使用过期资格、死循环且难归因。**T：** 保留外层确定性计划，让 Worker 安全使用工具并在人工批准后可靠继续。**A：** 实现 4 步 ReAct、双重 allowlist、读并行写串行、RunStore checkpoint/CAS Resume、call ID 幂等账本与 TraceId；退款由业务 Owner 事务内重验 `order_version`、窗口和重复申请。**R：** 9 个生产工具覆盖知识、记忆、工单、订单、退款和安全事件；测试证明跨用户拒绝、Shadow/未审批零副作用、并发 Resume 不重复提交、退款幂等 receipt 和 Trace 传播；外部支付仍未实现。

### Q56：简历可以怎么写？

1. 利用 `TaskGraph + CoverageGate + ExecutionBudget` 解决复合客诉中任务静默丢失、依赖误并发与 timeout 叠加，按 task_id 输出成功、阻塞、审批等待、超时、异常与预算耗尽证据。
2. 利用 BM25、Chroma 向量检索与 weighted RRF 解决压缩摘要丢失精确实体的召回失真，建立 Recall@K/MRR/nDCG 回归接口。
3. 利用有界 ReAct、Agent 白名单、持久 Checkpoint 与 CAS Resume 解决工具越权、死循环、审批后无法继续和重复写，将每次调用绑定到可追溯 receipt。
4. 利用单调 seq、不可变范围摘要和 checkpoint CAS 解决长对话溢出与并发写覆盖，保留可重建原文和来源证据。
5. 利用 fail-closed AnswerVerifier 与幂等 TicketService 解决高风险候选误发布和重复升级，确保只有 PASS 回复进入记忆。

## 公司轮次模拟与新追问

### Q57：“500 条数据从哪来”怎么答？

不沿用旧说法。当前正好有 500 条候选，但不是 500 条 gold：180 条 auto-mapped 外部压力样本、320 条 provisional 项目合同。下一步从脱敏工单分层抽样，双人标注+仲裁，按用户/时间/group_id 去重切分，保留无效输入与复合难例；不能把自动映射数量冒充人工项目标注。

### Q58：“91.3% 是高还是低”怎么答？

没有数据分布、错误成本和 baseline，单一准确率无法判高低。账户安全/财务的 false negative 可比问候误分类贵得多。应报类别 slice、置信校准、拒识率和业务成本，不为旧数字辩护。

### Q59：如果下一步只改一件事？

要上生产，在已有 JWT/scope 基线上先接企业 IdP/JWKS、tenant/resource ABAC 与密钥轮换；要证明算法价值，先完成人工 Gold 和新鲜 heldout；要扩长流程，把当前 task-level RunStore 迁为跨服务图调度。不是默认再加 Agent。

### Q60：怎样回答“项目最难的点”？

不答“模块协同很难”。选一条可验证故事：旧并行链路只有 Agent 文本列表，异常破坏整体且无法证明子问题完成；于是引入 task_id/Owner、DAG、六态 outcome、共享预算和 CoverageGate；最后用依赖阻塞、部分成功、审批等待、超时、重复/计划外 outcome 测试验证。

## 新增边界追问：这次代码收敛了什么

### Q61：短会话没触发压缩，怎样保证长期记忆？

现在不再把“能否跨会话检索”绑定到压缩。`add_messages()` 写完一个完整发布轮次后立即按稳定 message ID 幂等 upsert 原始片段，因此两条消息的短会话也能被下一会话检索。`POST /conversations/{conv_id}/finalize` 仍固定调用开始时的 high-water，补齐未覆盖范围的摘要/checkpoint 与索引 metadata，并强制处理同范围内已调度的 L1 事实任务；原始事件不删除。若完成后发现更大 seq，则返回 409，下一次只处理新增范围。

### Q62：为什么用户画像现在能确定性读取？

不再把整个画像合并进一条可覆盖 JSON，也不在每个请求后创建可能丢失的裸后台 task。`EXECUTE` 轮次把 L0 原文和唯一 Redis sorted-set任务原子提交；累计 3 轮或空闲 300 秒后，Worker持用户级租约按最多 20 条事件的固定范围提取，同一用户跨会话的事实状态转换也会串行。模型只能对支持的 fact key 产出 `upsert/retract`，每条事实携带来源 message ID、conversation ID、原始事件时间、局部 seq、置信度和生命周期；同会话按 seq、跨会话按 source time判断新旧。成功才推进 fact checkpoint，失败留队退避，进程重启可恢复。Prompt profile只是 active facts投影，不是 L3 Persona。

### Q63：Verifier 已替换顶层 response，为什么还要删 outcome content？

因为拒绝候选仍可能从嵌套诊断字段泄漏。内部 verifier/feedback 需要完整 outcome，但公开 API 只投影状态、延迟与类型化原因，删除 candidate、raw error、agent key 和 tool call IDs。管理员排障应走独立受控接口。

### Q64：请求体传别人的 user_id 还能查记忆吗？

不能。HTTP 先验证 JWT，所有记忆操作使用签名 `sub`；请求体 user_id 只是旧客户端一致性检查，冲突直接 403。chat、knowledge 和 admin scope 分开。要诚实说明：这还是本地 HS256 基线，不是完整企业 IAM。

### Q65：Chroma 远程挂掉为什么取消本地 fallback？

远程和 embedded 是不会自动合并的两套物理存储，静默切换会产生 split-brain。现在 `CHROMA_MODE=remote` 不可达就启动失败；开发要本地库必须显式 `embedded`，`/health` 报告实际 mode/location。

### Q66：在线降权以前为什么可能是“看起来有闭环”？

每种类型只有一个 `_0` 实例，分数再低也没有 `_1` 可选。现在 stats 输出 pool size 与 adaptive flag；singleton 只告警不施加选择 penalty。测试另建同类双实例，证明质量反馈确实能改选。

### Q67：Escalation 为什么要是真实 Agent？

TaskGraph 已把人工交接指定给 `AgentType.ESCALATION`，却由 General 执行会让 Owner 和 responder 不一致。现在 tool-free EscalationAgent 专门整理诉求、风险、证据和待核实项；真正工单事实仍由 API/TicketService 创建，不让 LLM 声称已建单。

### Q68：这一轮简历怎么写成一条？

> 利用 JWT Principal/scope、完整轮次即时幂等归档与显式存储模式收敛 Agent 服务生产边界，解决用户身份伪造、短会话索引滞后、拒绝候选旁路泄漏和 Chroma 双库分叉；补齐 tool-free Escalation Owner 与路由基数诊断，以对应不变量测试验证失败可重试、数据不丢失和公开投影最小化。

这句信息密度高，面试时优先拆成“身份/发布”或“记忆生命周期”一条 STAR，不要一次全背。

## 新增评测数据追问：从“有数据”到“能报数”

### Q69：现在仓库到底有哪些评测数据？

三类必须分开：11 条 intent + 5 组 dialog 是内置 smoke；`dialogpilot-500-v1` 是 500 条四层候选集 + 25 篇 retrieval corpus；其中 BANKING77、CLINC150 OOS 部分是 auto-mapped external pressure，项目合同仍是 provisional。当前 human-reviewed gold 是 0。

### Q70：为什么公开数据不能直接算项目准确率？

它们的标签和业务边界不是 DialogPilot 的 TaskGraph、知识库、记忆与工具规则。公开集能测试迁移、金融细粒度和 OOS 拒识，不能证明复合路由、依赖阻塞、证据 ID、用户隔离或零副作用授权。

### Q71：provisional 怎样变成 gold？

人工核对输入是否无歧义、expected 是否唯一、source/license 和 split 是否正确；记录 reviewer/reviewed_at/notes，再把状态改为 `human_reviewed`。推荐双人独立标注、冲突仲裁。运行器无权自动提升审核身份。

### Q72：怎样防 train/test 污染？

相同语义改写共用 group_id，校验器禁止一组跨 dev/heldout。生产数据还应按 user/ticket/time/document 去重。dev 用于阈值和 Prompt 迭代，heldout 标签只在发布评测读取。

### Q73：为什么缺一条 prediction 要整次失败？

若 scorer 自动忽略缺失项，失败样本会从分母消失，结果被虚高。当前要求 selected case 一一对应 actual；缺失和重复 case_id 都是 typed error。

### Q74：RAG 三个指标分别证明什么？

Recall@K 证明相关证据进入候选，MRR 关注第一条 relevant 的位置，nDCG 衡量多个 relevant 的整体排序。三者与 grounded answer 分开，才能判断问题出在召回、排序还是生成。

### Q75：怎么跑一次不造假的实验？

先固定 commit、dataset checksum、split、模型/Prompt/Skill/index 版本；一次只改变一个因素，例如 vector-only vs hybrid RRF；保存逐 case outcome、关键 slice、延迟/成本和多次运行方差。`include_non_gold` 只能验证管线，不进简历数字。

### Q76：这部分简历怎么写？

> 利用版本化 JSONL、group-safe dev/heldout、checksum/provenance/review 门禁与确定性 grader，解决 smoke case 无法支撑路由、RAG、记忆和工具安全回归的问题；构建 500 条四层候选集并接入隔离 RAG producer、Stateful Owner fixture，按 Accuracy/Macro-F1、Owner Exact、Recall@K/MRR/nDCG 和 assertion pass 分层归因；当前候选集待独立复核，不虚构生产准确率。

### Q76.1：Reviewer B 为什么能推翻 Stateful 100/100，后来怎么修？

旧 runner 把完整 `EvalCase` 传给 fixture，actual 生产者理论上能复制 `expected`，所以 100/100 只是接口约定，不是强制隔离。我把 fixture 输入收缩为递归冻结且没有 `expected` 的 `FixtureRequest`，故意复制期望值会在评分前失败；同时把 timeout/cancel 与业务副作用拆成两个状态轴，未知事务结果明确写成 `outcome_unknown`。原 80/20 与 Reviewer B 27 条现在都只作为已消费回归，真正的泛化结论仍等新 reviewer 封存新集。

### Q76.2：线上 Bad Case 怎么闭环，不会越积越多吗？

Verifier `REJECT/UNKNOWN`、Coverage 缺口和工具 `outcome_unknown` 自动进入 `BadCaseRegistry`，用户点踩走 `/feedback`。Registry 以 stage + symptom + 规范化输入生成 fingerprint，重复问题只增加 occurrence；状态按 `candidate → triaged → reproduced → fixing → regression_pass → verified → closed` 迁移，关闭后复发会自动重开。只有补齐根因 Owner、四层 expected、fixture/assertions/evidence hash 和修复 commit 才能进入回归阶段。

**追问：线上 Case 能当 heldout 吗？** 一旦看过并用于修复，它就是 consumed regression。导出脚本强制写成 `dev + provisional + consumed_regression`，不能生成 Gold 或 heldout；真正泛化仍依赖未来流量或独立封存的新语义组。

**STAR：** 利用 Verifier、Coverage、工具审计和认证用户反馈建立持久 Bad Case 状态机，解决进程内 Trace 重启丢失、失败无法归因和修复样本污染 heldout 的问题；通过脱敏去重、Owner 复现证据、修复 commit、复发重开和版本化 dev regression，把线上异常转成可追溯回归资产，同时保持 Gold 审核边界独立。

## DeepSeek 分层调用与模型选型

### Q77：现在具体用了哪些模型？

默认矩阵不是“全局一个 DeepSeek”。Intent、Worker、ReAct、Memory、rewrite、rerank 使用 `deepseek-v4-flash + reasoning none`；跨 Agent synthesis、AnswerVerifier 和文本质量 Judge 使用 `deepseek-v4-pro + reasoning none`。外层 TaskGraph 是确定性 Python，不调用 Planner LLM；routing 层也不调用 Answer Judge。

### Q78：为什么 Flash 足够？

不能直接说“足够”。它是面向高频闭合任务的成本/延迟基线：Intent 用 Macro-F1 与关键类 Recall 证明，Worker 用 required-task coverage/完成率证明，检索环节用 Recall@K/MRR/nDCG 证明。若 heldout 不达门槛，再逐角色升 Flash/low 或 Pro/high，不能全局拍脑袋升级。

### Q79：为什么 synthesis、Verifier、Judge 最终是 Pro/none？

Pro 保留跨域融合和开放文本判断能力，但 high 没在小样本中证明质量收益：Verifier 三档 3 条 pilot 都是 3/3，平均延迟分别约 0.90s、1.71s、5.73s；high 还曾在短预算下只返回 thinking。默认因此选 Pro/none，Coverage、权限、副作用和 routing truth 由确定性代码拥有。只有 gold heldout 证明 high 显著降低错误时才按角色升级。

### Q80：DeepSeek 默认 thinking 有什么坑？

如果只改 base URL 而不显式控制，简单 Intent/摘要调用也可能继承高 thinking，延迟和成本口径失真。`ModelProfile` 对 none 显式发送 disabled，对 low/high/max 发送 enabled + effort，并从 thinking 请求移除无效 temperature。

### Q81：thinking + tool call 为什么特别处理？

DeepSeek 的 Anthropic 兼容协议要求工具后续轮回传此前 thinking 内容。旧解析器只保存 text/tool_use，会导致第二轮协议错误。现在按响应顺序保留 `thinking → tool_use`，再追加配对的 `tool_result`，并有两轮 ReAct 测试证明。

### Q82：为什么不让 Router 每次动态选模型？

静态按角色分层更可复现，也少一次 Router 调用和错误反馈环。动态升级只有在真实复杂度 slice 证明收益后再加，而且必须记录选择理由、实际 profile、预算和 fallback，不能成为不可观测黑盒。

### Q83：怎么防报告里不知道跑的哪个模型？

`/health` 返回无密钥的实际 role matrix，`/eval/run` 把同一矩阵写入 report metadata。启动时未知 provider、DeepSeek 模型名或 reasoning 值直接失败，不静默回退。

### Q84：这项改造怎样写 STAR？

**S：** 九类调用共用一个模型，DeepSeek 默认 reasoning 让简单任务成本、延迟和结构化输出不可控。**T：** 在保持统一 Messages API 的同时，让每类调用可独立权衡质量。**A：** 实现按角色校验的 ModelPolicy，Flash/none 承担闭合高频任务，Pro/none 承担融合与质量门禁；显式 reasoning 强制最小完成预算，并补齐 health/eval 配置证据和 ReAct thinking 回传。**R：** 4 条 E2E pilot 均值约 28.4s → 13.3s，Verifier 解析 2/4 → 4/4；当前全仓 265 项回归测试通过。15 条 provisional 三档消融已完成，最终选择仍需 gold heldout 与重复运行确认。

### Q85：Flash/off、Flash/high、Pro/high 真跑后有什么区别？

同一 15 条 provisional seed、同一 15s/20s 预算下：Flash/off 的 Intent 与 Task success 都是 8/8；Flash/high 都是 7/8，case P95 +25.1%、峰时成本至少 +41.7%；Pro/high Intent 7/8、Task success 3/8，峰时成本下界约为 Flash/off 的 2.9 倍。Owner Exact 三档都是 7/7，只说明 Planner 分工正确，不说明 Worker 做完。结论是当前数据不支持全局开 high 或全局升 Pro；完整口径见[模型消融报告](./model-ablation-report.zh-CN.md)。

## Agent 进化、晋级与灰度追问

### Q86：Bad Case 现在会自动让 Agent 变好吗？

会自动形成**候选**，不会自动改生产。线上失败附加 `EvolutionEnvelope` 后按语义组聚类，确定性归因到 Prompt、路由、检索、工具描述或代码/安全 Owner。只有可进化配置面才让反思模型生成 4–8 个 Bundle；通过真实 Gate、Graduation、Pareto 和灰度之后才可能成为 Active。

### Q87：为什么不能一次差评就在线改 Prompt？

一次差评可能是噪声，也可能是 RAG、路由、权限或业务服务错误。在线改会让在途请求版本漂移，旧回归不可复现，失败也没有稳定回滚点。运行与学习分离后，生产只读取不可变版本，学习面只产候选。

### Q88：EvolutionEnvelope 解决什么问题？

它固定失败请求实际使用的 Bundle 和 Prompt/路由/检索/工具注册哈希，以及真实 producer、task、tool-call ID。这样能做 Credit Assignment 和版本复现；同时不保存原始 Prompt、回答和工具参数，避免学习资产扩大敏感数据面。

### Q89：哪些内容允许 Agent 自动进化？

闭合范围是 Prompt 片段、Few-shot、supporting/clarification 阈值、RAG top_k/RRF/混合权重、工具描述和经运行时兼容校验的模型策略。权限、allowlist、审批、JWT、PII、Verifier fail-closed、Gold 状态和 Python 代码都没有自动写权。

### Q90：为什么 AgentBundle 必须不可变？

一次请求、一次评测和一次 Resume 必须能指向相同配置。注册表只追加，同版本不同内容冲突；请求开始固定 Bundle 对象，缓存键、checkpoint、报告和 Bad Case 都绑定版本/哈希。Active 变化只影响新请求。

### Q91：怎么证明硬门禁不是调用者伪造的？

`CandidateRunner` 不接受裸 `hard_gates=true`。每个注册 Gate Runner 必须实际执行并返回名称一致、带 evidence ID 和 details SHA-256 的 `GateArtifact`；报告还绑定 candidate bundle version，阻止复制基线结果再改标签。

### Q92：为什么安全失败不能被高质量总分抵消？

越权、跨用户、错误写入属于零容忍不变量，不是偏好。Graduation 先检查 security、identity isolation、tool authorization、coverage、stateful；只有全部通过的候选才进入质量/延迟/成本 Pareto。

### Q93：Shadow 为什么不会污染生产？

Shadow 用真实输入跑候选 Intent/RAG/Worker/Verifier，但不发布、不写 Memory、不建 Ticket、不登记 Bad Case。更重要的是 `execution_mode=shadow` 在 ToolManager 执行边界拒绝写 Tool，只读调用也不更新生产 cache、breaker 和 Agent stats；不是单靠 Prompt 约束。

### Q94：Canary 怎么分流，为什么不是随机？

用 secret salt + 认证用户 subject 做 SHA-256 稳定分桶。同一用户在阶段不变时总落到同一版本，避免多轮会话随机跨版本。状态机只允许 Shadow → 5% → 25% → Active，不能跳过 25% 直接全量。

### Q95：自动回滚看什么？

越权、隐私泄漏、跨用户召回、错误写操作是一条即回滚的硬信号。Verifier pass rate、P95 latency、平均 cost proxy 是软信号；候选和基线达到最小样本后再判断，pass rate 用 Wilson 区间防小样本抖动。当前 cost 是 Agent/tool 数代理，不是美元账单。

### Q96：你实现的是完整 GEPA 吗？

不是。借鉴点是读取失败轨迹、自然语言反思、多候选和多目标 Pareto；仓库只允许对少数已接线配置面做最小 patch，没有复现论文完整搜索算法和跨任务实验，所以准确叫 `GEPA-lite`。

### Q97：为什么没直接用 Agent Lightning 做 RL？

当前 human Gold 为 0、线上真实 reward 和 step-level 轨迹规模不足，直接 RL 容易优化错误代理目标。先用可解释候选 + 硬门禁解决主要 Prompt/路由/检索问题；等奖励、数据和训练预算成熟，再验证 step credit assignment 是否带来增益。

### Q98：这项改造怎么写 STAR？

**S：** Bad Case 能入库，但人工直接改 Prompt 导致版本归因弱、回归不可复现、发布全量且安全边界可能被误改。**T：** 把线上失败变成可验证、可灰度、可撤销的策略升级。**A：** 增加脱敏 EvolutionEnvelope、确定性 Owner 归因、不可变 AgentBundle、GEPA-lite 多候选、provenance Graduation/Pareto，并以 Shadow、稳定 5%/25% 分桶及硬/软自动回滚发布。**R：** 请求内版本固定，候选无法修改权限或绕过 Gate，Shadow 写操作零提交，发布/回滚成为原子状态迁移；265 项回归通过，数据非 Gold 前不虚构线上提升。

## 面试前 10 分钟自查

1. 能画出 `/chat` 从 TraceId 到记忆写回的顺序，不混 Knowledge RAG 和 Memory Retrieval。
2. 能说出 5 个真实 Worker、Bundle 默认 supporting 0.45、20s/15s/3 agents 预算和 4 步 ReAct。
3. 能说出 Knowledge chunk 360 Token/48 overlap 与 Memory chunk 1200 字符/120 overlap，并解释两条链路身份不同。
4. 能说出 TaskGraph 依赖/上下文隔离、六种 outcome、synthesis 与 Verifier PASS/REJECT/UNKNOWN。
5. 能解释 BM25 对订单号的价值，以及 recency 为什么不能独立召回。
6. 能解释发现/执行共用 allowlist，审批不来自模型参数，RunStore 如何 CAS Resume 并防重复写。
7. 能说明回归测试数量不等于 benchmark 样本量，smoke、auto_mapped 与 provisional 都不支持生产准确率。
8. 能用 commit 划清原型与个人改造，不说从零原创。
9. 能讲清 Bundle → 候选 → Graduation → Shadow → 5% → 25% → Active/回滚，并说明 GEPA-lite 与 RL 的边界。
10. 能讲清 JWT/scope 与 task-level Resume 已完成，以及 IdP/JWKS、tenant ABAC、持久 Trace、全图恢复和真实 Gold benchmark 仍是缺口；不说“完整 MCP”“复现 SOTA”或“线上准确率 91.3%”。

> 完整仓库链路、源码定位和更多底层追问，回到 [DialogPilot 完整架构教程](./)。
