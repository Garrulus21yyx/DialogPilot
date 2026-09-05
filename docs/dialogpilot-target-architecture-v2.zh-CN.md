# DialogPilot Target Architecture v2

状态：冻结目标架构（实现中）  
日期：2026-09-05  
当前实现基线：`512d1c0`（Target Architecture v1）

## 1. 文档目的

本文定义 DialogPilot 通用电商客服的下一版权威架构。它在 v1 已完成的治理与执行主链上，
补齐统一的 Conversation Agent、上下文接线、自然指代、多轮回复组织、后台 Run、SSE
重连和模型调用前预算管理。

本文是目标合同，不把尚未实现的能力描述为当前能力。v1 的实现证据仍见
`docs/target-architecture-v1-core-report.zh-CN.md`。

## 2. 冻结结论

1. `TargetConversationManager` 是会话与权威状态生命周期 Owner，不是 LLM Agent。
2. 系统设置一个逻辑上的 `ConversationAgent`，作为用户感知到的统一客服主体。
3. `ConversationAgent.plan()` 吸收并替代独立的 `StructuredTargetCommandRouter`；同一轮
   只存在一个全局 LLM 语义规划入口。
4. Deterministic Resolver 和 Intent Encoder 位于 Conversation Agent 之前。明确状态续接
   与高置信低风险请求可以走快速路径，不强制调用主 Agent。
5. Conversation Agent 负责上下文理解、指代消解、多目标拆解、澄清和任务委派；它不
   保存权威状态，不授予权限，不直接执行业务写入。
6. Domain Agent 仅在开放的领域目标需要自主规划时调用。原子 Tool、固定复合 Skill 和
   受控 Flow 不应为了展示 Multi-Agent 而绕经额外 Agent。
7. LangGraph 负责图执行、依赖、并行、暂停、恢复和 checkpoint；业务数据库负责
   ConversationState、Flow CAS、Pending Signal、Operation、Receipt、Publication 和审计。
8. `ConversationAgent.compose()` 只在复杂结果需要统一叙述时调用，只能消费已验证事实。
9. Publication 是唯一允许提交用户可见消息的边界；Agent 可以生成候选文本，但不能绕过
   Verification 与 Publication 自行交付。
10. SSE 连接不是业务 Run 的 Owner。连接断开不能自动取消会话、Flow 或后台任务。
11. Transcript 无损保存；Summary 是可重建投影；跨会话 Memory 按需读取；单次模型调用
    的局部压缩不改写全局摘要。
12. 不为商品类别逐一创建 Skill。商品类别、属性和安装规则属于 Catalog/RAG 数据。
13. 不接受面向单个句式、Intent、商品类别、测试样例或故障现象的特化补丁。变更必须修复
    负责该语义的 Owner、通用合同或状态转换，并覆盖同一因果面的所有生产者与消费者。

### 2.1 通用性与最小充分治理原则

“安全”不等于为每个已知案例增加规则，“自主”也不等于让模型绕过确定性合同。v2 采用
最小充分治理：只固定权威、权限、状态代数、输入输出 Schema 和副作用边界，把开放语义
理解、低风险工具组合与自然表达留给 Agent。

禁止以下实现方式：

- 根据某个用户句子或关键词新增生产分支来修复指代、路由或缺参；
- 为某个商品类别、安装场景或旧 Intent 标签创建专属 Agent/Skill/Flow；
- 在 Router、Orchestrator、Publication 或测试夹具中补偿上游 Owner 的错误语义；
- 为通过一个回归样例而放宽权限、Requirement、Receipt 或来源校验；
- 用重试掩盖未知写结果，或用摘要/投影掩盖权威状态缺失；
- 为“未来可能需要”建立第二套 Registry、调度器、Memory 或状态机。

允许并要求的扩展方式：

- 新业务能力通过 Registry 数据和稳定 Tool/Action Schema 接入；
- 新指代表达通过通用 `EntityBinding` 候选、来源和歧义代数处理；
- 新上下文来源通过统一 Context Item、优先级、预算和可信度合同接入；
- 新任务形态通过已有 `DIRECT/RUN_SKILL/DELEGATED/WORKFLOW` 代数表达；
- 新故障通过 typed outcome 与既有恢复状态机处理；
- 回归测试作为见证，同时补充属性、状态机、生成式或模型化测试证明通用不变量。

