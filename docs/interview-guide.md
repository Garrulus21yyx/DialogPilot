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
| 并行回答直接拼接 | `TaskPlan → typed outcomes → CoverageGate → ResultSynthesizer → AnswerVerifier` | **CHANGED** |
| 消息超过 15 条压缩 | 6000 Token 预算、70% 触发；原始事件追加保留，摘要块覆盖固定 seq 范围，CAS 只推进 checkpoint | **CHANGED** |
| 长期记忆只检索摘要 | 检索 1200 字符/120 overlap 原始片段，摘要只是背景 metadata | **CHANGED** |
| 知识库是 BM25 + 向量 + RRF | 这是长期记忆；知识 RAG 仍是 rewrite + Chroma 多路向量 + 去重 + LLM rerank | **CORRECTED** |
| Agent 单次生成、无完整 Trace | Worker 内最多 4 步 ReAct，allowlist/宿主审批/脱敏 audit/TraceId | **CHANGED** |
| 准确率 91.3%、综合分 0.89 | 当前有 500 条分层候选集、25 篇 corpus 和 160 项回归测试，但仍无 human-reviewed gold | **UNPROVEN** |
| 完整 MCP Server / LangGraph | 是内部 ToolManager 与直接 Python 编排；没有远程 MCP Server，没用 LangGraph | **UNPROVEN** |

## 项目开场与完整链路

### Q1：用 60 秒介绍这个项目。

**CURRENT。** DialogPilot 是 Python/FastAPI 多 Agent 客服后端。它读取分层记忆，用 LLM、本地字符 n-gram 相似度和规则融合识别意图，按需检索业务知识，再生成带 Owner、风险和验收条件的 TaskPlan。领域 Worker 在共享预算内执行，内部可用有界 ReAct 调白名单工具。结果经覆盖检查、类型化融合和 fail-closed Verifier 后才发布，否则创建幂等人工工单。

### Q2：你个人改了什么？

说“接手已有客服原型”，不说从零原创。可用 commit 证明的改造包括：DialogPilot 命名与仓库收敛、持久工单状态机、Token-aware 并发安全压缩、typed synthesis、质量反馈路由、TaskPlan/CoverageGate/ExecutionBudget、混合长期记忆、工具权限/Trace 和有界 ReAct，以及测试、CI 和文档。

### Q3：`/chat` 端到端经过哪些节点？

`TraceId → Redis 工作记忆/混合情景记忆/画像 → 意图与实体 → 有条件知识 RAG → TaskPlan → 预算内 Worker 执行 → CoverageGate → ResultSynthesizer → AnswerVerifier → 发布或工单 → 只写入已发布答案。`

### Q4：为什么不用 LangChain / LangGraph？

**CURRENT。** 当前状态少、风险边界明确，直接 Python 更容易看清 TaskPlan、预算、outcome 和失败语义。如果需要 durable graph、checkpoint、人工中断恢复或长 workflow，再按恢复语义、可观测性和迁移成本评估 LangGraph。

### Q5：项目最重要的模块是哪个？

是“合同边界”而非单个模型调用。TaskPlan 拥有执行意图，ToolManager 拥有工具授权和副作用，CoverageGate/Verifier 拥有发布资格。没有这些 Owner，多 Agent 只是多调几次模型。

### Q6：多轮中途改意图会丢历史吗？

不会。工作记忆按认证 Principal 的 `subject + conv_id` 保留，意图每轮重新识别。老信息超 Token 预算后压缩；短会话结束时显式 finalize，原始消息以稳定 ID 幂等归档。请求体 `user_id` 不再拥有身份，冲突会 403。

## 意图识别与路由

### Q7：意图识别方案是什么？

**CURRENT。** 从闭合 `IntentCategory` 枚举选择，不让 LLM 自由创类别。`INTENT_SIMILARITY_MODE=ngram` 时是 LLM 0.70 + 本地字符 n-gram 0.20 + 规则 0.10；`disabled` 时是 LLM 0.85 + 规则 0.15。模式与 provider base URL 解耦，融合低于 0.5 归 `OTHER`。

### Q8：为什么不能把 n-gram 叫 Embedding 模型？

它是可运行的轻量词面相似度基线，不是语义 embedding 服务。应准确说“本地字符 n-gram 向量相似度”，否则模型名称、维度、训练数据一追问就会暴露。

### Q9：意图和 Agent 路由是同一件事吗？

