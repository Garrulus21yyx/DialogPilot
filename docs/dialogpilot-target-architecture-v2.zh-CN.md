# DialogPilot Target Architecture v2

状态：冻结目标架构（实现中）  
日期：2026-09-05  
当前实现基线：`512d1c0`

## 1. 目标

在 v1 已完成的受控执行主链上，补齐统一 Conversation Agent、上下文指代、按需回复合成、
后台 Run、SSE 重连和模型调用前预算管理。

本文描述目标合同，不把未实现能力写成现状。v1 证据见
`docs/target-architecture-v1-core-report.zh-CN.md`。

分阶段落地见 `plans/dialogpilot-target-architecture-v2-implementation-2026-09-05.md`。

## 2. 冻结决定

1. `TargetConversationManager` 保存会话与持续状态，不是 LLM Agent。
2. `ConversationAgent.plan()` 是 DEFER 后唯一全局 LLM 语义入口，吸收并替代
   `StructuredTargetCommandRouter`。
3. Deterministic Resolver 与 Intent Encoder 位于主 Agent 之前；明确续接和高置信低风险
   请求可以绕过主 Agent。
4. Domain Agent 只处理需要领域自主规划的开放目标。原子 Tool、固定 Skill 和受控 Flow
   不为展示 Multi-Agent 而绕经 Agent。
5. LangGraph 负责图执行、依赖、并行、中断、恢复和 checkpoint；业务 Store 负责状态、
   CAS、审批、Operation、Receipt、Publication 和审计。
6. `ConversationAgent.compose()` 只在复杂结果需要统一表达时调用，只消费已验证 Claims。
7. Publication 是唯一用户交付出口；Agent/RAG/Tool 可以生成候选，不能自行提交消息。
8. SSE 连接不拥有后台 Run；断线不等于取消、失败或会话结束。
9. Transcript 无损保存；Summary 是可重建投影；历史 Memory 按需检索；局部压缩不回写
   全局摘要。
10. 不为商品类别、旧 Intent、用户句式、badcase 或失败测试创建特化 Agent/Skill/Flow/分支。
11. 采用成熟的模块化单体：现有模块能清晰承载职责时直接扩展或合并；只有权威、生命周期、
    变化原因或依赖方向确实独立时才拆分，不预设“一接口一文件”。

## 3. 主链

```text
Client
  ↓
Admission → durable Run request
  ↓
Background Run Owner
  ↓
TargetConversationManager
  ├─ load ConversationState + ContextSnapshot
  ├─ L0 Deterministic Resolver
  ├─ L1 Intent Encoder
  │    ├─ ACCEPT ───────────────┐
  │    └─ DEFER                 │
  │         ↓                   │
  │    ConversationAgent.plan()│
  ├─ RoutePolicy ◀──────────────┘
  └─ TurnPlanCompiler
       ↓
LangGraph Runtime
  ├─ DIRECT Tool
  ├─ RUN_SKILL
  ├─ DELEGATED Domain Agent
  └─ WORKFLOW Flow
       ↓
Result Board + Verification
       ↓
ResponseAssemblyPolicy
  ├─ TEMPLATE
  ├─ PASS_THROUGH
  └─ ConversationAgent.compose()
       ↓
Final Response Verification
       ↓
Publication → Delivery → durable event/SSE/query
```

对用户始终是一个客服身份；后台 Agent 是专业 Worker。

## 4. Owner

| 问题 | Owner |
|---|---|
| 会话、Workstream、pending signal 生命周期 | ConversationManager + business stores |
| 当前消息的上下文理解 | `ConversationAgent.plan()` |
| 低成本候选与 Fast Path | Intent Encoder |
| 已绑定输入、审批和恢复 | Deterministic Resolver |
| 计划合法性 | RoutePolicy |
| WorkItem/DAG 编译 | TurnPlanCompiler |
| 并行、依赖、暂停和恢复 | LangGraph Runtime |
| 领域内 Tool/Skill 规划 | Domain Agent |
| 工具执行、身份、幂等和 Receipt | Tool Runtime |
| 事实合并、覆盖和冲突 | Result Board + Verification |
| 复杂回复表达 | `ConversationAgent.compose()` |
| 消息提交和送达 | Publication + Delivery |
| 后台任务生命周期 | Background Run Owner |
| 实时订阅与重放 | SSE Adapter |

## 5. Conversation Agent

它是一个逻辑 Agent、两个受限端口，不是拥有隐藏状态和全部权限的超级 Agent。

```python
class ConversationPlanner(Protocol):
    async def plan(self, context: PlanningContext) -> ConversationPlan: ...

class ConversationComposer(Protocol):
    async def compose(self, context: CompositionContext) -> ResponseCandidate: ...
```