变更评审必须能够回答：症状是什么、触发是什么、直接机制是什么、共享根因是什么、权威
Owner 是谁、正向合同是什么、哪些生产者和消费者被一并迁移。无法回答时继续诊断，不提交
猜测性修复。

## 3. 总体架构

```text
Client / Web / API / IM
          │
          ▼
Admission ── 持久化输入、身份、幂等键、Run 请求
          │
          ▼
Background Run Owner
          │
          ▼
TargetConversationManager
  ├─ 加载 ConversationState 与 ContextSnapshot
  ├─ L0 Deterministic Resolver
  ├─ L1 Intent Encoder
  │      ├─ ACCEPT ───────────────┐
  │      └─ DEFER                 │
  │             ▼                │
  │     ConversationAgent.plan() │
  │             └────────────────┤
  ├─ RoutePolicy                 │
  ├─ TurnPlanCompiler ◀──────────┘
  │
  ▼
LangGraph Turn / WorkPlan Runtime
  ├─ DIRECT：原子 Tool
  ├─ RUN_SKILL：稳定复合 Skill
  ├─ DELEGATED：Domain Agent / Subgraph
  └─ WORKFLOW：受控持久 Flow
          │
          ▼
Result Board + Verification
          │
          ▼
ResponseAssemblyPolicy
  ├─ TEMPLATE
  ├─ PASS_THROUGH
  └─ CONVERSATION_COMPOSE → ConversationAgent.compose()
          │
          ▼
Final Candidate Verification
          │
          ▼
Publication → Delivery / Durable Event → SSE / Query API
```

对用户始终呈现一个客服身份。后台 Agent 是专业 Worker，不获得独立聊天渠道。

## 4. 责任与权威 Owner

| 问题 | 唯一 Owner | 明确不负责 |
|---|---|---|
| 谁保存会话与持续状态 | `TargetConversationManager` + Conversation/Flow stores | 语义猜测、业务工具执行 |
| 谁理解当前用户含义 | `ConversationAgent.plan()` | 权限授予、状态提交 |
| 谁提供低成本候选 | Intent Encoder | 最终路由授权、复杂指代 |
| 谁处理已绑定输入/审批 | Deterministic Resolver | 模糊语义推断 |
| 谁验证计划合法 | RoutePolicy | 工具执行、回复生成 |
| 谁编译执行合同 | TurnPlanCompiler | 重新解释自然语言 |
| 谁调度 DAG 与恢复 | LangGraph Runtime | 业务事实权威、审批授权 |
| 谁做领域内自主规划 | Domain Agent | 全局领域重判、跨领域直接派发 |
| 谁执行真实工具 | Tool Runtime | 最终回复和事实创造 |
| 谁持有事实与结果合并语义 | Result Board / Verification | 自由改写事实 |
| 谁组织复杂回复 | `ConversationAgent.compose()` | 工具调用、事实修改 |
| 谁提交并发送 | Publication + Delivery | 重新规划任务 |
| 谁维持后台执行 | Background Run Owner | SSE 订阅生命周期 |
| 谁实时推送 | SSE Adapter | 启动或取消业务任务 |

## 5. Conversation Agent 合同

Conversation Agent 是一个逻辑 Agent，分成两个受限端口。它可以由同一模型配置、角色和
上下文策略实现，但两个阶段之间必须经过策略、执行和验证边界。

### 5.1 `plan()`

```python
class ConversationAgent(Protocol):
    async def plan(
        self,
        context: ConversationPlanningContext,
    ) -> ConversationPlan: ...
```

职责：

- 解析当前消息与上下文中的指代；
- 识别一个或多个用户目标及其依赖；
- 判断直接回答、Tool、Skill、Domain Agent、Flow、澄清、人工或能力越界；
- 只输出 Registry 可验证的结构化计划提议；
- 对无法唯一绑定的业务对象返回 typed ambiguity，不凭空补参数。

允许输出：

```text
RESPOND_DIRECT
RUN_TOOL
RUN_SKILL
DELEGATE_GOAL
START_FLOW / CONTINUE_FLOW / PAUSE_FLOW / CANCEL_FLOW
CLARIFY
HANDOFF
NO_SUPPORTED_CAPABILITY
```

禁止直接执行：

- 业务写工具；
- Flow 状态提交；
- 权限或审批判定；
- Publication；
- 未经 Registry 注册的 Tool/Skill/Flow。

### 5.2 `compose()`

```python
class ConversationAgent(Protocol):
    async def compose(
        self,
        context: ConversationCompositionContext,
    ) -> ResponseCandidate: ...
```