不是。意图描述用户主要要做什么；路由将意图、关键词和实体转成领域 score 和 TaskPlan。因此一个主意图仍可产生 Technical + Billing 两个独立子任务。

### Q10：关键词会覆盖 LLM 意图吗？

不是直接覆盖，两者累加到领域 score。但旧面经的担心成立：“我不是说扣钱问题”仍可命中“扣钱”。当前启发式未完整处理否定和引用；改进应让解析层输出肯定/否定 domain evidence，路由器只消费该结构。

### Q11：低置信度时是否启动所有 Agent？

不应该。模糊不等于多领域，无差别 fan-out 只是让多个模型一起猜。没有明确证据时追问，有多个可分离领域证据时才并行。

### Q12：怎样测意图识别？

当前有 Accuracy/Macro-F1、版本化 intent layer 和公开数据 adapter。180 条外部意图是 `auto_mapped`，320 条项目合同是 `provisional`；正式结果必须来自 human-reviewed、新鲜 group-safe heldout，并报告 confusion matrix、每类 precision/recall/F1、置信度校准、拒识质量与复合/否定难例 slice。

## TaskPlan 与 Multi-Agent 编排

### Q13：是“主 Agent plan，子 Agent 执行”吗？

**CHANGED。** 外层是 planner/worker 分工，但 Planner 是确定性 Python 规则，不是 LLM 自主规划。它生成 TaskPlan；领域 Worker 执行 task，Worker 内部才用 ReAct 选工具。

### Q14：TaskPlan 比 Agent 列表多了什么？

每项有 `task_id`、唯一 Owner、范围、required、risk 和 success criteria。下游按 task_id 对齐 outcome，可检测 missing、duplicate、unexpected 和 failure，不需要从文本猜是否做完。

### Q15：复合请求怎样触发多 Agent？

领域 score 最高者为 primary，其他非 General 领域达 0.45 成为 supporting。“登录 401 还重复扣款”包含两份可分离证据，所以生成 Technical 与 Billing 两项 task。阈值是工程初值，尚无消融证明最优。

### Q16：为什么不所有请求都多 Agent？

多 Agent 增加模型调用、尾延迟、上下文割裂、冲突和归因成本。只在子问题可分离、Owner 不同且可并行时 fan-out，单领域只跑一个 Worker。

### Q17：为什么用 asyncio 而不是线程池跑子 Agent？

Agent 主要等模型和存储 I/O，`asyncio` 是主并发模型；同步 tool handler 才放线程池。线程池不会自动解决超时、共享 deadline、部分成功和取消语义。

### Q18：一个 Agent 失败怎么办？

每项收敛为 `SUCCESS / TIMEOUT / ERROR / BUDGET_EXCEEDED`，不让一个异常抹掉其他成功结果。CoverageGate 标出 required task 缺失；Synthesizer 保留可用候选，Verifier 因 incomplete 拒绝自动发布并转人工。

### Q19：当前执行预算是多少？

默认共享请求 deadline 20 秒，单 Agent 15 秒，每请求最多 3 个 Agent。超出 max-agents 的任务产生 `BUDGET_EXCEEDED`，不静默丢弃。当前还没有 token、模型次数或金额预算。

### Q20：为什么不直接 LLM Planner？

领域少、风险明确时，代码 Planner 更便宜、可复现、易测。长尾数据证明规则无法覆盖时，可让 LLM 输出同一 TaskPlan schema，但风险、预算和 CoverageGate 仍是确定性边界。

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

对需要业务事实的意图，ToolManager 产生原 query 加改写 query，并行调用 KnowledgeBase。每次检索由 Chroma 提供向量候选，同时对隔离 corpus 做 BM25，再用 RRF 融合；多 query 结果合并后由 chat LLM rerank 取 Top-K。rewrite 失败退原 query，rerank 失败退融合排序。

### Q28：知识 chunk size 和 overlap 是多少？

**CURRENT。** 默认估算 Token 上限 360、overlap 48，优先段落/句末/空白边界；超长单句强制二分硬切。`chunk_id` 贯穿向量、BM25、RRF 与证据投影，最终才按父 `document_id` 去重。参数可由 `RAG_CHUNK_MAX_TOKENS` 和 `RAG_CHUNK_OVERLAP_TOKENS` 配置；当前值是工程基线，不是消融最优值。

### Q29：怎样实验 chunk 参数？

固定文档、query、embedding、Top-K 和 reranker，比较 256/360/512 Token 与 0/32/48/64 overlap。检索层看 Recall@K、MRR/nDCG、边界证据完整率与 chunk 投影一致性；端到端看 grounded answer、拒答率、延迟、索引体积和 Token 成本。

