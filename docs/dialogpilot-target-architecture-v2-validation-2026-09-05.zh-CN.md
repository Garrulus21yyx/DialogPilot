# DialogPilot Target Architecture v2 收敛验证报告

状态：Target v2 运行合同已通过；比较实验未运行
日期：2026-09-05
实现分支：`feat/customer-service-target-architecture`

## 1. 结论

v2 主体已从设计迁移到正式运行主链。系统现在以 ConversationManager 为会话协调者，以
Conversation Agent 为 DEFER 后唯一全局语义 Planner，以 LangGraph 为可恢复执行 Runtime，
以业务 Store、Receipt 和 Publication 为权威持久化边界。

本次收敛不是把所有请求改成 Multi-Agent，也没有把通用电商任务枚举成商品类别 Skill：

- pending signal、唯一续接和 Encoder 高置信低风险请求走最短路径；
- 明确原子动作由 DIRECT Runtime 执行；
- 只有开放的领域目标才进入 Domain Agent；
- 可复用复合能力才使用 Skill；
- 写操作、审批和跨轮阶段才进入 Flow；
- 简单结果采用模板或通过验证的领域候选，复杂结果才调用 compose。

仓库级全量验证还受另一组未提交 RAG 改动影响：`RAGPolicy` 已产生三个新字段，但
`AgentBundle` 白名单尚未同步。该问题不属于 v2 Target 因果面，本报告不以跨 Owner
兼容补丁掩盖它，也不把全仓状态误报为全绿。

## 2. 重复重开后的共同根因

此前讨论反复重开，不是若干互不相关的措辞问题，而是同一组架构边界没有形成可执行合同。

| 表象 | 直接机制 | 共同根因 | 修复 Owner |
|---|---|---|---|
| 主 Agent 与 Intent/Router 看似重复 | 两个 LLM 都理解全局请求 | 全局语义权威重复 | `ConversationAgent.plan()` |
| “它、那个、继续”不能稳定解析 | 理解层未收到完整上下文，实体只看本轮 | 上下文接线与实体来源合同缺失 | Context Snapshot + Binding Resolver |
| 每类商品似乎需要 Skill | 示例被误当作生产分类代数 | Tool/Skill/Flow/Agent 的选择边界未冻结 | WorkItem + Capability Registry |
| 所有任务似乎都要派发 Agent | Orchestrator 被当成意图理解器 | 路径选择与执行调度混淆 | Turn planning + ControlMode |
| SSE 断开后任务生存不明确 | HTTP 请求协程拥有执行生命周期 | 连接与 Run 生命周期权威混淆 | Background Run Coordinator |
| 多 Agent 结果可能不稳定 | reducer 按完成顺序追加 | 执行顺序泄漏到业务投影 | ResultBoard，以 WorkPlan 为序 |

统一因果模型是：语义、执行、持久状态和交付的 Owner 曾只存在于说明中，没有通过输入合同、
typed outcome、持久化边界和跨层测试共同闭合。v2 在 Owner 处修复这些边界，没有为商品类别、
用户句式、旧 Intent 或测试 ID 增加生产分支。

## 3. 已支持的正向合同

### 3.1 理解与规划

```text
ConversationState + ContextSnapshot
  → Deterministic Resolution
  → Intent Encoder ACCEPT/DEFER
  → ConversationAgent.plan()（仅 DEFER）
  → RoutePolicy
  → TurnPlanCompiler
```

- 每个 unresolved turn 最多一次全局 LLM planning；
- pending/approval 等已绑定状态可由 StateBound 直接处理；其他自然语言不会因关键词或唯一
  active Workstream 被机械续接，Encoder Fast Path 也不使用 signal-term 白名单；
- Planner 可拆分多目标、澄清、委派或启动 Flow，但不授权副作用；
- Provider 输出的 `depends_on` 经校验后成为 WorkPlan DAG 依赖，Orchestrator 不重新理解用户；
- 实体必须来自带 scope、时效和版本的 Binding；歧义、缺失、过期和越权均为 typed outcome；
- RoutePolicy 接受或拒绝 Command，Registry 持有能力、风险、权限和工具定义。

### 3.2 执行代数

| ControlMode | 语义 | 执行者 |
|---|---|---|
| `DIRECT` | 已知的原子低风险动作 | governed Tool Runtime |
| `DELEGATED` | 需要领域内自主规划的开放目标 | Domain Agent/Subgraph |
| `WORKFLOW` | 写操作、审批、对账或跨轮阶段 | 受控 Flow Runtime |