输入只包含当前问题、相关前文、经过验证的 Facts、允许表达的 Claims、WorkItem Outcomes、
Missing Inputs、Receipt 摘要、交付状态和风格规则。

它不能：

- 再调用业务工具；
- 修改金额、订单状态、权限、Receipt 或 Flow 状态；
- 把 `USER_ASSERTED` 升级为 `VERIFIED_STATE`；
- 隐藏关键部分失败或不确定状态。

输出必须再经过最终 Claim/Citation/Receipt 验证，才能提交 Publication。

## 6. Intent Encoder 与 Conversation Agent 不重合

Intent Encoder 是轻量候选模型：

```text
输入：当前消息 + 有界确定性特征
输出：Domain/Goal Top-K、实体候选、confidence、boundary score、ACCEPT/DEFER
```

它只能在以下条件同时满足时 ACCEPT：

- 当前消息可独立理解；
- Domain/Goal 唯一且类别门禁通过；
- 参数来自当前输入或已验证的唯一会话绑定；
- 不存在多目标、冲突 Workstream 或普通自然语言指代；
- 目标为 Registry 允许的低风险读取或固定 Skill。

否则 DEFER 给 `ConversationAgent.plan()`。

Conversation Agent 读取更完整的会话上下文，解决指代、歧义、多目标、依赖和对话行为。
现有 `StructuredTargetCommandRouter` 的 Goal、Clarify/OOS、多目标和 CommandProposal 语义
职责全部迁入 `ConversationAgent.plan()`，不作为并列 LLM 节点保留。

## 7. 最短安全执行路径

每轮根据需求选择最短路径，而不是默认 Multi-Agent：

| 请求形态 | 执行路径 |
|---|---|
| 已绑定字段、审批、恢复信号 | Deterministic → 编译/恢复 |
| 明确原子读取 | Encoder/Fast Path → DIRECT Tool |
| 明确稳定复合能力 | Encoder/Conversation Agent → RUN_SKILL |
| 简单 Knowledge QA | Knowledge RAG，不启动 Domain Agent |
| 开放的单领域目标 | Conversation Agent → 一个 Domain Agent |
| 多个独立领域目标 | Conversation Agent → LangGraph `Send` 并行 |
| 有依赖的多个目标 | 编译 DAG，按依赖波次执行 |
| 高风险或跨轮写操作 | WORKFLOW + Approval + Operation + Receipt |

`primary_agent` 只表示当前任务的领域 Owner，不能冒充全局 Supervisor。

## 8. Domain Agent、Tool、Skill 与 Flow

```text
Conversation Agent
  └─ DelegateGoal（仅开放目标）
       └─ Domain Agent
            ├─ 直接调用 allowlist 内原子 Tool
            ├─ 调用可选复合 Skill
            ├─ 请求 Evidence
            ├─ 返回 MissingInput
            └─ 返回 NEEDS_EXTERNAL_CAPABILITY

明确原子目标 ───────────────→ Tool Runtime
明确稳定复合目标 ───────────→ Skill Runtime
高风险/跨轮目标 ───────────→ Governed Flow
```

Tool 必须注册。Skill 是可复用、可版本化的复合能力，不是所有工具的强制包装层。Flow 只
用于需要阶段、等待、审批、恢复、幂等或对账的任务。

Domain Agent 不重新判断全局 Domain，不直接创建其他领域 WorkItem。发现跨领域需求时返回：

```python
ExternalCapabilityRequest(
    objective=...,
    requirement_ids=...,
    evidence=...,
)
```

由父级策略验证并扩展 WorkPlan。

## 9. 上下文架构

持久状态与发送给 LLM 的消息是两套结构。所有上下文先以 typed item 保存、过滤和预算，
最后才渲染成模型协议消息。

### 9.1 ContextSnapshot 的来源

```text
Authoritative current state
  ├─ active workstreams / flow phase
  ├─ pending interaction / approval
  ├─ active case
  ├─ current business object bindings
  └─ latest committed receipts

Conversation projections
  ├─ recent public transcript
  ├─ bounded thread summary
  └─ delivery/ack state

On-demand evidence
  ├─ ServiceEpisode memory
  ├─ Knowledge RAG
  ├─ Media evidence
  └─ execution/evidence/receipt refs
```