### Q30：为什么 LLM rerank 不用 cross-encoder？

当前 LLM rerank 是依赖少、可重用 provider 的工程取舍，不是证明它更好。cross-encoder 往往延迟更可控、结果更稳定；应在同一 judged query-document 集比 nDCG/MRR、P95、成本和错误 slice。

### Q31：query rewrite 的价值和风险？

它将一种用户表达扩展成多种检索表达，提高候选覆盖；代价是 LLM 延迟和 query drift。要做原 query / rewrite only / rerank only / rewrite+rerank 四组消融。

### Q32：长期记忆与知识库有什么区别？

知识库是公共业务事实；长期记忆是某个用户的历史情景，必须按 user 隔离。两者不共享排名管线，也不能互相充当权威事实。

### Q33：混合长期记忆怎么做？

**NEW。** 原始历史按 1200 字符、120 overlap 写入 episodic collection。在用户边界内分别取 vector 和 BM25 候选，只对候选并集做 recency，最后用 RRF `k=60`、vector/BM25/recency 权重 `0.30/0.60/0.10` 融合。

### Q34：为什么 BM25 权重更高？

订单号、错误码和金额精确 token 对词法召回更敏感。`0.60/0.30/0.10` 是工程初值，不是最优值；要用 held-out query/relevant-id 集调参，同时看精确实体 slice 和延迟。

### Q35：为什么 recency 不能单独召回？

“最新”不等于“相关”。recency 只能重排 vector/BM25 已召回的候选，不能把无关的新记忆拉进候选池。

### Q36：压缩记忆的当前参数？

6000 Token 预算，估算达 70% 触发，摘要视图上限 1200 Token。普通压缩保护最近 2～5 条原文，其余从最旧未覆盖事件开始形成有界 seq 范围摘要；Prompt 仍由 `ContextAssembler` 按总预算裁剪。提交只 WATCH checkpoint，新消息拥有更大 seq，不会让已固定范围失效。

### Q37：记忆错了怎么办？

组件不可用时降级无记忆；内容不准时，当前用户输入和当前业务知识优先，记忆只是背景。已有用户隔离、Top-K、原始片段和 rank 证据；未有记忆编辑/删除 UI、事实有效期和持久反馈学习。

## ReAct、工具权限与 Trace

### Q38：ReAct 在哪一层？

**NEW。** ReAct 只在已领取 TaskSpec 的 Worker 内，用于选择和观察工具，最多 4 步。它不拥有总计划，不能改写 TaskPlan。外层确定性、内层有界推理是核心分层。

### Q39：怎么防止越权工具？

工具发现只暴露 Agent allowlist，执行时 ToolManager 用同一 allowlist 再校验。模型编造工具名会得到 `denied`。授权不是 Prompt 约定，而是执行边界的权威决策。

### Q40：模型能伪造 `approved=true` 吗？

不能。`approved` 不在暴露给模型的 input schema，只由可信宿主传给执行器。高风险/写工具默认没批准就无副作用终止。

### Q41：工具并发如何处理？

同一 ReAct 步中只读工具可并行，潜在写工具串行，避免顺序未定义的副作用。这不是分布式事务；生产写工具还需业务幂等 key、版本或 typed receipt。

### Q42：怎么防 ReAct 死循环？

默认最多 4 个 model/tool step，`tool_use` 与 `tool_result` 按 call_id 配对。超限收敛为 `MAX_STEPS`，不让 General 文本覆盖该失败。

### Q43：Trace 记录了什么？

`TraceId` 通过 `contextvars` 从 HTTP 传到 asyncio Worker、ReAct step 和 Tool span；工具记录 call_id、Agent、状态、延迟、参数哈希/形状和截断结果元数据。不保存完整敏感参数/输出，避免把可观测系统变成泄漏面。

### Q44：这是完整 MCP 吗？

**UNPROVEN。** `MCPToolManager` 是内部工具运行时的历史命名，有注册、schema、超时、缓存、熔断、fallback、权限和 audit，但没实现远程 MCP transport/protocol server。

## 融合、评测与生产边界

### Q45：多 Agent 结果怎样融合？

先按 TaskPlan 检查覆盖，再对成功候选按主辅去重和冲突检测。Synthesizer 输出 `success / partial / conflict / unavailable / single`。文本仍只是 candidate，只有 Verifier `PASS` 才有发布资格；`REJECT/UNKNOWN` 转人工。