### `plan()`

负责指代、多目标、依赖、澄清以及选择直接处理、Tool、Skill、Domain Agent、Flow、人工或
能力越界。输出仅限：

```text
RESPOND_DIRECT | RUN_TOOL | RUN_SKILL | DELEGATE_GOAL
START/CONTINUE/PAUSE/CANCEL_FLOW | CLARIFY | HANDOFF | NO_CAPABILITY
```

它不执行写工具、不授权动作、不提交状态、不绕过 Registry/RoutePolicy。

### `compose()`

只读取当前问题、必要前文、AllowedClaims、WorkItem Outcomes、MissingInput、Receipt 摘要和
风格规则。不再调用业务工具，不修改事实；输出必须再次验证。

## 6. Intent 与规划边界

Intent Encoder 只输出 Top-K、confidence、boundary score、实体候选与 ACCEPT/DEFER。

只有当前消息可独立理解、目标唯一、参数有来源、无多目标/指代/冲突且能力为低风险读取或
固定 Skill 时才 ACCEPT；否则 DEFER。

因此：

```text
Intent Encoder = 便宜的候选和快速路径
Conversation Planner = 完整上下文推理和任务规划
RoutePolicy = 权限与状态合法性
```

三者不共享最终决定权。

## 7. 最短执行路径

| 请求形态 | 路径 |
|---|---|
| Pending input/approval/resume | Deterministic → resume |
| 明确原子读取 | DIRECT Tool |
| 明确稳定复合能力 | RUN_SKILL |
| 简单 FAQ/政策 | Knowledge RAG |
| 开放单领域任务 | 一个 Domain Agent |
| 多个独立领域任务 | `Send` 并行 |
| 有依赖目标 | DAG 波次执行 |
| 高风险/跨轮写入 | WORKFLOW + Approval + Receipt |

Tool 必须注册；Skill 是可选复合能力；Flow 只用于阶段、等待、审批、恢复、幂等或对账。
商品类别和属性属于 Catalog/RAG 数据。

Domain Agent 不重判全局 Domain，也不直接调用其他 Agent。跨领域需求返回 typed
`ExternalCapabilityRequest`，由父级策略决定是否扩展 WorkPlan。

## 8. 上下文

持久状态与 LLM messages 分离。Context Builder 先构造 typed Snapshot，再按目标投影和预算，
最后渲染模型消息。

```text
ContextSnapshot
  ├─ recent public transcript
  ├─ bounded thread summary
  ├─ active workstreams / pending signal / active case
  ├─ provenance-backed entity bindings
  ├─ recent verified outcomes and receipts
  ├─ Publication/Delivery/ACK state
  ├─ required Memory/Media evidence refs
  └─ projection status + source watermark
```

### Planning Context

包含角色边界、当前消息/附件、最近公开对话、摘要、当前事项、业务对象绑定、近期结果与交付
状态、Intent 候选、有限 capability cards 和预算。

### Domain Context

只包含本 WorkItem 的目标、用户限制、相关对象、Facts/refs、相关对话、允许 Tool/Skill 与
输出合同；不接收完整 Transcript 或无关领域状态。

### Composition Context

只包含必要前文、已验证结果、允许 Claims、缺失字段、部分失败、Receipt 摘要和风格政策；
不包含子 Agent 完整工作消息或原始工具载荷。

## 9. 指代与实体绑定

解析优先级：

```text
pending signal
→ unique active workstream/case/current object
→ recent public transcript
→ sourced summary binding
→ one bounded ServiceEpisode lookup
→ Conversation Agent semantic disambiguation
→ CLARIFY
```

```python
@dataclass(frozen=True)
class EntityBinding:
    entity_type: str
    entity_id: str
    source_kind: BindingSourceKind
    source_ref: str
    observed_at: datetime
    valid_until: datetime | None
    authority: BindingAuthority
```

结果代数为 `UNIQUE | AMBIGUOUS | MISSING | STALE | UNAUTHORIZED`。业务 ID 只能来自当前
输入、权威状态或可追溯历史。历史只能证明过去信息；实时状态仍由权威 Tool 刷新。

## 10. Memory 与预算

| 数据 | 用途 | 默认给模型 |
|---|---|---|
| Public Transcript | 实际对话 | 最近窗口 |
| Current State | 当前流程/审批/对象 | 相关结构化子集 |
| Thread Summary | 压缩旧对话 | 一个有界摘要 |
| ServiceEpisode | 跨会话历史 | 按需检索，理解前最多一次 |
| Agent Run/Checkpoint | 恢复和审计 | 结果摘要与引用 |