Skill 是可选复合能力，不是 Tool 的强制包装层。Domain Agent 在 WorkItem envelope 内可以选择
原子 Tool 或可选 Skill；明确 `skill_hint` 不再进行一次重复的领域 LLM 规划。

### 3.3 状态与恢复

```text
HTTP/SSE connection  != Background Run
LangGraph checkpoint != Business state/Receipt
Thread Summary       != Transcript
```

- Admission 持久化请求并创建 Start Outbox；唯一 Run Coordinator 领取、续租和执行；
- LangGraph checkpoint 保存图进度，业务 Store 保存 Workstream、审批、operation key 和 Receipt；
- PendingInteraction 以 scope/version/signal 绑定恢复；写结果未知进入 Reconciliation；
- SSE 只读取持久公开事件，断线不取消 Run；重连从 cursor 重放，ACK 仍独立存在。

### 3.4 上下文与 Memory

Planning Context 由当前消息、最近公开对话、可重建 Summary、active workstreams、pending
interaction、最近 Receipt、交付状态和按需 Evidence 组成。保存与送模分离：原 Transcript
不被压缩覆盖；长期 episode 按需检索；局部 Agent 工具循环的预算压缩不回写全局 Summary。

Domain Agent 只收到与 WorkItem 相关的视图，返回短说明、结构化 Fact/MissingInput/Receipt 和
可回查引用，不把内部完整 messages 合并进公开会话。

### 3.5 结果与交付

- ResultBoard 按 WorkPlan 顺序投影并行结果，完成时间不改变 Facts、结果或 Publication 输入；
- 独立成功结果在其他任务失败或等待输入时保留；
- compose 只消费 AllowedClaims，生成后的文本再次验证；
- Publication 是唯一提交出口，Delivery 负责幂等发送与不确定状态；
- Handoff 只有获得 Ticket Receipt 后才能声明创建成功。
- Handoff draft 经 commit policy 校验后才调用 Ticket Tool；只投影声明依赖的结果、Fact、
  Evidence、MissingInput 与 Receipt，不复制子 Agent 完整工作消息。

## 4. 模块边界复核

实现保持模块化单体，不以文件数量代替架构质量：

| 内聚模块 | 持有的职责 | 未吸收的职责 |
|---|---|---|
| Conversation Manager | 单轮协调、状态加载、规划与执行接线 | Provider、DB 实现、工具循环 |
| Conversation Agent | `plan` 与按需 `compose` 的逻辑对话身份 | 权威状态、副作用授权、发送 |
| Turn Runtime | planning/execute/compose 阶段 checkpoint | 业务 Receipt 与 Delivery |
| Orchestration Runtime | WorkPlan DAG、并行、interrupt/resume | 重新解释用户意图 |
| Tool/Agent adapters | 框架与既有 governed runtime 的边界转换 | 第二套权限或 Receipt 语义 |
| Run Coordinator | 后台 Run 的领取、租约和终态 | SSE 连接与业务 Flow |
| Conversation Query | Transcript、Invocation、公开事件读取 | 执行任务 |
| Target Runtime Composition | 生产依赖装配与生命周期 | 规划、执行、权限或持久化语义 |

只在 Provider、Store、外部执行边界和测试替身需要替换时使用 Protocol；内部一对一转发没有被
抽成接口。Target 生产装配已从 `api/main.py` 提取到一个内聚 composition root；它只连接既有
Owner，没有演化成服务定位器或第二套运行框架。

## 5. 验证矩阵

| 不变量 | 证明方式 | 代表测试 |
|---|---|---|
| Planner 唯一且可绕过 | 调用计数/级联测试 | `test_conversation_agent.py`、`test_target_understanding.py` |
| Binding 有来源且不跨 scope | 属性与持久化往返 | `test_entity_binding.py`、`test_conversation_state_resolution.py` |
| 并行完成顺序不影响投影 | 全排列属性测试 | `test_target_orchestration_runtime.py` |
| Tool 顺序不影响消费评分，重复仍失败 | 多重集合属性 | `test_target_architecture_eval.py` |
| hard gate 只禁用失败 capability | 全 SafetyInvariant 枚举 | `test_target_architecture_eval.py` |
| 断线不取消 Run，cursor 分页无遗漏 | 单元 + PostgreSQL 重放测试 | `test_conversation_events.py`、`test_postgres_conversation_query.py` |
| Turn 阶段崩溃不重复执行已完成 Tool | 节点故障注入 | `test_turn_runtime.py` |
| lease 接管后旧 attempt 不能提交 | PostgreSQL fencing | `test_postgres_target_run.py` |
| durable HTTP 穿过真实 Run Owner | ASGI + PostgreSQL E2E | `test_target_http_postgres_e2e.py` |
| 上传资产进入真实商品工具链 | ASGI + PostgreSQL E2E | `test_target_product_http_postgres_e2e.py` |
| 长上下文不破坏关键事实 | 预算与工具消息属性 | `test_context_budget.py` |
| Product 开放目标使用 governed 子图 | 等价、越权、隔离测试 | `test_target_framework_agent.py` |