### Q46：当前评测链路是什么？

有两条链。运行时链对 intent 算 Accuracy/Macro-F1，对 dialog 真实调 Orchestrator 并用 LLM Judge；确定性链对版本化 intent/routing/retrieval/stateful prediction 分别算分类、Owner/task、Recall@K/MRR/nDCG 和 assertion 指标。报告带 dataset version/checksum/split/review scope。

### Q47：Judge 分高就代表路由正确吗？

不代表，错 Agent 也可能碰巧生成流畅答案。要同时记 expected/actual Owner、TaskPlan、fan-out、coverage、budget、工具授权和发布状态，分开评“结果好”与“过程合同正确”。

### Q48：91.3%、0.89 等旧数字怎么回答？

**UNPROVEN。** 旧数字没对应数据版本、切分、运行产物和 commit，已移除。当前可证明的是 160 项回归测试、500 条分层候选集、25 篇 corpus、Stateful fixture 和隔离 RAG producer；因为 gold 仍为 0，不能报项目准确率。独立审核并运行新鲜 heldout 后才报均值、方差、slice 和置信区间。

### Q49：多 LLM 调用怎么降延迟？

先用 trace 分解 intent、rewrite、retrieval、rerank、Worker、Verifier 的 P50/P95/P99。已有按需 RAG、多 query 并行、有条件 fan-out、超时/缓存/熔断与共享 deadline。当前 Trace 尚未覆盖全链 span，不宣称已解决生产 P99。

### Q50：最大生产缺口是什么？

此前最大缺口是可信身份，不是 Prompt；现在 JWT `sub` 是用户身份，chat/knowledge/admin scope 控制接口，CORS 默认显式来源。剩余生产缺口是外部 IdP/JWKS、密钥轮换、tenant claim、resource/action ABAC、撤销与持久审计。

### Q51：Trace/审批还缺什么？

Trace 缺 OpenTelemetry exporter、持久存储、全链 span、采样与保留策略；审批缺 pending call 持久化、批准人/过期时间、resume endpoint。当前是安全闭合基线，不是完整人工审批平台。

### Q52：100 万条知识怎么检索？

不能将小型单 collection 结论直接外推。生产先用 tenant/ACL/业务类别 metadata pre-filter，再 ANN + 词法候选，用便宜 reranker 缩小后精排。还要测 shard/索引构建、更新一致性和 P99；当前仓库没有百万规模压测。

## STAR 表达与简历钩子

### Q53：如何用 STAR 讲 Multi-Agent 改造？

**S：** 旧链路只有 Agent 列表并拼文本，复合请求的子任务可静默丢失。**T：** 保留并行优势，让完整性、失败和超时可证明。**A：** 引入 TaskPlan/TaskSpec、唯一 Owner、四态 outcome、ExecutionBudget、CoverageGate 与 typed synthesis，拆出 AccountSecurity Worker。**R：** API 返回计划、分数、覆盖缺口和预算证据，回归测试覆盖部分失败、超时与 fan-out；不编线上业务指标。

### Q54：如何用 STAR 讲混合记忆？

**S：** 旧版检索压缩摘要，订单号/错误码可消失。**T：** 保留精确事实并支持语义改写，让排名可解释。**A：** 以原始重叠片段为事实载体，用户内融合 BM25/vector/recency ranks，两路独立降级，输出 source ranks 和 Recall@K/MRR/nDCG。**R：** 精确 token 用例不再只依赖向量，相关不变式有测试；权重仍需真实数据调优。

### Q55：如何用 STAR 讲 ReAct 权限与 Trace？

**S：** 模型能调工具后可能编造名称、越权写、死循环且难归因。**T：** 保留外层确定性计划，让 Worker 安全使用工具。**A：** 实现 4 步 ReAct、双重 allowlist、宿主审批、读并行写串行、输出截断、call_id 配对与 contextvars TraceId。**R：** 测试证明越权零副作用、循环有界和 Trace 跨并行传播；持久审批/OTel 仍未实现。

### Q56：简历可以怎么写？

1. 利用 `TaskPlan + CoverageGate + ExecutionBudget` 解决复合客诉中任务静默丢失与 timeout 叠加，按 task_id 输出成功、超时、异常与预算耗尽证据。
2. 利用 BM25、Chroma 向量检索与 weighted RRF 解决压缩摘要丢失精确实体的召回失真，建立 Recall@K/MRR/nDCG 回归接口。
3. 利用有界 ReAct、Agent 白名单和宿主审批解决工具越权、死循环与不可追溯，将拒绝/失败证据传到发布校验。
4. 利用单调 seq、不可变范围摘要和 checkpoint CAS 解决长对话溢出与并发写覆盖，保留可重建原文和来源证据。
5. 利用 fail-closed AnswerVerifier 与幂等 TicketService 解决高风险候选误发布和重复升级，确保只有 PASS 回复进入记忆。