保存不等于注入。Summary 不替代 Transcript，Checkpoint 不替代业务状态。

每次 plan/domain/compose 前计算：

```text
input budget = model window - output reserve - safety margin
```

超预算依次：外置大载荷、去重/去无关、压缩已完成过程、用 Summary 替换旧消息、缩小目标或
按需读取；仍不足则 typed fail。必须保留当前输入、否定/取消、未完成目标、业务对象、
pending signal、写限制、Approval/Operation/Receipt。局部压缩不回写全局 Summary，并保持
tool call/result 配对。

## 11. LangGraph 与状态

v1 的 WorkPlan Graph 保留：

```text
initialize → Send(worker × N) → merge → more work / interrupt / finish
```

v2 在需要 planning/compose 崩溃恢复后增加薄 TurnGraph：

```text
prepare_context → fast_path/plan → validate_compile
→ work_plan_subgraph → assemble/compose → verify → prepare_publication
```

`graph.py` 只装配 State/Node/Edge；Node 是应用端口的薄适配。阶段迁入图后，Manager 删除
对应图外分支，避免双 Owner。

Checkpoint 保存运行位置和阶段 artifact；Conversation/Flow/Operation/Receipt/Publication
继续由业务 Store 权威保存。conversation_id 与一次可恢复 run/thread ID 不强制相同。

## 12. 子 Agent

- 默认 per-invocation：独立任务不继承完整内部消息；
- 需要内部恢复时使用框架 Agent/Subgraph checkpoint；
- 跨轮领域任务按 `workstream_id` 持久化，不按 Agent 类型共享线程；
- 子图与父图使用不同 State Schema，只回传 AgentResult/Facts/refs/MissingInput/Receipts；
- 外层 Worker 包住旧 ReAct 不会自动获得内部步骤 checkpoint，按领域等价迁移，不长期双轨。

AgentResult 允许简短自然语言说明，但对象、金额、状态、来源和副作用必须结构化可追溯。

## 13. 回复与交付

```text
TEMPLATE             简单状态、审批、Receipt、OOS
PASS_THROUGH         单领域完整且已验证候选
CONVERSATION_COMPOSE 多领域、部分失败、依赖或必要前文
```

多个 MissingInput 先确定性去重和绑定，再形成一个 InteractionRequest；Composer 只优化表达。
Publication 幂等绑定 invocation、bundle、evidence hash 和 delivery operation key。

Agent/RAG/Tool “不能直接发送”只表示不能绕过该边界，并不禁止它们生成候选内容。

## 14. Background Run 与 SSE

四类生命周期必须分离：

| 层 | Owner |
|---|---|
| SSE connection | SSE Adapter |
| Run execution | Background Run Owner |
| Conversation/business | Conversation/Flow stores |
| Model context | Context Builder |

```text
POST message → Admission → run_id → Background Worker
SSE subscribe(run_id, cursor) → durable events → reconnect/replay
```

后台 Run 可选择现有 PostgreSQL Outbox Worker 或 LangGraph Agent Server，但必须先用 ADR 选定
唯一 Owner，不能双轨。

重连规则：

- 订阅原 run_id，不重新提交消息；
- Admission 不确定时用相同 request_id 和相同输入重试；
- 事件具有稳定 cursor，允许传输重放；
- 客户端按 event_id/response_id 幂等展示；
- cursor 过期后用 Transcript/Invocation 查询校准；
- cursor 不代替正式回复 ACK。

## 15. 恢复

| 中断 | 恢复依据 | 行为 |
|---|---|---|
| SSE 断开 | Run + durable events | 后台继续，重连补发 |
| 进程退出 | Admission/Run + checkpoint | Worker 接管 |
| Planning 崩溃 | TurnGraph checkpoint | 恢复规划阶段 |
| Agent 崩溃 | subgraph checkpoint/RunStore | 恢复未完成步骤 |
| 等待输入/审批 | Pending signal + checkpoint | 验证 scope/version 后恢复 |
| 写结果未知 | Operation key + ledger | 对账，不重复提交 |
| Receipt 后、compose 前崩溃 | Receipt + checkpoint | 只恢复回复 |
| Publication 后送达未知 | response/delivery key | 重放同一回复 |
| Summary 失败 | 原始 Transcript | 降级并重建 |

任何可能重新进入的节点，在 interrupt 前不得执行无幂等保护的副作用。

## 16. 安全与验证

事实区分 `VERIFIED_STATE | USER_ASSERTED | MEDIA_OBSERVED | KNOWLEDGE_ASSERTED | DERIVED`。
Requirement 权威来源由 Registry 定义；用户、RAG、Memory、OCR、图片和 Tool 文本均不获得
系统指令权。