投影不可覆盖权威业务状态。读取失败时保留 typed degradation，并在必要时回退到原始事件的
有界读取；不能将空投影解释为“用户没有历史”。

### 9.2 Planning Context

`ConversationPlanningContext` 至少包含：

```text
客服角色、行为和能力边界
当前用户消息与附件引用
最近相关的公开 user/assistant 消息
有界 Thread Summary
active workstreams / pending signal / active case
有来源的当前业务对象绑定
最近已完成任务的简短结果
最近 Publication 与 Delivery/ACK 状态
Intent Top-K 与置信度
必要的 Memory / Media Evidence
有限的 Agent/Tool/Skill/Flow capability cards
token / step / latency budget
```

### 9.3 Domain Agent Context

按 WorkItem 筛选，只提供：

- 领域 Prompt 与本次 objective；
- 当前用户原话和相关限制；
- 与本 WorkItem 有关的业务对象；
- 已验证 Facts、Evidence refs 和必要 Receipt refs；
- 相关近期对话片段，不是全部 Transcript；
- 本次允许的 Tool/Skill envelope；
- 输出合同、最大步骤和 Token 预算。

安全 Agent 不接收完整商品推荐历史；Product Agent 不接收无关账户事件。

### 9.4 Composition Context

只包含当前问题、必要前文、已验证结果、缺失字段、部分失败、允许 Claims、Receipt 摘要、
风格政策和交付状态。不得把原始工具载荷或子 Agent 完整工作消息交给 compose 阶段。

## 10. 指代与业务对象绑定

指代解析按权威性排序：

```text
1. PendingInteraction / Approval 的精确字段绑定
2. 唯一 active workstream / active case / current object binding
3. 最近公开对话中的显式业务对象
4. 有来源的 Thread Summary 绑定
5. 一次按需 ServiceEpisode 检索
6. ConversationAgent 语义消歧
7. 仍不唯一 → CLARIFY
```

绑定结果必须保留：

```python
EntityBinding(
    entity_type="order",
    entity_id="DP1234",
    source_kind="RECENT_TRANSCRIPT",
    source_ref="event:...",
    observed_at=...,
    confidence=...,
)
```

Router/Agent 不得生成输入和可信上下文中不存在的业务 ID。历史记忆可以证明过去说过什么，
但订单、退款、账户的当前状态仍需权威 Tool 刷新。

## 11. Memory 与压缩

### 11.1 五种不同用途

| 数据 | 用途 | 默认注入策略 |
|---|---|---|
| Public Transcript | 用户实际看到的对话 | 最近窗口 |
| Current State | 当前流程、工单、审批、对象 | 与本轮相关的结构化子集 |
| Thread Summary | 压缩较旧公开对话 | 一个有界摘要 |
| ServiceEpisode | 跨会话历史与旧承诺 | 只有触发时检索，理解前最多一次 |
| Agent Run / Checkpoint | 恢复、审计、回查 | 默认只传结果与引用 |

保存不等于每轮注入模型。Summary 不能替代 Transcript，Agent Run 不能变成公开聊天历史。

### 11.2 持久摘要

继续使用现有 Thread Summary 投影：按消息数、Token 估算、会话事件和固定范围生成，保存
source range、watermark、hash 与 summarizer version，并支持重建。投影失败不影响原始事件。

### 11.3 模型调用前预算管理

每次 Agent 调用前计算：

```text
input_budget
= model_context_window
 - output_reserve
 - reasoning/protocol_safety_margin
```

超预算时按顺序处理：

1. 将大工具结果外置，只保留必要字段与引用；
2. 删除无关历史和重复证据；
3. 压缩已完成的旧任务过程；
4. 用 Thread Summary 替换更旧的公开消息；
5. 缩小当前目标或按需读取证据；
6. 仍无法满足时返回 typed context-budget failure，不静默截断关键约束。

必须优先保护当前输入、未完成目标、否定/取消、写操作限制、业务对象 ID、Pending Signal、
Approval binding、Operation key 和关键 Receipt。

框架 Agent 的局部 Summarization Middleware 只压缩该次工具循环，不回写全局 Thread Summary。
删除消息时必须保持 assistant tool call 与 tool result 配对。

## 12. LangGraph 执行边界

### 12.1 当前 v1

当前 LangGraph 只承载形成后的 WorkPlan：

```text
initialize → Send(execute_work_item × N) → merge_results
           → more work / await_resume / finish
```

理解、RoutePolicy、编译和回复组织仍在图外普通 Python 应用服务中。