## 公司轮次模拟与新追问

### Q57：“500 条数据从哪来”怎么答？

不沿用旧说法。当前正好有 500 条候选，但不是 500 条 gold：180 条 auto-mapped 外部压力样本、320 条 provisional 项目合同。下一步从脱敏工单分层抽样，双人标注+仲裁，按用户/时间/group_id 去重切分，保留无效输入与复合难例；不能把自动映射数量冒充人工项目标注。

### Q58：“91.3% 是高还是低”怎么答？

没有数据分布、错误成本和 baseline，单一准确率无法判高低。账户安全/财务的 false negative 可比问候误分类贵得多。应报类别 slice、置信校准、拒识率和业务成本，不为旧数字辩护。

### Q59：如果下一步只改一件事？

要上生产，在已有 JWT/scope 基线上先接企业 IdP/JWKS、tenant/resource ABAC 与密钥轮换；要证明算法价值，先做去污染 held-out 数据集和 intent/retrieval/routing 消融；要长流程可恢复，再引入 durable graph 和持久审批。不是默认再加 Agent。

### Q60：怎样回答“项目最难的点”？

不答“模块协同很难”。选一条可验证故事：旧并行链路只有 Agent 文本列表，异常破坏整体且无法证明子问题完成；于是引入 task_id/Owner、四态 outcome、共享预算和 CoverageGate；最后用部分成功、超时、重复/计划外 outcome 测试验证。

## 新增边界追问：这次代码收敛了什么

### Q61：短会话没触发压缩，怎样保证长期记忆？

客户端在会话关闭时调用 `POST /conversations/{conv_id}/finalize`。MemoryManager 固定调用开始时的 high-water，把所有未覆盖范围按稳定 message ID 幂等 upsert，再逐段推进 checkpoint；原始事件不删除。若完成后发现更大 seq，则返回 409，下一次只处理新增范围。

### Q62：为什么用户画像现在能确定性读取？

不再把整个画像合并进一条可覆盖 JSON。模型只能对支持的 fact key 产出 `upsert/retract`，每条事实携带来源 message ID、source seq、置信度和生命周期。标量事实由更新的 source seq 替代旧值，多值事实可共存；Prompt profile 只是 active facts 的投影。当前进程锁避免本进程内交错提交，多副本仍需共享存储 CAS。

### Q63：Verifier 已替换顶层 response，为什么还要删 outcome content？

因为拒绝候选仍可能从嵌套诊断字段泄漏。内部 verifier/feedback 需要完整 outcome，但公开 API 只投影状态、延迟与类型化原因，删除 candidate、raw error、agent key 和 tool call IDs。管理员排障应走独立受控接口。

### Q64：请求体传别人的 user_id 还能查记忆吗？

不能。HTTP 先验证 JWT，所有记忆操作使用签名 `sub`；请求体 user_id 只是旧客户端一致性检查，冲突直接 403。chat、knowledge 和 admin scope 分开。要诚实说明：这还是本地 HS256 基线，不是完整企业 IAM。

### Q65：Chroma 远程挂掉为什么取消本地 fallback？

远程和 embedded 是不会自动合并的两套物理存储，静默切换会产生 split-brain。现在 `CHROMA_MODE=remote` 不可达就启动失败；开发要本地库必须显式 `embedded`，`/health` 报告实际 mode/location。

### Q66：在线降权以前为什么可能是“看起来有闭环”？

每种类型只有一个 `_0` 实例，分数再低也没有 `_1` 可选。现在 stats 输出 pool size 与 adaptive flag；singleton 只告警不施加选择 penalty。测试另建同类双实例，证明质量反馈确实能改选。

### Q67：Escalation 为什么要是真实 Agent？

TaskPlan 已把人工交接指定给 `AgentType.ESCALATION`，却由 General 执行会让 Owner 和 responder 不一致。现在 tool-free EscalationAgent 专门整理诉求、风险、证据和待核实项；真正工单事实仍由 API/TicketService 创建，不让 LLM 声称已建单。

### Q68：这一轮简历怎么写成一条？