写操作必须有注册 Action、权限、注入身份、Approval binding、operation key/fingerprint、
目标版本、typed Receipt 和 reconciliation。安全门禁按 capability fail closed。

最终验证覆盖：Fact/Requirement、Authority、Citation、Receipt、部分失败、Final Claims、
Publication identity 与 Delivery 状态。

## 17. 模块化、复用与反特化

v2 是模块化单体，不把内部职责模拟成微服务。模块边界按四个问题决定：

1. 谁拥有该语义或状态；
2. 是否有独立生命周期或持久化边界；
3. 是否有不同的权限与依赖方向；
4. 是否能独立变化和测试。

处理现有代码时采用：

| 情况 | 做法 |
|---|---|
| 同一 Owner、同一生命周期、总是一起变化 | 合并或保留在同一内聚模块 |
| 已有模块职责清晰，只缺输入或合同 | 扩展现有模块 |
| 两段逻辑有独立权威、生命周期或依赖方向 | 拆分 |
| 只有一个实现且无需替换 | 普通类/函数即可，不强建 Protocol |
| 跨层端口、多实现、外部 Provider 或关键测试替身 | 使用明确 Protocol/ABC |
| 仅转发参数、没有独立语义 | 不新增文件或抽象层 |

当前迁移优先复用：

- 扩展 `target_turn_context.py`，而不是并行建立第二套 Context 系统；
- 将 `structured_target_router.py` 中可复用的结构化校验迁入/演化为 Conversation Agent，
  不复制一套 Planner；
- 保留 `target_conversation_manager.py` 的会话协调职责，但不把 Provider、实体解析、回复生成
  或数据库实现继续堆入其中；
- 保留 `orchestration_runtime.py` 的 WorkPlan 图；只有 TurnGraph 出现独立恢复需求时才增加
  薄上层图；
- Response Assembly 初期可以与 Target Chat 应用层共同演化，出现独立策略、多消费者或显著
  测试边界后再提取；
- 仅当 `api/main.py` 的 Target 装配已成为独立可测生命周期时，才提取 runtime factory。

God File 的问题是混入多个权威和变化原因，不是文件较长；接口碎片化的问题是大量一对一
转发和无意义抽象，不是文件较短。评审同时检查内聚性与耦合度。

禁止句式关键词补丁、类别 Skill、下游补偿、为测试放宽权限、未知写结果盲重试和预建第二套
通用平台。新能力通过 Registry、typed contract 和现有执行代数扩展。验收以属性、状态机、
生成式和 E2E 为主；回归样例只作为见证。

## 18. 核心不变量

1. 同一轮只有一个全局 LLM Planner。
2. Conversation Agent 不保存权威状态、不授权副作用。
3. 明确原子请求不调用 Domain Agent。
4. Domain Agent 不越过 WorkItem envelope。
5. Flow mutation 与业务 side effect 分离。
6. 未知写结果必须对账。
7. Summary/checkpoint 不替代 Transcript、状态或 Receipt。
8. SSE 断开不取消 Run。
9. 子 Agent 工作消息不进入公开 Transcript。
10. compose 只组织 AllowedClaims，生成后再次验证。
11. 无 Ticket Receipt 不宣称转人工；无 Publication commit 不宣称已发送。
12. 所有恢复绑定 tenant/user/conversation/signal/version/run。
13. 生产语义不以句式、旧 Intent、商品类别或测试 ID 为权威。
14. 模块拆并由职责、权限、生命周期和变化耦合决定；既不形成多权威 God File，也不形成
    一对一转发的接口碎片。

## 19. 当前差距

| v1 | v2 |
|---|---|
| Structured Router | Conversation Planner |
| 最近历史未完整进入理解 | Typed Planning Context |
| 有限指代 | Provenance Entity Binding |
| 模板/候选拼接 | 三模式 Assembly + final verification |
| WorkPlan checkpoint | 薄 TurnGraph 阶段恢复 |
| HTTP 请求内执行 | Durable Background Run |
| 无 durable rejoin 主链 | Event cursor + SSE + query/ACK |
| 静态窗口/budget | 每次调用前预算与局部压缩 |
| 旧 ReAct 外层包裹 | 按需框架 Subgraph 等价迁移 |

## 20. 最终定义

DialogPilot v2 是一个 state-first hierarchical manager-worker 客服系统：一个逻辑
Conversation Agent 维持统一语义和表达，Intent 提供可绕过的快速路径，Domain Agent 按需
提供专业规划，LangGraph 负责可恢复执行，业务 Store 持有权威状态，Publication 负责唯一
交付。系统为每个请求选择最短、安全、可验证且可恢复的路径，而不是把所有步骤 Agent 化。