### 12.2 v2 目标

先保持现有 WorkPlan Graph 作为稳定执行子图。需要规划和回复阶段具备进程崩溃恢复时，再
建立一张薄 `TurnGraph`：

```text
prepare_context
  ↓
deterministic_and_fast_path
  ├─ resolved ───────────────┐
  └─ conversation_plan      │
             ↓              │
validate_and_compile ◀──────┘
  ↓
work_plan_subgraph
  ↓
collect_verified_results
  ↓
select_response_mode
  ├─ template/pass-through
  └─ conversation_compose
  ↓
verify_candidate
  ↓
prepare_publication
```

同一段分支、循环、重试和恢复逻辑只能有一个 Owner。阶段迁入图后，Manager 不再在图外
重复执行同一阶段。

Checkpoint 可以保存完整图 State，但不能成为业务事实权威。会话 ID 表示持续对话；
checkpoint thread ID 表示一次可恢复 Run/Workstream，两者不强制相同。

## 13. 子 Agent 状态与返回合同

### 13.1 默认：per-invocation

独立读取或分析任务使用隔离的工作上下文。完成后保存结构化 `AgentResult` 和执行引用，
下一任务不继承完整内部消息。

### 13.2 需要内部恢复：Subgraph checkpoint

需要在工具循环中细粒度恢复的领域 Agent 才迁移为框架 Agent/Subgraph。外层一个 Worker
节点包住旧 ReAct，不会自动让内部每一步变成 LangGraph checkpoint。

### 13.3 跨轮领域任务：per-workstream

复杂诊断需要跨轮工作消息时，子图线程绑定具体 `workstream_id`，不绑定笼统的 Agent 类型。
父图与子图使用不同 State Schema，通过适配节点显式映射。

### 13.4 返回主 Agent 的内容

```python
AgentResult(
    work_item_id=...,
    status=...,
    summary=...,
    facts=...,
    evidence_refs=...,
    missing_inputs=...,
    action_receipts=...,
    requested_capabilities=...,
    execution_ref=...,
)
```

允许简短自然语言说明，但金额、对象、状态、来源与副作用必须结构化且可追溯。主 Agent 默认
不接收子 Agent 的完整 Prompt、思维过程、所有检索候选或冗长工具输出。

## 14. Response Assembly 与 Publication

```python
class ResponseAssemblyMode(str, Enum):
    TEMPLATE = "template"
    PASS_THROUGH = "pass_through"
    CONVERSATION_COMPOSE = "conversation_compose"
```

| 模式 | 适用情况 |
|---|---|
| TEMPLATE | 状态确认、审批、OOS、单一 Receipt、简单工具结果 |
| PASS_THROUGH | 一个领域候选完整且最终验证通过 |
| CONVERSATION_COMPOSE | 多领域、部分失败、因果解释、引用前文、统一澄清 |

多个 MissingInput 先由 Clarification Policy 去重、补证和排序，再产生一个绑定所有目标字段的
InteractionRequest。Conversation Agent 可以优化自然表达，但不能改变字段绑定。

Publication 提交：

- 最终回复；
- 统一 InteractionRequest；
- 已提交的 HumanReply。

Agent、Tool、Skill 和 RAG 只能产生候选内容或结构化结果，不能绕过这个出口。Publication
必须幂等并与 invocation、bundle、evidence hash 和 delivery operation key 绑定。

## 15. 后台 Run 与 SSE

### 15.1 四种生命周期

| 层 | 状态示例 | Owner |
|---|---|---|
| Connection | CONNECTED / DISCONNECTED | SSE Adapter |
| Run execution | QUEUED / RUNNING / INTERRUPTED / COMPLETED | Background Run Owner |
| Conversation/business | OPEN / WAITING_INPUT / HUMAN_OWNED / RECONCILING | Conversation/Flow stores |
| Model context | LOADED / COMPACTED / DEGRADED | Context Builder |

`SSE=DISCONNECTED` 可以与 `Run=RUNNING`、`Flow=WAITING_APPROVAL` 同时成立。

### 15.2 提交与订阅分离

```text
POST message
  → Admission 持久化
  → 返回 invocation_id / run_id
  → Background Worker 执行

SSE subscribe(run_id, after_event_id)
  → 读取持久事件
  → 推送增量
  → 断开时只结束订阅
```