真实 PostgreSQL E2E 已迁移到 durable Admission → Start Outbox → Run Coordinator → TurnGraph →
LangGraph WorkPlan → Publication 链路。旧测试不再通过直接调用同步 runtime 绕过后台执行 Owner。

最终测试记录：

- Target 选择集（含真实 PostgreSQL）：`159 passed in 31.65s`；
- Event/SSE 查询集（含真实 PostgreSQL）：`10 passed in 8.48s`；
- Encoder 封存 heldout：100 cases，13 accepted，13 correct，Accepted Precision `1.0`，Coverage
  `0.13`。它只证明 Encoder Fast Path gate，不代表完整 Planner 或端到端任务质量；
- 仓库级（含真实 PostgreSQL）：`1251 passed, 6 failed in 251.83s`；
- 六项失败全部在 `AgentBundle` 构造时拒绝另一组未提交 RAG Policy 新增的
  `expansion_query_weight`、`query_expansion_count`、`metadata_hint_weight`，没有 Target
  测试失败。

## 6. 当前工程实践对照

核对日期：2026-09-05。这里只说明模式对齐，不宣称 SOTA。

- LangGraph 官方建议大多数一次性子 Agent 使用 per-invocation 子图持久化；它支持同一次调用
  内的 checkpoint、interrupt 和并行隔离。v2 的一次性 Domain Agent 默认遵循该边界，只有
  真正跨轮的任务才绑定 Workstream thread。[LangGraph Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- OpenAI Agents SDK 官方将 manager（agents as tools）与 handoff 区分，并允许 LLM 决策与
  代码编排混合。v2 采用统一客服身份 + 后台专家的 manager 语义，同时把权限、Flow 和恢复
  保留在确定性代码边界。[OpenAI Agents SDK: Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- LangGraph 的 checkpoint 负责图状态，应用仍需自行定义业务持久事实；v2 没有让 checkpoint
  替代 operation key、Receipt 或 Conversation Store。[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- LangGraph interrupt 恢复会重新进入节点，因此副作用必须幂等；v2 把等待节点与写工具分离，
  写操作绑定 operation key 并在 outcome unknown 时对账。[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

这些资料支持“状态优先、manager-worker、框架图执行、应用持有业务权威”的选择；它们不证明
该架构在任意 benchmark 上最优。是否优于单 Agent 必须通过相同模型、工具、数据和预算的实验。

## 7. 非目标与剩余风险

本次没有：

- 一次性迁移所有领域 Agent；
- 为每类商品、Intent 或用户措辞建立 Skill/Flow；
- 把所有应用服务搬成 LangGraph Node；
- 新建第二套 Memory、队列、Tool Runtime 或 ReAct 引擎；
- 以“安全失败就阻断整个系统”替代 capability-scoped fail-closed。

剩余工程风险包括真实模型/provider 的长期延迟分布、生产 SSE 多副本通知机制、业务工具自身的
operation status 查询质量，以及尚未执行的固定预算 A/B/C benchmark。它们是后续运营与实验
工作，不改变当前已闭合的 Target 运行合同，也不能被当前合同测试包装成性能或质量优势。

## 8. 状态声明

Target v2 的运行合同已通过属性、状态、关键节点故障注入、封存 Encoder heldout 和真实
PostgreSQL E2E。fresh-context 源码复核确认：全局语义只有一个 Conversation Planner，旧
Structured Router、关键词式 Bounded fast path 与 encoder signal-term gate 均不在生产路径；
Knowledge/Handoff RouteMode、Goal 依赖、Ticket Receipt 和 Provider typed failure 已贯通。

固定预算 A/B/C 尚未运行，因此状态明确为 `NOT_RUN`，本报告不宣称 v2 优于单 Agent 或达到
SOTA。仓库级全量测试仍有另一组未提交 RAG Policy/Bundle 改动导致的六项失败；该问题由 RAG
Owner 收口，本次没有在 Target 边界加入兼容补丁。