> 利用 JWT Principal/scope、确定性消息归档与显式存储模式收敛 Agent 服务生产边界，解决用户身份伪造、短会话 TTL 丢失、拒绝候选旁路泄漏和 Chroma 双库分叉；补齐 tool-free Escalation Owner 与路由基数诊断，以对应不变量测试验证失败可重试、数据不丢失和公开投影最小化。

这句信息密度高，面试时优先拆成“身份/发布”或“记忆生命周期”一条 STAR，不要一次全背。

## 新增评测数据追问：从“有数据”到“能报数”

### Q69：现在仓库到底有哪些评测数据？

三类必须分开：11 条 intent + 5 组 dialog 是内置 smoke；`dialogpilot-500-v1` 是 500 条四层候选集 + 25 篇 retrieval corpus；其中 BANKING77、CLINC150 OOS 部分是 auto-mapped external pressure，项目合同仍是 provisional。当前 human-reviewed gold 是 0。

### Q70：为什么公开数据不能直接算项目准确率？

它们的标签和业务边界不是 DialogPilot 的 TaskPlan、知识库、记忆与工具规则。公开集能测试迁移、金融细粒度和 OOS 拒识，不能证明复合路由、证据 ID、用户隔离或零副作用授权。

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

## DeepSeek 分层调用与模型选型

### Q77：现在具体用了哪些模型？

默认矩阵不是“全局一个 DeepSeek”。Intent、Worker、ReAct、Memory、rewrite、rerank 使用 `deepseek-v4-flash + reasoning none`；跨 Agent synthesis、AnswerVerifier 和文本质量 Judge 使用 `deepseek-v4-pro + reasoning none`。外层 TaskPlan 是确定性 Python，不调用 Planner LLM；routing 层也不调用 Answer Judge。

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

**S：** 九类调用共用一个模型，DeepSeek 默认 reasoning 让简单任务成本、延迟和结构化输出不可控。**T：** 在保持统一 Messages API 的同时，让每类调用可独立权衡质量。**A：** 实现按角色校验的 ModelPolicy，Flash/none 承担闭合高频任务，Pro/none 承担融合与质量门禁；显式 reasoning 强制最小完成预算，并补齐 health/eval 配置证据和 ReAct thinking 回传。**R：** 4 条 E2E pilot 均值约 28.4s → 13.3s，Verifier 解析 2/4 → 4/4；160 项回归测试通过。15 条 provisional 三档消融已完成，最终选择仍需 gold heldout 与重复运行确认。

### Q85：Flash/off、Flash/high、Pro/high 真跑后有什么区别？

同一 15 条 provisional seed、同一 15s/20s 预算下：Flash/off 的 Intent 与 Task success 都是 8/8；Flash/high 都是 7/8，case P95 +25.1%、峰时成本至少 +41.7%；Pro/high Intent 7/8、Task success 3/8，峰时成本下界约为 Flash/off 的 2.9 倍。Owner Exact 三档都是 7/7，只说明 Planner 分工正确，不说明 Worker 做完。结论是当前数据不支持全局开 high 或全局升 Pro；完整口径见[模型消融报告](./model-ablation-report.zh-CN.md)。

## 面试前 10 分钟自查

1. 能画出 `/chat` 从 TraceId 到记忆写回的顺序，不混 Knowledge RAG 和 Memory Retrieval。
2. 能说出 5 个真实 Worker、supporting 0.45、20s/15s/3 agents 预算和 4 步 ReAct。
3. 能说出 Knowledge chunk 360 Token/48 overlap 与 Memory chunk 1200 字符/120 overlap，并解释两条链路身份不同。
4. 能说出 Task outcome 四态、synthesis 五态与 Verifier PASS/REJECT/UNKNOWN。
5. 能解释 BM25 对订单号的价值，以及 recency 为什么不能独立召回。
6. 能解释发现/执行共用 allowlist，审批不来自模型参数。
7. 能说明 160 tests 不等于 160 个 benchmark，smoke、auto_mapped 与 provisional 都不支持生产准确率。
8. 能用 commit 划清原型与个人改造，不说从零原创。
9. 能讲清 JWT/scope 已完成，以及 IdP/JWKS、tenant ABAC、持久 Trace、可恢复审批和真实 benchmark 仍是缺口。
10. 不说“精通 LangGraph”、“完整 MCP”、“项目是 SOTA”或“线上准确率 91.3%”。

> 完整仓库链路、源码定位和更多底层追问，回到 [DialogPilot 完整架构教程](./)。