后台 Run 可由现有 FastAPI/PostgreSQL Outbox Worker 或 LangGraph Agent Server 承载，但一个
Run 生命周期只能选择一个 Owner。框架选型须单独形成 ADR；两套后台调度不能并行成为权威。

### 15.3 重连合同

- 客户端重连订阅原 `run_id`，不重新提交原消息；
- 不确定 Admission 是否成功时，用相同 `request_id` 和相同输入重试；
- 持久事件具有稳定 `event_id`，支持 `after_event_id`/`Last-Event-ID`；
- 允许传输层重放，客户端按 `event_id`/`response_id` 幂等展示；
- SSE 临时事件过期后，通过 Transcript 与 Invocation 查询校准最终状态；
- SSE cursor 不能代替用户对正式回复的 ACK。

## 16. 崩溃与恢复合同

| 中断点 | 权威恢复依据 | 恢复行为 |
|---|---|---|
| 浏览器/SSE 断开 | Run + durable events | 后台继续，重连补事件 |
| API 进程退出 | Admission/Run store + checkpoint | 独立 Worker 接管 |
| Planning 崩溃 | TurnGraph checkpoint | 从最近保存阶段重算；未验证计划不执行 |
| Domain Agent 崩溃 | subgraph checkpoint / RunStore | 恢复未完成步骤 |
| 等待用户输入 | PendingInteraction + checkpoint | 校验 signal/version 后恢复原 WorkItem |
| 等待审批 | PendingApproval + operation binding | 审批通过后才执行写工具 |
| 写工具结果未知 | Operation key + Tool Ledger | 查询/对账，不盲目重复提交 |
| Receipt 已保存、compose 未完成 | Receipt + checkpoint | 只恢复回复阶段 |
| Publication 已提交、Delivery 不确定 | response ID + delivery key | 重放同一 Publication，不重新生成 |
| Summary 失败 | 原始 Transcript | 降级读取并异步重建 |

任何可能重新进入的节点，在 `interrupt()` 前不得执行无幂等保护的业务副作用。

## 17. 流式交付语义

默认只实时推送安全进度和已经提交的正式内容：

| 内容 | 是否可实时推送 |
|---|---|
| 已受理、正在查询、某 WorkItem 完成 | 是，状态事件 |
| 内部 Prompt、工具参数和原始输出 | 否 |
| 未验证业务结论 | 否，不作为正式回复 |
| 已验证并提交的回复/追问 | 是 |
| 已提交完整文本的分段传输 | 是 |

若未来提供 token 级草稿，必须标记 `draft`，不得写入正式 Transcript 或 Summary，高风险业务
结论默认禁用草稿预览。

## 18. 事实、证据与安全

事实来源继续区分：

```text
VERIFIED_STATE
USER_ASSERTED
MEDIA_OBSERVED
KNOWLEDGE_ASSERTED
DERIVED
```

Requirement 的权威来源由 Registry 定义，不采用多数投票。RAG、Memory、OCR、图片文字、
用户输入和工具文本均视为不可信数据，不获得系统指令权。

写操作必须具有：

```text
registered action + risk/effect
authority and injected identity
approval policy and binding
operation_key and semantic fingerprint
target entity version
typed receipt state
reconciliation policy
```

安全门禁按 capability 生效。某个写能力违反不变量时 fail closed，不阻断无关只读能力。

## 19. 可观测性与评测

每轮 Trace 至少记录：

- Deterministic/Encoder/Conversation Agent 哪一层消费了请求；
- Context source ranges、投影状态、压缩动作和 Token 使用；
- ConversationPlan、RoutePolicy 结果、WorkPlan fingerprint；
- WorkItem control mode、Agent/Tool/Skill/Flow 选择与依赖波次；
- checkpoint/run/interrupt ID，但不记录秘密与完整 Prompt；
- Facts、Evidence、Receipt、MissingInput、部分失败和冲突；
- ResponseAssemblyMode、compose 调用、最终验证与 Publication/Delivery 状态；
- SSE 重放、Run 接管和恢复次数。

核心指标：

```text
Reference Resolution Accuracy
Entity Binding Provenance Accuracy
Encoder Accepted Precision / Coverage
Conversation Plan Accuracy
Unnecessary Main-Agent Rate
Agent Delegation Accuracy
TaskGraph / Dependency Accuracy
Context Recall / Context Waste / Compression Loss
Partial Failure Preservation
Unsupported Claim Rate
Duplicate Side-effect Count
Resume / Rejoin Accuracy
P95 latency and token cost
```

必须与以下基线在相同模型、数据、工具和预算下比较：

```text
A. 单 Agent + 全部允许工具
B. Conversation Agent + Domain Agents，无 Encoder
C. Conversation Agent + Domain Agents + Encoder Fast Path
```

只有基准数据支持时才能声称优于基线；架构本身只描述为 state-first hierarchical
manager-worker 与 governed execution。

## 20. 系统不变量

1. 每个权威事实、状态转换和 Run 生命周期只有一个 Owner。
2. 同一轮只有一个全局 LLM 语义规划入口。
3. Intent Encoder 的 DEFER 不得被下游当作负分类结论。
4. Conversation Agent 不保存权威状态，也不授权业务副作用。
5. 明确原子请求不应为了形式而调用 Domain Agent。
6. Domain Agent 不能越过 WorkItem capability envelope。
7. Flow mutation 与业务 Tool side effect 严格分离。
8. 写结果未知必须对账，禁止盲重试。
9. Summary 和 checkpoint 都不能替代原始 Transcript、业务状态或 Receipt。
10. SSE 断开不能自动取消后台 Run。
11. 子 Agent 工作消息不能自动并入公开 Transcript。
12. compose 只能组织已验证事实，生成后必须再次验证。
13. 无成功 Ticket Receipt 不得宣称已转人工。
14. 无成功 Publication commit 不得把候选消息当成已发送事实。
15. 恢复必须绑定 tenant/user/conversation/signal/version/run，不接受 stale 或跨主体信号。
16. 生产路径不得以具体句式、旧 Intent 标签、商品类别或测试 case ID 作为业务语义权威。
17. 一个回归样例通过不能单独证明修复完成；必须验证其所属通用合同或状态转换。

## 21. 当前 v1 与 v2 差距

| 能力 | v1 当前状态 | v2 目标 |
|---|---|---|
| 全局语义入口 | Bounded + Encoder + Structured Router | Bounded + Encoder + `ConversationAgent.plan()` |
| 最近历史进入理解层 | 未完整接通 | typed Planning Context 全量有界接入 |
| 普通指代 | pending/active state 与显式历史有限支持 | provenance binding + 语义消歧 |
| 回复组织 | 模板与 candidate 拼接 | 三模式 Assembly + 按需 compose |
| 最终生成验证 | Result Board 为主 | compose 后 Claim/Citation/Receipt 再验证 |
| LangGraph 范围 | WorkPlan 执行/中断 | 稳定执行子图；按恢复需要扩展薄 TurnGraph |
| HTTP 生命周期 | 请求内等待执行 | Admission 与后台 Run 分离 |
| SSE | 尚未形成 durable rejoin 主链 | cursor/replay/query/ACK 协同 |
| 单次模型预算 | 摘要 + 最近窗口 +静态 budget | 每次调用前预算、裁剪、局部压缩 |
| Domain Agent 内部循环 | 现有 BaseAgent/ReAct | 分阶段适配框架 Agent，不长期双轨 |

## 22. 非目标

- 不构建永远常驻并拥有隐藏状态的超级 Agent。
- 不让 Conversation Agent 直接拥有全部工具或写权限。
- 不将每个业务 Intent、商品类别或原子 Tool 包装成 Skill。
- 不默认把所有请求派发给多个 Agent。
- 不要求所有普通应用服务都改写成 LangGraph Node。
- 不让 LangGraph checkpoint、Memory 或 Summary 成为第二套业务数据库。
- 不同时维护两套等价的后台 Run Owner 或通用 ReAct 引擎。
- 不以“使用 Multi-Agent/LangGraph”本身作为质量或 SOTA 证明。

## 23. 最终定义

DialogPilot Target Architecture v2 是一个：

```text
持久、确定性的会话生命周期控制面
+ 可绕过的 Intent 低成本快速路径
+ 一个上下文感知的逻辑 Conversation Agent
+ 少量按需调用的领域 Agent
+ 注册化原子 Tool、可选复合 Skill 与受控 Flow
+ LangGraph 执行、并行、中断和恢复
+ 权威业务状态、Receipt 与可重建 Memory 投影
+ 统一结果验证、按需回复合成与唯一 Publication
+ 与客户端连接解耦的后台 Run 和可重放交付
```

它的核心不是让所有步骤 Agent 化，而是在不牺牲会话连续性的前提下，为每个请求选择最短、
最安全、可恢复且可验证的执行路径。
