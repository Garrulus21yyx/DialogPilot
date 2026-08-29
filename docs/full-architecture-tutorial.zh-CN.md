# DialogPilot 完整架构教程：从一次请求到可验证交付

> 目标：读完这一页，你应该能独立画出仓库架构，沿一次 `/chat` 请求讲清每个状态变化，解释主要设计取舍、失败路径、测试证据和当前边界，并能回答连续追问。

## 快速导航

- 想先会讲：读第 0、3、5、19、20、22 章；
- 想吃透 Agent：读第 6、9、10、11、12、14 章；
- 想吃透 Context/Memory：读第 8 章；
- 想吃透后端可靠性：读第 7、13、17、18、21 章；
- 面试前速查：读第 22 章连续追问、第 23 章闭卷自测，再用第 24 章做真实性审计。

## 0. 先把项目说准确

DialogPilot 是一个 Python 3.12 + FastAPI 的异步多 Agent 客服后端。它把单提示词聊天机器人中容易混在一起的职责拆开：

- `IntentRecognizer` 判断用户想做什么；
- `KnowledgeBase` 和 `MCPToolManager` 提供受控知识检索；
- `MemoryManager` 拥有工作记忆、滚动摘要、情景记忆和用户画像；
- `ContextAssembler` 把这些数据装进有 Token 上限的模型输入；
- `AgentOrchestrator` 生产 `TaskPlan`，为子任务分配 General、Technical、Billing 或 AccountSecurity Owner；
- `CoverageGate` 判断每个必需任务是否真的得到闭合 outcome；
- `ResultSynthesizer` 合并并行 Agent 的有类型结果；
- `AnswerVerifier` 决定候选回答能否发布；
- `TicketService` 把需要人工确认的结果持久化为可追踪工单；
- `PerformanceMonitor` 与 `EndToEndEvaluator` 分别负责在线健康和离线质量。

必须同时说明两个边界：

1. 这是**由应用代码控制步骤的多 Agent 工作流**。模型负责分类、生成、改写、重排、融合和校验，但没有一个 `while tool_calls` 自主循环。因此，不应把它描述成完整的 ReAct 自主 Agent Runtime。
2. [`MCPToolManager`](../mcp/tool_manager.py) 是项目内部的工具注册、超时、熔断、缓存和降级层；它目前没有实现远程 MCP 的 transport、server、session 或协议握手。名字表达的是工具抽象方向，不等于已经实现网络 MCP Server。

### 30 秒版本

> DialogPilot 是一个 Python/FastAPI 多 Agent 客服后端。请求先读取记忆并融合识别意图，再按需完成 RAG。Orchestrator 将复合问题拆成带 task_id、Owner、风险和完成标准的 TaskPlan；通用、技术、账务与账户安全 Worker 只处理自己的子任务。所有 Worker 共享请求级 deadline 和最大并发预算，每项收敛为 SUCCESS、TIMEOUT、ERROR 或 BUDGET_EXCEEDED。CoverageGate 证明必需任务覆盖后，Synthesizer 才融合候选回答；Verifier 只有明确 PASS 才发布，其余情况幂等落成 SQLite 人工工单。在线监控反馈可用性和经校验质量，离线评测额外衡量 Owner、覆盖、预算与 fan-out。

### 3 分钟版本的顺序

面试或演示时按这个顺序讲，不要朗读技术栈：

1. 问题：单提示词客服把路由、知识、记忆、安全和升级混成黑盒。
2. 合同：一次请求必须得到可诊断的路由结果；只有明确 `PASS` 的回答能发布；需要人工时同步尝试创建持久工单，并把建单成功或失败明确返回。
3. 主链：Memory → Intent → RAG → Context → TaskPlan → Workers → Coverage → Synthesis → Verification → Ticket → Persist published messages。
4. 四个最值得深挖的改动：Token 驱动且并发安全的压缩、TaskPlan/CoverageGate、请求预算下的有类型结果代数、校验质量反馈闭环。
5. 证据：50 个测试，覆盖生命周期配置、RAG 降级、校验 fail-closed、工单状态机/幂等、压缩并发冲突、任务缺失/重复、账户安全 Owner、共享 deadline、预算耗尽、部分成功/冲突和质量 EWMA。
6. 边界：无鉴权，SQLite 只适合单应用写者，画像后台任务不耐进程崩溃，在线统计重启丢失，评测数据还不足以声称生产准确率。

## 1. 如何学习这个仓库

推荐按四遍阅读，而不是从第一行顺序读到最后：

### 第一遍：只记主链

先读 [`api/main.py`](../api/main.py) 的 `lifespan()` 和 `chat()`。你只需要回答：组件何时创建？一次请求按什么时序调用它们？

### 第二遍：按 Owner 读

对每个模块问五个问题：

1. 它拥有哪个权威事实？
2. 输入是什么？
3. 输出是什么？
4. 失败时输出什么有类型结果？
5. 哪些消费者依赖这个合同？

### 第三遍：只读失败路径和测试

从 [`tests/`](../tests) 倒推设计。成功路径只能证明 Demo 能跑；超时、并发写、非法状态、模型坏 JSON、重试和部分成功更能证明你理解系统。

### 第四遍：自己复述

不看本文，手画一张图并讲一次“登录失败后又重复扣款”的请求。能讲到最终 `ticket_events`，才算真正掌握。

## 2. 仓库地图与依赖方向

```text
DialogPilot/
├── api/main.py                      # HTTP 入口、生命周期、主流程编排
├── core/
│   ├── intent_recognizer.py         # 三路意图识别、实体和紧急度
│   ├── skill_loader.py              # 动态业务 Skill 加载/匹配/热更新
│   └── llm_utils.py                 # 供应商响应文本归一化
├── memory/
│   ├── conversation_memory.py       # Redis/Chroma 记忆状态与压缩提交
│   └── context.py                   # Token 估算与 Prompt 输入装配
├── mcp/
│   ├── knowledge_base.py            # Chroma 知识文档摄取与检索
│   └── tool_manager.py              # 工具注册、校验、超时、熔断、缓存、降级
├── agents/agent_orchestrator.py     # Agent 池、路由、并发执行、质量反馈
├── agents/orchestration_contracts.py # TaskPlan、风险、Coverage、执行预算
├── services/
│   ├── result_synthesizer.py        # 并行结果的唯一融合 Owner
│   ├── answer_verifier.py           # 回答发布边界
│   └── ticket_service.py            # 工单身份、状态、幂等、审计
├── monitor/performance_monitor.py   # 在线指标、告警、路由 penalty
├── evaluation/evaluator.py          # 离线意图/对话评测和基线
├── skills/*/SKILL.md                # 运营可热更新的领域规则
├── tests/                            # 关键不变量的回归证据
├── Dockerfile / docker-compose.yml  # 运行镜像与服务拓扑
└── .github/workflows/ci.yml         # compileall + pytest 门禁
```

依赖方向可以简化成：

```mermaid
flowchart LR
    Client --> API[FastAPI API]
    API --> Memory[MemoryManager]
    API --> Context[ContextAssembler]
    API --> Orchestrator[AgentOrchestrator]
    API --> Tool[MCPToolManager]
    API --> Verifier[AnswerVerifier]
    API --> Ticket[TicketService]
    Orchestrator --> Intent[IntentRecognizer]
    Orchestrator --> Plan[TaskPlan + ExecutionBudget]
    Plan --> Agents[Scoped Domain Workers]
    Orchestrator --> Synth[ResultSynthesizer]
    Agents --> Coverage[CoverageGate]
    Coverage --> Synth
    Agents --> Skills[SkillManager]
    Tool --> KB[KnowledgeBase]
    Monitor[PerformanceMonitor] --> Orchestrator
    Monitor --> Tool
    Eval[EndToEndEvaluator] --> Orchestrator
    Eval --> Intent
```

API 层负责**时序编排**，但不应该成为各领域事实的 Owner。例如 API 可以请求创建工单，却不能自己判断某个状态转换是否合法；这个意义属于 `TicketService`。

## 3. 权威事实、生产者和消费者

| 事实/状态 | 唯一 Owner | 主要生产者 | 主要消费者 | 正向合同 |
|---|---|---|---|---|
| HTTP 输入输出 | `api/main.py` 的 Pydantic 模型 | Client/API | 所有内部模块/Client | 非法结构在边界拒绝 |
| 意图、置信度、紧急度、实体 | `IntentRecognizer` | 三路识别器 | RAG gate、Orchestrator、Ticket | 返回闭合 `IntentCategory` |
| Prompt 可裁剪部分预算 | `ContextAssembler` | Memory/RAG/API | Domain Agent | section/history 有界，最近历史优先 |
| 子任务、Owner 与完成标准 | `AgentOrchestrator` 的 `TaskPlan` | Intent、实体、关键词、统计 | Worker、CoverageGate、API | task_id 唯一，每个 required task 恰有一个 Owner |
| 请求执行预算 | `ExecutionBudget/ExecutionWindow` | 启动配置、单调时钟 | Worker、Synthesizer、API | 共享 deadline、per-Agent 上限、max-agents |
| 单任务执行状态 | Worker + Orchestrator 包装 | Provider call/预算 | CoverageGate、Synthesizer、Monitor | `SUCCESS/TIMEOUT/ERROR/BUDGET_EXCEEDED` |
| 必需任务覆盖 | `CoverageGate` | TaskPlan + outcomes | Synthesizer、Verifier、Evaluator | 缺失、失败、重复、越界均不能 complete |
| 并行候选回答 | `ResultSynthesizer` | Agent outcomes | Verifier/API | `SUCCESS/PARTIAL/CONFLICT/FAILED/UNKNOWN` |
| 是否可发布 | `AnswerVerifier` | Candidate + context + plan/coverage/outcomes | API、质量反馈、Ticket | coverage 缺口本地 REJECT；其余只有 `PASS` 可发布 |
| 选定发布文本 | `/chat` 发布边界 | Verifier/API | Memory、Client、Ticket | 只持久化选入 HTTP 响应的文本 |
| 工单身份与状态 | `TicketService` | Chat/manual API | 人工流程/API | 幂等创建、合法迁移、同事务事件 |
| Agent 可用性/质量统计 | 每个 `AgentStats` | 执行结果、Verifier verdict | Router、Monitor | 可用性与回答质量分离 |
| 离线质量报告 | `EndToEndEvaluator` | Cases + Judge + orchestration evidence | 开发者/发布决策 | 同时度量文本质量、Owner、覆盖、预算和 fan-out |

这个表是整个仓库最重要的“所有权地图”。连续追问时，只要回到 Owner，答案通常不会乱。

## 4. 应用启动和关闭

入口是 [`api/main.py`](../api/main.py) 的 `lifespan()`：

1. `_anthropic_cfg()` 读取 API Key、模型和可选兼容 Base URL；Key 缺失时启动失败。
2. 创建 `IntentRecognizer`。
3. 扫描 `skills/`，加载 Markdown/JSON/TXT 业务规则。
4. 创建 `AgentOrchestrator`、`AnswerVerifier`、`TicketService` 和 `ContextAssembler`。
5. 创建 `MemoryManager`：Redis 保存短期状态，ChromaDB 保存情景记忆和画像。
6. 创建 `MCPToolManager` 和 `KnowledgeBase`，注册 `knowledge_search`。
7. 启动 `PerformanceMonitor` 后台采集循环。
8. 创建 `EndToEndEvaluator`。
9. `yield` 后服务开始接请求；退出时停止 Monitor 并关闭 Redis 连接。

为什么使用 FastAPI lifespan，而不是 import 时直接创建？

- 初始化顺序明确；
- 可在服务真正 ready 前完成依赖检查；
- 测试可以替换模块级组件；
- 关闭时能取消后台任务、释放连接。

当前不足：组件以模块级变量保存，适合这个单进程项目，但更大的系统可以改成显式依赖容器，减少测试 monkeypatch 和隐藏全局状态。

这里实际有两个 `IntentRecognizer` 实例：Orchestrator 内部实例服务 `/chat`，lifespan 还单独创建一个给 Evaluator。它们代码合同相同，但 cache/learn 状态不共享；生产版应明确是否合并为同一依赖，或保持评测隔离并给出版本一致性检查。

## 5. `/chat` 主链时序

下面用请求“订单 #A123 登录失败后又被扣款了”走一遍：

```mermaid
sequenceDiagram
    participant C as Client
    participant A as FastAPI /chat
    participant M as MemoryManager
    participant I as IntentRecognizer
    participant T as ToolManager + KB
    participant X as ContextAssembler
    participant O as AgentOrchestrator
    participant G as CoverageGate
    participant S as ResultSynthesizer
    participant V as AnswerVerifier
    participant K as TicketService

    C->>A: message,user_id,conv_id?,request_id?
    A->>M: get_context(user, conv, query)
    M-->>A: recent/history/profile/summary
    A->>I: recognize(message, recent history)
    I-->>A: intent/confidence/urgency/entities
    A->>T: search_with_rewrite (business intent only)
    T-->>A: reranked knowledge or controlled fallback
    A->>X: assemble(sections, history, current message)
    X-->>A: bounded PromptContext
    A->>O: run(Request)
    O->>O: build TaskPlan + ExecutionWindow
    par technical
        O->>O: TechnicalAgent
    and billing
        O->>O: BillingAgent
    end
    O->>G: TaskPlan + ordered typed outcomes
    G-->>O: complete / unresolved task evidence
    O->>S: plan + outcomes + coverage
    S-->>O: candidate + synthesis status
    O-->>A: OrchestratorResult
    A->>V: verify(question,candidate,context,plan,coverage,outcomes)
    V-->>A: PASS / REJECT / UNKNOWN
    alt escalation needed
        A->>K: create idempotent ticket
        K-->>A: new/reused ticket or explicit delivery failure
    end
    A->>M: persist user + actually published response
    A-->>C: ChatResponse + diagnostics
```

### 5.1 请求身份

- `conv_id` 标识一次会话；调用方不传时生成 UUID。
- `request_id` 标识一次客户端操作；不传时生成 UUID。
- 自动人工工单的幂等键是 `chat:{request_id}:handoff`。

如果客户端会重试，应该自己生成并复用 `request_id`。否则每次服务端都生成新 ID，无法识别为同一个操作。

### 5.2 先读记忆，再识别当前意图

`MemoryManager.get_context()` 会顺序读取：

- 当前会话 Redis 消息；
- 基于当前 query 检索的跨会话情景记忆；
- 用户画像；
- 已有滚动摘要。

API 只取最近 5 条真实消息作为意图识别 history。这样“它还是没到”可以借前文判断是物流问题。

### 5.3 意图结果只计算一次

API 先调用 `recognize_intent()`，随后把同一个 `IntentResult` 同时用于：

- 判断是否需要知识检索；
- 构造 Orchestrator Request；
- 路由和优先级；
- 最终响应元数据；
- 工单中的 intent。

避免 RAG gate 和 Agent Router 各自识别一次、得到相互矛盾的结果。

### 5.4 知识是数据，不是假对话

RAG 结果作为 `ContextSection(tag="knowledge", data_only=true)` 进入 system context。真实历史仍保持 `user/assistant` message。系统不会伪造一句 assistant “我已了解背景”，因为伪造历史会改变模型对真实交互的判断。

### 5.5 执行和发布是两个边界

`OrchestratorResult.response` 只是**候选回答**。`AnswerVerifier` 才拥有“能否选入 HTTP 响应”的决定：

- `PASS`：发布候选回答；
- `REJECT`：coverage 缺口、本地空回答或 Judge 明确拒绝，替换为固定人工确认话术；
- `UNKNOWN`：模型失败、坏 JSON 或未知状态，也替换并升级。

### 5.6 先确定发布文本，再写记忆

Memory 保存的是服务端选入 HTTP 响应的 `response_text`，不是未经校验的 candidate。服务端无法证明客户端最终收到了字节，因此这里的 “published” 是响应选择事实，不是用户实际阅读 receipt。即便如此，也不能让下轮模型把已被拒绝的 candidate 当成历史事实。

### 5.8 工单投递失败不是成功

如果 `TicketService.create_ticket()` 失败，API 当前保持 `escalated=true`、`ticket_id=null`，返回明确“工单创建失败，请用相同 request_id 重试”的文本。也就是说，系统拥有“需要升级”和“已持久化 ticket”两个不同事实；当前没有 outbox/可靠队列保证升级最终必达。

### 5.7 用户画像异步更新

`asyncio.create_task(update_profile(...))` 避免阻塞响应。代价是进程在任务完成前崩溃时，本次画像更新可能丢失。生产环境应交给有重试、幂等和可观测状态的队列。

## 6. 意图识别：三路信号如何融合

代码：[`core/intent_recognizer.py`](../core/intent_recognizer.py)

### 6.1 输出合同

`IntentResult` 包含：

- `intent`：闭合枚举，如 `refund`、`technical_login`；
- `intent_group`：细粒度意图映射到 query/billing/technical 等大类；
- `confidence`：融合置信度；
- `urgency`：LOW/MEDIUM/HIGH/CRITICAL；
- `entities`：订单号、日期、金额、错误码；
- `source_scores`：LLM、embedding、pattern 各自分数；
- `reasoning` 和延迟。

### 6.2 三条识别路径

1. LLM：理解复杂语义和最近三轮历史，优先输出细粒度意图。
2. 本地向量：把字符 1/2/3-gram 哈希到 256 维，做余弦相似度。它不是训练过的语义 Embedding，但可作为无远端 embedding 时的确定性近似。
3. Pattern：对退款、发票、401、500、转人工等高精度表达做同步匹配。

LLM 与 embedding 并行，pattern 同步执行。官方 Anthropic SDK 没有 embeddings 资源时，本地字符向量是实际兜底；配置第三方兼容 Base URL 时代码会禁用 embedding 分支，使用 LLM 0.85 + pattern 0.15。

### 6.3 投票和细粒度纠偏

默认权重为 LLM 0.7、embedding 0.2、pattern 0.1。若 LLM 失败，直接选择有效 fallback，不让一个 0 分 `OTHER` 稀释结果。

如果融合结果是泛化类 `billing/technical/query`，但 pattern 高置信命中了 `refund/technical_login/...`，代码会在条件满足时细化结果。这解决“模型说大类，规则知道具体子类”的问题。

### 6.4 缓存和在线学习

- cache key 同时覆盖 message 和 history；同一句话在不同上下文中不会误用同一结果。
- 内存缓存最多约 1000 条，满后删除前 500 条；这是简单近似 LRU，不是严格 LRU。
- `learn()` 能把纠正样本加入进程内模板并清理对应向量缓存，但没有持久化，重启会丢失。

### 6.5 面试中要主动承认

- 字符 n-gram 是轻量相似度，不应声称为生产语义模型；
- 三路权重是工程初始值，尚未通过大规模标注集校准；
- cache 和 learn 都是进程内状态，多副本不共享；
- 更成熟方案应记录混淆矩阵、按类别阈值和 calibration error。

## 7. RAG 与工具可靠性链

代码：[`mcp/tool_manager.py`](../mcp/tool_manager.py)、[`mcp/knowledge_base.py`](../mcp/knowledge_base.py)

### 7.1 为什么先做 Intent Gate

问候、反馈、转人工不检索知识库。业务意图或业务关键词才进入 RAG，减少：

- 无关知识污染；
- 查询改写和重排的模型成本；
- Chroma 查询延迟；
- 简单寒暄被“政策文档”带偏。

### 7.2 检索数据流

```text
原始问题
  -> LLM 改写为最多 3 个不同角度 + 保留原问题
  -> 每个子查询并行调用 knowledge_search
  -> 每路 Chroma Top-K
  -> 按完整 item 哈希去重
  -> LLM 输出相关性索引顺序
  -> 取最终 Top-K
  -> 作为 data_only ContextSection 注入
```

### 7.3 Tool 调用合同

`Tool` 定义名称、说明、handler、JSON Schema、TTL、超时、是否支持重排和 fallback。`call()` 的顺序是：

1. 查注册表；
2. 查 TTL cache；
3. 检查 CircuitBreaker；
4. 按 JSON Schema 的 required 和基础 type 校验参数；
5. `asyncio.wait_for` 执行 handler；同步 handler 放线程池；
6. 可选重排；
7. 写最终结果缓存；
8. 更新统计。

### 7.4 熔断器状态

```mermaid
stateDiagram-v2
    [*] --> CLOSED
    CLOSED --> OPEN: 连续失败达到阈值
    OPEN --> HALF_OPEN: recovery_s 到期后放行探测
    HALF_OPEN --> CLOSED: 探测成功
    HALF_OPEN --> OPEN: 探测失败
```

默认连续 5 次失败打开，60 秒后进入 HALF_OPEN。注意当前 HALF_OPEN 没有并发探测闸门，恢复瞬间多个请求可能同时穿透；生产版应只允许一个 probe。

### 7.5 Fallback 的语义

知识库 fallback 返回一条 `fallback=True, score=0` 的受控说明，而不是抛异常。但 `ToolResult.success=True` 表示“调用方拿到了可消费的降级数据”，不表示知识检索真的成功。消费者如果需要区分真实结果，必须检查 item 的 `fallback` 或设计更明确的 `DEGRADED` 状态。

### 7.6 KnowledgeBase

- collection 名为 `knowledge_base`；
- 首次为空时导入默认客服文档；
- 文档按句号/换行尽量切成约 500 字片段；
- ID 由 title、chunk index、片段前缀哈希生成；
- Chroma 返回 distance，代码用 `1 - distance` 表示近似 score；
- 本地 Chroma 不可服务化连接时退到 PersistentClient。

### 7.7 当前不是完整工具 Agent

模型不会收到一个 tools schema 后自主选择工具。API 明确调用 `search_with_rewrite()`。优点是路径容易控制和测试；缺点是扩展到多工具、多轮观察-行动需要增加执行循环、max loops、中断、审批、tool call/result 配对和输出压缩。

## 8. 记忆系统：状态、压缩和并发安全

代码：[`memory/conversation_memory.py`](../memory/conversation_memory.py)、[`memory/context.py`](../memory/context.py)

### 8.1 四类上下文不要混淆

| 类型 | 存储 | 生命周期 | 用途 |
|---|---|---|---|
| Working memory | Redis list | TTL 24h | 当前会话原始消息 |
| Rolling summary | Redis string | TTL 24h | 被压缩历史的结构化摘要 |
| Episodic memory | Chroma `episodic` | 持久 | 跨会话语义检索的历史摘要 |
| User profile | Chroma `user_profile` | 持久 | 偏好和关键实体 |

Knowledge Base 也在 Chroma，但使用独立 collection。知识文档是业务事实投影，用户记忆是个人交互状态，不能混为一类。

当前画像按 `user_id` 查询并 `limit=1`，没有显式按时间排序，也没有跨 conversation 合并合同，因此不能保证拿到真正最新或完整的用户画像。生产设计应让 profile 有唯一 user key/version，定义新旧偏好冲突与更新顺序。

### 8.2 Redis 消息顺序

写入使用 `LPUSH`，Redis 中最新消息在前。读取时 `_decode_messages()` 对列表 `reversed`，恢复为从旧到新的真实时序。

### 8.3 为什么按 Token，不按消息数

一条消息可以是 2 个字，也可以是 2 万字。固定 20 条压缩无法控制模型输入。`TokenEstimator` 用中文约 1.5 字/token、其他字符约 4 字/token 加元数据开销做快速估算。

它不是精确 tokenizer，所以它拥有的是“预检预算估算”，不是供应商计费事实。配置中仍应留安全余量。

### 8.4 压缩触发和保留

默认：

- memory budget 6000；
- 达到 70% 即触发；
- 最近原文最多保留 5 条、至少 2 条；
- 最近原文额外受约 30% memory budget 约束；
- summary 最多 1200 estimated tokens。

摘要是滚动替换，不是把每次摘要继续无限 append。Schema 固定为：

```json
{
  "user_goal": "",
  "confirmed_facts": [],
  "pending_questions": [],
  "entities": {},
  "decisions": [],
  "user_preferences": []
}
```

LLM 失败时，用旧摘要加最近事实生成确定性 fallback；随后 `_bounded_summary()` 从最长列表开始删除旧项，直到落入 Token 上限。

### 8.5 并发写为何会丢消息

最危险的时间窗口：

```text
T1 读取 Redis 快照
T1 调 LLM 生成摘要（慢）
T2 在这期间 LPUSH 新消息
T1 删除原列表并写回保留消息
```

如果 T1 无条件提交，T2 的新消息会被删除。

修复使用 Redis optimistic transaction：

1. 保存原始 list 和 old summary 快照；
2. 生成摘要；
3. `WATCH` 消息 key 和 summary key；
4. 重新读取并逐值比较；
5. 只在完全未变化时 `MULTI`，同一事务重写列表、TTL 和 summary；
6. 发生 `WatchError` 或值变化就放弃本次压缩，不覆盖任何新状态。

这条正向不变量是：**压缩可以延后，但不能删除压缩快照之后到达的消息。**

### 8.6 压缩成功后的情景记忆

成功提交 Redis 后，再把被压缩部分的 summary 存入 Chroma `episodic`。如果 Chroma 写失败，当前实现只记录 warning；Redis 压缩已经成功，因此跨会话检索会少一段，但当前会话仍有 summary。这是一个明确的部分成功语义。

### 8.7 Prompt Context 和持久记忆是两层

`MemoryManager` 拥有持久状态；`ContextAssembler` 拥有“怎样转换为本次 LLM 输入”。后者：

- 先预留模型输出和固定 system prompt 容量；
- 默认给数据 sections 55% 可用预算；
- 按 priority 高到低选择/截断 section，但最终恢复原 section 顺序；
- history 从最新往前装，空间不足时丢最旧消息；
- history 有剩余预算时返还给被截断 section；
- 当前 user message 永远单独保留。

`ContextSection.render()` 会 HTML escape 内容并标注 `data_only=true`，降低检索内容或旧记忆被模型当指令执行的风险。它是信任边界提示，不是完整 Prompt Injection 防御；真正安全仍需要权限和工具边界。

精确的数据投递关系：

| 来源 | Prompt 位置 | Agent 可见 | Verifier 可见 |
|---|---|---|---|
| recent Redis messages | `messages` 中真实 user/assistant history | 是 | 否 |
| rolling summary | system `conversation_summary` section | 是 | 是 |
| episodic retrieval | system `relevant_history` section | 是 | 是 |
| user profile | system `user_profile` section | 是 | 是 |
| RAG evidence | system `knowledge` section | 是 | 是 |
| Skill | Domain Agent 基础 system prompt | 是 | 否 |
| entities | Agent 调用时额外 system section | 是 | 否 |

预算还有一个必须诚实说明的边界：`ContextAssembler` 只裁剪 sections 和 history，当前 user message 完整保留；Domain Agent 的基础 system prompt、Skill 和 entities 又在 assembler 之后追加。因此 `estimated_tokens` 不是所有 HTTP 输入的硬上限。`ChatRequest.message` 当前也无长度约束，生产版需要 HTTP 长度限制、完整 preflight 计数和 typed `context_too_large` 失败。

## 9. Skill：业务规则怎样与代码解耦

代码：[`core/skill_loader.py`](../core/skill_loader.py)、[`skills/`](../skills)

Skill 适合放：客服话术、所需字段、退款/发票规则、排障 SOP、必须转人工的条件。它不适合拥有账户余额、订单状态或权限决策等动态权威事实。

### 9.1 加载

- 优先发现任意子目录的 `SKILL.md`；
- 也支持普通 `.md/.txt/.json`；
- 单个文件解析失败只进入 errors，不阻断其他 Skill；
- 简单 front matter 不依赖 PyYAML；
- `POST /skills/reload` 可在不重启进程时重扫。

### 9.2 匹配

- `enabled=false` 仍会被解析并出现在 `/skills` 摘要，但匹配时不会注入；
- `agents` 限制 General/Technical/Billing；
- `keywords` 为空表示全局；否则命中任一关键词；
- 单 Skill 和总 prompt 都有字符上限。

### 9.3 注入位置

每个 Domain Agent 在 `_build_system_prompt()` 中按当前 message 和 agent type 选 Skill，再拼进该 Agent 的 system prompt。

### 9.4 当前边界

- 匹配是 substring，不是语义路由；
- 热更新没有版本号、审批、回滚和多副本同步；
- 字符截断可能在一条规则中间切断；
- Skill 内容仍是 Prompt，不是强制 Policy。高风险要求必须在代码/权限/业务服务再次校验。

## 10. Multi-Agent 编排

代码：[`agents/agent_orchestrator.py`](../agents/agent_orchestrator.py)、[`agents/orchestration_contracts.py`](../agents/orchestration_contracts.py)

<div class="dp-task-ledger" role="img" aria-label="DialogPilot 任务账本：规划、并行 Worker、覆盖检查、融合和发布校验">
  <div class="ledger-stage ledger-plan"><span>01 · PLAN</span><strong>3 required tasks</strong><small>task_id · owner · risk · criteria</small></div>
  <div class="ledger-workers"><span>02 · WORKERS</span><strong>Security ╱ Technical ╱ Billing</strong><small>shared deadline · max 3 agents</small></div>
  <div class="ledger-stage ledger-coverage"><span>03 · COVERAGE</span><strong>2 complete · 1 unresolved</strong><small>fail closed, never silently drop</small></div>
  <div class="ledger-stage ledger-verify"><span>04 · VERIFY</span><strong>REJECT_INCOMPLETE</strong><small>safe handoff + ticket</small></div>
</div>

这张 Task Ledger 是代码实际合同的缩影：Agent 列表只说明谁运行，TaskPlan 才说明用户要求中的哪些工作必须完成。

### 10.1 Agent 角色

- `GeneralAgent`：通用接待、澄清、分流；
- `TechnicalAgent`：错误诊断、配置、排障步骤；
- `BillingAgent`：账单、退款、发票、订阅；
- `AccountSecurityAgent`：账号被盗、异常登录、身份验证和敏感资料保护；
- `ESCALATION`：路由占位，不是一个真实 LLM Agent，执行时会落到 General fallback，但上层保持升级语义。

四个真实领域 Agent 共用 `BaseAgent.handle()`：计时、调用模型、累计执行统计、识别回答里的转人工关键词、把异常转成 `AgentResponse(success=False)`。AccountSecurity 有单独 Skill 和高风险完成标准，不再由 Billing 代管。

### 10.2 路由不是只看单一 intent

`_domain_scores()` 仍用可解释的确定性证据选能力：

- 意图大类：例如技术类 +0.75；
- 领域关键词：每个命中增加分数并设上限；
- 实体：错误码给 Technical，金额给 Billing，订单号给 General；账号被盗、陌生设备、身份验证等证据给 AccountSecurity；
- General 初始 0.1，承接普通请求。

得分最高的能力成为 primary，其他非 General 能力达到 0.45 成为 supporting。随后 `_build_task_plan()` 不只返回 Agent 名单，而是为每项生成 `TaskSpec(task_id, owner, instruction, required, risk, success_criteria)`。当前 Planner 是代码控制的可解释启发式，不是另一次 LLM 自主规划；这是刻意保留的生产确定性边界。

例子“账号被盗、登录失败、又重复扣款”会形成 AccountSecurity、Technical、Billing 三项任务。每个 Worker 的 system prompt 注入自己的任务范围，明确禁止替其他领域下结论。

### 10.3 何时先澄清

当 intent 是 `OTHER`、文本不是极短输入且 confidence < 0.5，Orchestrator 直接返回澄清问题，不执行领域 Agent。这里对应 Agent 的 `ASK` 决策，而不是硬猜。

### 10.4 同类实例如何选择

Agent 池允许每类多个实例。`_best_agent()` 取 `routing_score()` 最大者：

```text
latency_score = 1 / (1 + avg_ms / 1000)
base = availability * 0.35 + verified_quality * 0.45 + latency * 0.20
routing_score = base * (1 - monitor_penalty)
```

当前默认每类只有一个实例，所以动态选择能力已有合同和测试，但生产收益需要扩容后才出现。

### 10.5 Specialist 降级

专属 Agent 返回 `success=False` 时，`_execute()` 再调用 GeneralAgent。结果会记录：

- `agent_type`：原本选择的领域；
- `responding_agent_type`：最终实际回答的 General；
- `agent_key`：真实生产者实例。

这样反馈不会错误记到失败的 specialist 上。

### 10.6 请求级预算与并行执行

`ExecutionBudget` 默认配置为请求 20 秒、单 Agent 15 秒、最多 3 个 Agent。一次 `run()` 创建一个基于单调时钟的 `ExecutionWindow`，所有 Worker 和 Synthesizer 共享同一个绝对 deadline；每个 Worker 实际 timeout 是 `min(agent_timeout, remaining_request_time)`。

超过 `max_agents` 的任务不会从计划中消失，而是得到 `BUDGET_EXCEEDED` outcome；请求时间在 Worker 前或执行中耗尽也收敛到该状态。并发仍用 `asyncio.gather`，结果再按 `TaskPlan.ordered_tasks` 恢复稳定顺序。

### 10.7 CoverageGate：完整性不能由回答观感决定

`CoverageGate` 用 task_id 比较计划和 outcomes，输出：

- `completed_task_ids`：明确 SUCCESS；
- `failed_task_ids`：TIMEOUT、ERROR 或 BUDGET_EXCEEDED；
- `missing_task_ids`：计划存在但完全没有 outcome；
- `duplicate_task_ids`：同一 task 重复生产结果；
- `unexpected_task_ids`：计划外结果；
- `unresolved_required_task_ids`：所有未成功的必需任务。

只有 required tasks 全部成功且不存在重复/越界结果，`coverage.complete` 才为 true。这样“技术回答很完整，但账务任务没执行”不能被误标成整体成功。

## 11. 并行结果代数和融合

代码：[`services/result_synthesizer.py`](../services/result_synthesizer.py)

### 11.1 两层闭合状态

每个计划任务：

```text
SUCCESS | TIMEOUT | ERROR | BUDGET_EXCEEDED
```

真正进入 `ResultSynthesizer` 的并行整体融合：

```text
SUCCESS | PARTIAL | CONFLICT | FAILED | UNKNOWN
```

单 Agent 和澄清路径不调用 Synthesizer，API 外层使用 `synthesis_status="single"`。因此“五态”是并行融合器的闭合代数，`single` 是编排层的旁路状态，不应混成第六种融合结果。

闭合状态的价值是：调用方不需要根据空字符串、异常文本或缺失字段猜发生了什么。

### 11.2 状态转换

| Task outcomes / coverage | Synthesis | Candidate | 是否升级 |
|---|---|---|---|
| 全部失败/超时/预算耗尽 | `FAILED` | 固定人工话术 | 是 |
| 只有一个成功，无失败 | `SUCCESS` | 原回答 | 继承 Agent 决定 |
| 只有一个成功，其余失败或 required 未覆盖 | `PARTIAL` | 保留成功回答 | 是 |
| 多个成功且一致 | `SUCCESS` 或有失败时 `PARTIAL` | LLM 去重融合 | 视失败/Agent 标志 |
| 多个成功但冲突 | `CONFLICT` | 融合回答 + conflicts | 是 |
| 融合模型失败/坏 JSON | `UNKNOWN` | 按路由顺序确定性拼接 | 是 |

关键不变量：**有一个有效答案时，不能因为另一个超时就把它降成“所有失败”；但 CoverageGate 仍将缺失的 required task 标为不完整，避免把部分回答伪装成整体成功。**

### 11.3 为什么需要唯一 Synthesizer Owner

如果 API、Orchestrator 和各 Agent 都能拼接回答，会出现：

- 顺序不稳定；
- 重复内容；
- 冲突没人负责；
- 某个失败路径丢掉成功结果；
- 反馈不知道应该归因给谁。

因此只有 `ResultSynthesizer` 能把 outcomes 变成 candidate。

### 11.4 Producer 归因

只有 `SUCCESS`/`PARTIAL` candidate 的成功生产者 Agent keys 会进入 `producer_agent_keys`。`CONFLICT` 和 `UNKNOWN` 不给个体 Agent 记 PASS/REJECT，因为整体结果无法可靠归因。

## 12. 回答校验与发布安全

代码：[`services/answer_verifier.py`](../services/answer_verifier.py)

### 12.1 正向合同

不是“发现坏答案就拒绝”，而是：**只有受支持的明确 `PASS` 才可发布。**

```mermaid
stateDiagram-v2
    [*] --> Candidate
    Candidate --> REJECT: coverage incomplete（本地确定性）
    Candidate --> Judge: coverage complete
    Judge --> PASS: 可解决且无编造高风险事实
    Judge --> REJECT: 错误/危险/无关/虚假执行声明
    Judge --> UNKNOWN: 证据不足/模型失败/解析失败
    PASS --> Published
    REJECT --> SafeHandoff
    UNKNOWN --> SafeHandoff
```

Verifier 现在接收 `task_plan + coverage + agent_outcomes`。如果 `coverage.complete=false` 或仍有 unresolved required task，它在调用 Judge 前直接 `REJECT`，原因码为 `incomplete`；模型不能通过流畅措辞覆盖确定性的任务缺口。

### 12.2 解析失败为何是 UNKNOWN

模型可能返回 Markdown、空串、未知 status 或非法 JSON。如果异常时默认 true，校验边界等于不存在。代码捕获所有异常并生成：

```text
status=UNKNOWN, grounded=false, need_escalation=true
```

机器可读 `reason_code` 进一步区分 `passed/empty_answer/incomplete/ungrounded/unsafe/irrelevant/model_rejected/verifier_unavailable`；API 和工单无需解析自然语言 reason 来决定后续动作。

### 12.3 校验器不是事实引擎

Coverage 缺口和空回答是本地确定性检查；其余语义判断仍由同类 LLM 基于 question、candidate、system context 和编排证据完成。`publishable` 只要求 `status=PASS`，不会额外要求 `grounded=true`，因为问候等回答可能无需外部知识。它能减少明显错误，但不等于确定性事实证明。资金、账户变更等高风险操作最终必须查询业务系统 receipt，不能只靠第二次模型调用。

## 13. 人工工单：从布尔标志到持久状态

代码：[`services/ticket_service.py`](../services/ticket_service.py)

### 13.1 为什么 `escalated=true` 不够

布尔值只表达请求当下的建议，不保证：

- 人工真的收到；
- 重试不会重复创建；
- 当前由谁处理；
- 客户是否补充信息；
- 是否解决/关闭；
- 谁在何时改变了状态。

`TicketService` 把升级变成有身份、有状态、有审计历史的持久对象。

### 13.2 状态机

```mermaid
stateDiagram-v2
    [*] --> OPEN
    OPEN --> IN_PROGRESS
    OPEN --> CLOSED
    IN_PROGRESS --> WAITING_CUSTOMER
    WAITING_CUSTOMER --> IN_PROGRESS
    IN_PROGRESS --> RESOLVED
    WAITING_CUSTOMER --> RESOLVED
    RESOLVED --> IN_PROGRESS
    IN_PROGRESS --> CLOSED
    WAITING_CUSTOMER --> CLOSED
    RESOLVED --> CLOSED
    CLOSED --> [*]
```

- 相同状态重试是幂等 no-op；
- `CLOSED` 是终态；
- `RESOLVED` 可以回到 `IN_PROGRESS`，用于问题复现；
- 不支持的迁移抛 `InvalidTransitionError`，API 映射为 409。

### 13.3 创建幂等

`idempotency_key` 有数据库唯一约束。第一次创建时还生成 request fingerprint，只覆盖稳定的客户端操作字段：

- idempotency key；
- user_id；
- conv_id；
- request_id；
- question。

它故意不覆盖模型生成的 `published_response` 和 reason。重试时模型措辞可能变化，但只要还是同一个用户操作，就必须返回第一次工单，而不是冲突或重复创建。

如果同一个 key 搭配不同稳定字段，抛 `IdempotencyConflictError`。

### 13.4 SQLite 事务

- `BEGIN IMMEDIATE` 先获得写锁；
- ticket insert 和第一条 `OPEN` event 在同一事务；
- transition 的 ticket update 和 event insert 也在同一事务；
- WAL + busy timeout 改善单机并发；
- 进程内 `RLock` 避免同一实例的线程竞争。

正向不变量：**不存在状态已经变化但审计事件没写入的已提交结果。**

### 13.5 优先级

- CRITICAL urgency → critical ticket；
- verifier REJECT/UNKNOWN → high；
- 其他升级 → normal。

### 13.6 生产迁移

SQLite 适合单应用写者和作品集部署。多副本要把同一服务合同迁到 PostgreSQL，并用唯一约束和事务保留幂等/状态不变量；不能简单让多个副本各写自己的本地 db。

还要处理跨模块原子性：当前 ticket 创建、两条 Redis message 和 HTTP response 不在同一事务。ticket 成功后 memory 失败、只写入 user message 等情况没有 request-result ledger。生产版可用 operation record/outbox 把状态显式化，而不是假装跨 SQLite、Redis 和网络存在全局事务。

## 14. 在线质量反馈和路由闭环

代码：[`agents/agent_orchestrator.py`](../agents/agent_orchestrator.py)、[`monitor/performance_monitor.py`](../monitor/performance_monitor.py)

### 14.1 为什么 execution success 不等于 answer quality

供应商返回 200 且生成了文本，只能说明执行可用；回答仍可能胡编。项目把两者分开：

- availability：`success / total`；
- verified quality：Verifier 的 PASS/REJECT；
- verifier infrastructure：UNKNOWN 单独计数。

### 14.2 Sample-aware EWMA

PASS 作为 1，REJECT 作为 0，alpha=0.25：

```text
quality_ewma = 0.25 * observation + 0.75 * old_ewma
```

前 10 个样本还会向 0.5 prior 收缩：

```text
confidence = min(1, samples / 10)
quality_score = 0.5 * (1-confidence) + ewma * confidence
```

这样一个早期 PASS 不会立即把新实例推到满分。UNKNOWN 只增加计数，不改变质量，因为校验基础设施失败不是 Agent 自身错误。

### 14.3 Monitor 闭环

Monitor 每隔 N 秒：

1. 拉取 Agent/Tool 统计；
2. 用滑动窗口 Z-score 检测突变；
3. 按固定阈值生成 Alert；
4. 暴露 Prometheus Gauge/Histogram；
5. 根据成功率和延迟计算 `monitor_penalty`；
6. 回写 Orchestrator；
7. 生成优化建议；
8. 可选异步发 Webhook。

### 14.4 当前观测边界

- 指标和 EWMA 都在进程内，重启归零；
- Histogram 当前每次采集 observe 一个累计平均延迟，而不是每次真实请求延迟，统计语义不够严格；
- 没有 trace_id/span、模型 token/cost、检索候选和工具参数的完整链路；
- label 应保持低基数，不能把 user_id/request_id 直接作为 Prometheus label。

## 15. 离线评测

代码：[`evaluation/evaluator.py`](../evaluation/evaluator.py)

### 15.1 三类评测信号

1. Intent：预测与标注比较，计算 accuracy、每类 precision/recall/F1 和 macro-F1。
2. Dialog：直接调用 Orchestrator，再让 LLM Judge 从 relevance、accuracy、completeness、helpfulness 四维各打 0-1。
3. Orchestration：从真实 `task_plan/coverage/agent_outcomes` 计算 coverage complete、task coverage、budget success rate、fan-out efficiency；case 提供 `expected_agents/expected_task_ids` 时再计算 route exact/Jaccard 和 task exact。

因此类名虽叫 `EndToEndEvaluator`，当前实际覆盖的是 **Intent + Orchestrator candidate + orchestration evidence**，没有经过 `/chat` 的 Memory、RAG、ContextAssembler、AnswerVerifier、Ticket 和最终 response persistence。页面把它称为离线评测管线，而不声称是完整发布面的端到端测试。

总体通过阈值为 0.75。报告保存时间、总数、通过数、均值、退化指标、建议和逐 case metadata。

### 15.2 基线

`EVAL_BASELINE_PATH` 指向 JSON。当前报告和基线比较，任何共同指标相对下降超过 5% 就列为 regression。每次运行又会覆盖 baseline，所以它更像“最近一次运行基线”，不是版本化 benchmark。

### 15.3 Judge 失败的风险

Judge 异常时返回四个 0.5 并标记 `judge_failed=True`。这不会伪装成高分，但会混入平均值。任务覆盖和预算指标是代码计算，不受 Judge 故障影响；严格发布门禁仍应把 judge failure 作为独立 typed outcome，而不是普通 0.5 样本。

### 15.4 什么能说，什么不能说

可以说：“实现了可运行的评测管线和基线回归机制。”

不能说：“系统准确率达到 X%”，除非同时给出：

- 数据集版本、规模和分布；
- 模型、Prompt/Skill 版本；
- 日期与环境；
- Judge 校准方法；
- dev/held-out 划分；
- 重复运行方差。

内置 11 条意图和 5 组对话只适合 smoke/regression，不足以证明生产质量。

## 16. API 面与典型调用

| Method | Path | 作用 | 关键边界 |
|---|---|---|---|
| GET | `/health` | readiness + Agent stats | 不是深度依赖健康检查 |
| POST | `/chat` | 主链路 | 只有 PASS candidate 可发布 |
| GET/POST | `/skills`, `/skills/reload` | Skill 查看/热加载 | 当前无鉴权 |
| POST | `/search` | 改写、并行召回、重排 | query 是 query parameter |
| POST | `/knowledge/add` | JSON 文档导入 | 当前无权限/版本控制 |
| POST | `/knowledge/upload` | txt/md/json 上传 | 10MB，UTF-8 宽松解码 |
| GET | `/knowledge/stats` | chunk 计数 | 只反映数量，不反映质量 |
| GET | `/monitor` | 在线统计/告警/建议 | 进程内状态 |
| GET | `/metrics` | Prometheus scrape | 无业务维度高基数 |
| POST | `/eval/run` | 运行评测 | 会调用模型并写 baseline |
| POST/GET/PATCH | `/tickets...` | 工单 CRUD/迁移 | 当前无鉴权/RBAC |

示例：

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id":"demo-20260828-001",
    "user_id":"demo-user",
    "conv_id":"demo-conversation",
    "message":"订单 #A123 登录失败后又被扣款 50 元"
  }'
```

重点观察响应里的：`intent`、`entities`、`primary_agent`、`supporting_agents`、`routing_reason`、`agent_outcomes`、`synthesis_status`、`producer_agent_keys`、`verification_status`、`escalated` 和 `ticket_id`。

## 17. 配置、部署和运行

### 17.1 本地开发

```bash
cd /path/to/DialogPilot
cp .env.example .env
# 编辑 .env，至少填写 ANTHROPIC_API_KEY
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

源代码模式仍需要 Redis 和 Chroma 可达。最省事的完整启动：

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

### 17.2 Compose 拓扑

```text
Nginx :80
  -> DialogPilot :8000
       -> Redis :6379
       -> ChromaDB :8000 (host 映射 8001)
Prometheus :9090
```

应用容器使用非 root 用户；Chroma ONNX embedding 模型在 build 阶段预下载，避免首次请求临时下载失败；tickets/chroma/eval/skills/logs 映射为持久目录。

### 17.3 关键环境变量

| 变量 | 默认/作用 |
|---|---|
| `ANTHROPIC_API_KEY` | 必填 |
| `ANTHROPIC_MODEL` | 模型名 |
| `ANTHROPIC_BASE_URL` | Anthropic-compatible provider |
| `REDIS_URL` | 工作记忆 |
| `CHROMA_HOST/PORT/PERSIST_DIRECTORY` | Chroma 服务或本地路径 |
| `TICKET_DB_PATH` | SQLite 工单文件 |
| `CONTEXT_INPUT_BUDGET` | 完整输入预算 12000 |
| `CONTEXT_OUTPUT_RESERVE` | 为输出预留 1536 |
| `MEMORY_TOKEN_BUDGET` | 记忆预算 6000 |
| `MEMORY_COMPRESSION_THRESHOLD` | 压缩阈值 0.70 |
| `MEMORY_SUMMARY_MAX_TOKENS` | 摘要估算上限 1200 |
| `AGENT_TIMEOUT_SECONDS` | 每 Agent 15 秒 |
| `AGENT_REQUEST_TIMEOUT_SECONDS` | Worker + synthesis 共享请求预算 20 秒 |
| `AGENT_MAX_PER_REQUEST` | 单请求最多执行 3 个 Agent；其余任务产生预算终态 |

## 18. 测试如何证明设计

CI 在 Python 3.12 上运行：

```bash
python -m compileall -q agents api core evaluation mcp memory monitor services
python -m pytest -q
```

当前 50 个测试按不变量分组：

### Lifespan 与 RAG boundary

[`tests/test_lifespan.py`](../tests/test_lifespan.py)、[`tests/test_knowledge_context.py`](../tests/test_knowledge_context.py)

- memory budget 只进入 `MemoryManager`，不会错误传给 Intent；
- shutdown 关闭 Memory 和 Monitor；
- RAG fallback 不作为业务知识证据注入，也不计 `knowledge_used`；
- 真实检索结果正常进入 Context。

### Answer verification

[`tests/test_answer_verifier.py`](../tests/test_answer_verifier.py)

- 只有 PASS publishable；
- REJECT 升级；
- malformed output 和 provider failure 都 UNKNOWN；
- empty answer 在不调用模型时直接 REJECT；
- unresolved required task 在不调用模型时以 `incomplete` 原因码 REJECT。

### Ticket

[`tests/test_ticket_service.py`](../tests/test_ticket_service.py)、[`tests/test_ticket_api.py`](../tests/test_ticket_api.py)、[`tests/test_chat_handoff.py`](../tests/test_chat_handoff.py)

- 跨 service instance 持久；
- 同一请求幂等复用；
- 模型措辞变化不影响重试；
- 同 key 不同稳定内容冲突；
- 所有合法迁移及 event history；
- 非法迁移、closed reopen、missing ticket typed failure；
- `/chat` 升级只创建一张工单；
- critical urgency 保持 critical priority。

### Context and compression

[`tests/test_context_memory.py`](../tests/test_context_memory.py)

- Token estimator 对重复内容单调；
- Prompt 有界且不伪造对话；
- 触发依据是 Token，不是消息条数；
- 滚动 summary 始终可解析且有界；
- 并发写导致快照变化时拒绝 stale commit；
- 成功压缩替换 summary 并保留最近消息。

### Multi-Agent synthesis

[`tests/test_agent_orchestration.py`](../tests/test_agent_orchestration.py)

- 部分成功保留有效回答；
- 全失败 fail closed；
- model-detected conflicts 传播；
- synthesis unavailable 保持 route order 并 UNKNOWN；
- timeout/exception 转 typed outcomes；
- 缺失和重复 task 不能通过 CoverageGate；
- AccountSecurity 成为账号被盗主 Owner；
- max-agents 和共享 deadline 产生 typed `BUDGET_EXCEEDED`。

### Multi-Agent evaluation

[`tests/test_agent_evaluation.py`](../tests/test_agent_evaluation.py)

- 正确 Owner 集合、任务集合和完整覆盖得到满分编排指标；
- 额外 Agent、覆盖缺口和预算失败分别降低 route、fan-out、coverage 和 budget 指标。

### Quality routing

[`tests/test_quality_routing.py`](../tests/test_quality_routing.py)

- UNKNOWN 可观测但不惩罚质量；
- PASS/REJECT 逐步改变 EWMA；
- 相同类型实例会因质量反馈改变选择；
- 只归因到真实 producer；
- 不支持的反馈状态确定性失败。

## 19. 这次仓库改造具体做了什么

先保持事实边界：这个仓库由用户提供的客服原型整理而来，原型已有功能不能表述成全部从零独立完成。仓库自己的 [`NOTICE.md`](../NOTICE.md) 也保留了来源声明。

### 19.1 初始化和更名 — `cb01521`

- 从用户提供文件中只提取源代码，不导入嵌套 Git 历史、环境、数据库和构建垃圾；
- 全局迁移为 DialogPilot 命名；
- 加入 fail-closed AnswerVerifier；
- 整理 Python-only 文档、Docker、Compose、CI、Skill、监控和评测；
- 创建私有 GitHub 仓库。

### 19.2 持久人工工单 — `715ad82`

根因：`escalated=true` 只是瞬时投影，没有可恢复的人工工作对象。

修复：由 `TicketService` 拥有 ID、幂等 fingerprint、SQLite 状态、合法迁移和同事务 event；`/chat` 用稳定 request_id 创建或复用工单。

### 19.3 Token-aware Context — `548dee1`

根因：消息数不能代表输入大小；摘要 append 会继续膨胀；LLM 压缩期间并发写可能被旧快照覆盖；把 memory 当假消息会污染历史。

修复：Token 触发、结构化有界滚动摘要、保护最近轮次、Redis WATCH/MULTI optimistic commit、typed data sections 和单独的 Prompt assembler。

### 19.4 Typed Multi-Agent Synthesis — `6f8b19f`

根因：直接 gather 文本再拼接无法表达 timeout、partial、conflict 和 all-failed，且没有唯一融合 Owner。

修复：单 Agent `SUCCESS/TIMEOUT/ERROR`、整体五态 synthesis、per-Agent deadline、部分成功保留、冲突传播、确定性 fallback 和 outcomes API 证据。

### 19.5 Quality-aware Routing — `af4c7f3`

根因：API 调用成功并不说明回答正确；只按成功率/延迟路由会奖励稳定生成错误答案的实例。

修复：将 PASS/REJECT 只反馈给真实 producer，用 sample-aware EWMA 区分回答质量；UNKNOWN 单独观测；routing score 联合可用性、质量、延迟和 monitor penalty。

### 19.6 最终收敛 — `cb1e32c`

完整运行测试、静态编译、源/secret 检查、生产镜像构建，并同步 README、架构、pitch 和计划状态。

### 19.7 教程审计触发的合同修复 — `e272fb2`、`24c581c`、`4fec4ab`、`29204ba`

- `e272fb2`：修复 lifespan 把 memory budget 误传给 Intent 的启动错误，并用生命周期测试守住配置 Owner；
- `24c581c`：让一个明确的次领域信号真正达到 supporting contract，用自然复合输入验证 Technical + Billing fan-out；
- `4fec4ab`：让单 Agent 与并行 Agent 共用 typed timeout boundary；
- `29204ba`：RAG fallback 不再被 API 当作真实业务证据或 `knowledge_used`。

这组修复说明文档审阅也可以是验收工具：先让新读者按页面预测行为，再用代码/测试寻找不一致，最后在事实 Owner 处修正，而不是只润色说法。

### 19.8 Task-aware Multi-Agent 收敛 — `2003249`、`6a7cf21`、`5bdb00b`

根因：领域 Agent 列表只能证明“谁运行过”，不能证明用户的每个必需问题都有唯一 Owner 和闭合结果；独立 timeout 也没有约束整次请求，账户安全错误归 Billing，最终 Judge 可能被流畅的部分回答误导。

- `2003249`：新增 TaskSpec/TaskPlan/CoverageReport，把路由、执行和融合迁移到 task_id 合同；CoverageGate 拒绝缺失、失败、重复和计划外结果；
- `6a7cf21`：新增 AccountSecurityAgent/Skill；引入共享 ExecutionBudget，限制 request deadline、Agent timeout 和 max-agents，新增 `BUDGET_EXCEEDED`；
- `5bdb00b`：Verifier 在模型前确定性拒绝 coverage 缺口；API 暴露计划、覆盖、预算和原因码；Evaluator 增加 Owner、task、coverage、budget 和 fan-out 指标。

这次没有引入 LangGraph 或 Swarm，也没有宣称实现 LLM 自主 Planner。当前 TaskPlan 仍由代码中的意图、关键词、实体和阈值生成；收益是任务完整性、预算和发布边界已经可验证，未来可以替换 Planner 而不改变下游合同。

## 20. 三个可完整复盘的 Badcase

### Badcase A：重试创建多张人工工单

- 症状：用户只发一次问题，网络重试后人工台看到重复 ticket。
- 触发：Client 没收到响应，复用 request_id 重试。
- 立即机制：旧实现只有 `escalated=true`，每次请求都可能新建对象。
- 根因：缺少由领域服务拥有的 idempotent operation identity。
- 修复：`chat:{request_id}:handoff` 唯一键 + stable fingerprint + DB unique constraint。
- 验证：同请求返回原 ticket；不同稳定内容复用 key 返回 409；生成文案变化不影响复用。
- 代价：Client 必须稳定复用 request_id；否则系统无法知道两个请求是否同一操作。

### Badcase B：压缩删除并发到达的新消息

- 症状：高并发会话中偶发缺失最近消息。
- 触发：压缩 LLM 正在运行时同会话写入新消息。
- 立即机制：基于旧快照删除并重写 Redis list。
- 根因：压缩没有验证其读到的版本仍是当前状态。
- 修复：WATCH 两个权威 key，比较值，事务提交；冲突则放弃而非覆盖。
- 验证：测试 client 在 summary 调用期间改变 Redis，断言 commit 返回 false 且新消息保留。
- 代价：冲突时本轮不压缩，后续请求重试；正确性优先于立即节省 Token。

### Badcase C：一个 Agent 超时导致所有有效结果丢失或任务静默消失

- 症状：技术 Agent 已回答，但 Billing 超时后整体只返回失败。
- 触发：复合问题并行执行，其中一个依赖慢。
- 立即机制：把 `gather` 的整体结果当成全成或全败。
- 根因：缺少 task identity、整体预算、单任务 outcome 和 coverage 结果代数。
- 修复：共享请求 deadline；每项进入闭合 outcome；超过 max-agents 仍保留 `BUDGET_EXCEEDED`；CoverageGate 标出 unresolved required task。
- 验证：timeout + success 保留成功候选但 coverage=false，Verifier 本地 `REJECT/incomplete` 并转人工；任务不会从 API 证据中消失。
- 代价：用户可能先得到部分建议，同时人工继续处理缺失领域。

## 21. 当前限制和下一步演进

按优先级，而不是“什么都加”：

### 与当前主流 Agent 编排实践对照

以下链接于 **2026-08-29** 核对。这里的“SOTA”指可核验的设计实践，不代表 DialogPilot 在公开 benchmark 上达到最佳成绩：

- [OpenAI Agents SDK：Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/) 区分 manager-as-tools 与 handoff，并明确独立任务可并行、代码编排在速度、成本和确定性上更可控。DialogPilot 属于前者：API/Orchestrator 保留对最终回答的控制权，领域 Worker 不直接接管会话。
- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) 给出 routing、parallelization、orchestrator-workers、evaluator-optimizer 等组合，并强调只有评测证明收益后才增加复杂度。DialogPilot 当前采用确定性 routing + bounded parallel workers，而不是为固定客服域强行引入开放式 LLM Planner。
- [Anthropic：How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) 展示 lead-agent/parallel subagent、委派合同、token budget 和覆盖广度的工程价值，也说明多 Agent 有显著 token 成本。DialogPilot 因此把 request deadline、max-agents、coverage 和 fan-out efficiency 作为一等合同。
- [Google ADK：Workflow agents](https://adk.dev/agents/workflow-agents/) 将 sequential、parallel、loop 作为确定性控制流。DialogPilot 只对互不依赖的领域任务并行；以后出现依赖任务时，应在 TaskPlan 上增加 DAG，而不是让 Worker 自由互聊。

对照后的结论是：当前实现已经补齐“任务身份、唯一 Owner、共享预算、覆盖门禁、发布校验和编排评测”这条生产链；尚未实现动态 LLM Planner、持久化执行图、Worker tool loop、token/金额预算和人工审批节点。因此面试时应说“按主流先进实践完成了有界实现”，不要说“项目就是 SOTA”。

### P0：身份、授权和数据边界

当前 ticket、knowledge、skills、eval 接口无鉴权，CORS 允许全部来源。生产前应加入 Principal、tenant、RBAC/ABAC，所有检索按 tenant/user 过滤，上传和状态变更做 audit。`user_id` 和 `conv_id` 只是外部字符串，不是认证。

### P0：高风险业务 receipt

模型不得声称已退款、改地址或修改账户。真正执行时需要业务服务返回 typed receipt，由业务服务而非模型拥有副作用事实。

### P1：PostgreSQL + durable queue

TicketService 迁移 PostgreSQL 支持多副本；画像更新和其他异步副作用进入可靠队列，增加幂等、重试、dead letter 和 task status。

### P1：完整 Trace

引入 OpenTelemetry 风格 trace/span：API、intent、retrieval、每个 Agent、synthesis、verify、ticket。记录版本、typed outcome、延迟、token/cost；Prompt/文档默认摘要或脱敏。

### P1：版本化评测

建立 ≥100 条分层数据，80/20 dev/held-out；记录 dataset/prompt/skill/model/index 版本；用确定性 grader + LLM Judge + 人工抽检；发布看 pass^k、关键 slice 和 cost/success。

### P2：真正的 Agent Tool Loop（只有需求证明后）

如果要支持多工具自主决策，再加：统一 LLM response/tool call 类型、有限循环、ToolScheduler、审批、取消、调用/结果配对、输出压缩和副作用 receipt。当前客服固定流程不一定需要先引入这层复杂度。

## 22. 项目追问题库与参考答案

本节的提问层次参考个人私有仓库 [agent-systems-atlas](https://github.com/Garrulus21yyx/agent-systems-atlas) 中“项目设计、Multi-Agent、Context、Memory、Tool Runtime、Reliability、Evaluation、Observability、Security”的题型组织；没有修改或复制那个仓库的页面。下面答案只依据 DialogPilot 当前代码。

### Q1：为什么做这个项目？为什么不是一个普通聊天接口？

**先答：** 普通聊天接口把意图、知识、记忆、生成和发布混成一次不可诊断调用。DialogPilot 的目标是让每个关键判断有 Owner 和 typed outcome：能看到为什么路由、哪个 Agent 生产、融合是否部分成功、回答是否通过、人工是否真的建单。

**追问：不用 Agent，用规则工作流不行吗？** 其实主框架就是确定性工作流；模型只放在自然语言模糊的节点。固定状态迁移、幂等、权限和发布规则仍由代码控制。项目没有为了叫 Agent 而把所有决策交给模型。

### Q2：你个人具体负责了什么？

**推荐诚实答案：** 我接手的是一个已有客服原型。我负责仓库清理和 DialogPilot 命名迁移，并重点完成五条 Owner 级改造：持久工单状态机、Token-aware 并发安全压缩、typed Multi-Agent synthesis、Verifier 质量反馈路由，以及 TaskPlan/CoverageGate/ExecutionBudget 收敛；同时把账户安全拆成独立 Owner，并把编排证据接入发布校验和评测。当前有 50 个测试、CI、Docker 验证和架构文档。原型已有的基础意图/RAG/记忆功能我可以讲其实现，但不会把它们说成全部从零原创。

**追问：去掉你的改动还剩什么？** 仍有基础 FastAPI、三路意图、Redis/Chroma 记忆、RAG、领域 Agent、Skill、监控和评测原型；会失去真实工单闭环、Token/并发压缩不变量、有类型并行结果、发布质量反馈和对应验收门禁。

### Q3：讲一次完整请求。

**答题骨架：** 用第 5 章顺序，每一步都说 input → owner → output → failure。最后强调 candidate 与 published response 不同，Memory 只写 published response。

**追问：为何 memory 必须最后写？** 如果先写 candidate，REJECT 后下轮会读到用户从未看到的错误回答，造成状态污染。

### Q4：为什么需要多个 Agent？

**先答：** 只为存在可分离领域且能并行的复合问题，例如登录失败 + 重复扣款。Technical 和 Billing 有不同 Prompt/Skill/升级规则；简单问题仍只走一个 Agent。

**追问：多 Agent 的代价？** 多一次模型调用、上下文断裂、融合延迟、冲突和归因复杂度。因此默认不是所有请求都 fan-out，只有 supporting score 达门槛才并行。

### Q5：主 Agent 和辅助 Agent 怎么选？

**答：** intent、关键词、实体形成 domain scores；最高分为 primary，非 General 的其他领域达到 0.45 成为 supporting。随后不是直接返回 Agent 数组，而是形成 TaskPlan：每项有 task_id、Owner、风险、任务范围和成功标准。路由原因、计划和各分数都返回 API，并有自然复合请求测试。

**追问：阈值怎么来的？** 目前是工程启发式初值，不伪称离线最优。应在标注复合请求集上评 routing precision/recall、端到端成功、成本和延迟，再调阈值。

### Q6：为什么不直接拼接并行回答？

**答：** 拼接没有冲突 Owner，也无法表达任务缺失、预算耗尽和部分成功。当前先把每项收敛为 `SUCCESS/TIMEOUT/ERROR/BUDGET_EXCEEDED`，CoverageGate 对照计划检查完整性，再由 Synthesizer 统一去重、主辅排序、冲突检测和升级。

**追问：Synthesizer 自己失败怎么办？** 输出 `UNKNOWN`，按路由顺序确定性拼接成功项，保留诊断信息并升级；不会把未经验证的融合当 SUCCESS。

### Q7：一个 Agent 超时，为什么不让整个请求失败？

**答：** 因为结果代数支持 PARTIAL。成功内容仍作为候选证据保留，但缺失 required task 会让 coverage=false；Verifier 在模型前以 `incomplete` 原因码拒绝自动发布并转人工，所以“保留证据”不等于“冒充完整答案发布”。

**追问：为何不用 gather(return_exceptions=True) 就够了？** 它只给编程语言异常形态，不定义业务状态、deadline、稳定序列、生产者、candidate 和升级语义。

### Q8：什么是“质量反馈路由”？

**答：** Provider 200 是 availability，Verifier PASS/REJECT 才是 answer quality。两者分别统计，质量用带 prior 收缩的 EWMA，再与延迟和 monitor penalty 合成 routing score。

**追问：UNKNOWN 为什么不记 0？** UNKNOWN 表示校验器不可用或无法判断，不证明 Agent 答错。记 0 会把基础设施故障错误归因给生产者。

### Q9：为什么压缩按 Token？

**答：** 模型限制是 Token，不是消息数；十条长消息可能比一百条短消息大。项目使用快速估算触发，并在 Prompt assembler 再做独立输入预算。

**追问：估算不准怎么办？** 它只是 preflight，配置预留 output/fixed reserve。更严格可接 provider tokenizer 或实际 usage 反馈校准估算误差。

### Q10：摘要为什么必须结构化？

**答：** 自由文本 append 会无限增长，也难判断事实是否丢失。固定字段让目标、事实、待办、实体、决定和偏好可分别裁剪、测试和演进。

**追问：结构化摘要仍会丢信息，怎么测？** 定义必须无损字段和 query set，对压缩前后执行事实问答/实体提取/后续任务成功率比较；加入 held-out 长对话，不只测 JSON 可解析。

### Q11：并发压缩如何保证不丢消息？

**答：** LLM 生成 summary 时不持有长事务；提交前 WATCH message 和 summary 两个 key，重新比较完整快照，只在未变化时 MULTI 重写，否则放弃。

**追问：为什么不加分布式锁？** 锁会跨越慢模型调用，放大延迟和故障恢复。压缩是可重试优化，optimistic conflict-abort 更合适；正确消息写入不应该等摘要。

### Q12：Context 中为什么把知识和记忆标成 data_only？

**答：** 检索文档和历史可能包含指令文本，但它们的角色是数据，不应覆盖 system policy。XML-like tag、description、escape 和 `data_only=true` 给模型明确边界。

**追问：能彻底防 Prompt Injection 吗？** 不能。Prompt 标记是软边界。真正防线是最小权限、工具参数校验、业务服务授权、审批、sandbox、receipt 和审计。

### Q13：RAG 为什么要查询改写和重排？

**答：** 原问题可能只覆盖一种措辞，多查询改善候选覆盖；向量相似不等于对任务最有用，LLM rerank 选择最终上下文。失败时分别退回原 query 或原排序。

**追问：这一定提高效果吗？** 不一定，会增加延迟和成本，也可能 query drift。必须分别测 retrieval Recall@K、MRR/nDCG、evidence hit 和端到端答案质量，做 no-rewrite/no-rerank 消融。

### Q14：工具的可靠性设计有哪些？

**答：** 注册表、基础 schema 校验、TTL cache、per-call timeout、三态 breaker、sync handler 线程池、fallback 和 stats。

**追问：缺什么？** 没有工具审批、权限、执行 receipt、完整 JSON Schema、幂等副作用、输出压缩、调用状态持久化和真正 MCP transport。

### Q15：为什么 Verifier 要 fail closed？

**答：** 发布是安全边界；只有明确 PASS 是受支持的可发布状态。坏 JSON、未知 status 和 provider error 都没有证明安全，所以 UNKNOWN + handoff。

**追问：会不会过度拒绝？** 会，所以要同时测 unsafe publish rate 和 unnecessary escalation rate；高风险 slice 更重视前者，低风险 FAQ 可按证据提高自动通过率。

### Q16：工单幂等 fingerprint 为什么不含回答文本？

**答：** 幂等身份描述客户端操作，不描述非确定性生成结果。同 request 重试时模型措辞变化仍是一次 handoff；把回答放进去会制造重复或冲突。

**追问：同 request_id 但问题变了呢？** stable fingerprint 包含 question/user/conv/request；同 key 不同内容抛 conflict，阻止错误复用。

### Q17：状态更新和 event 为什么同事务？

**答：** 否则会出现 ticket 已 RESOLVED 但 audit 仍停在 IN_PROGRESS，两个事实互相矛盾。同事务保证状态和事件一起提交或一起回滚。

**追问：event 是权威状态吗？** 当前 ticket row 是当前状态权威，events 是不可变审计历史；它不是用 event replay 构建状态的 Event Sourcing。

### Q18：SQLite 能抗多少并发？

**答：** 这里不虚构数字。WAL、busy timeout、BEGIN IMMEDIATE 和进程锁适合单应用写者，但横向副本和高写入应迁 PostgreSQL。容量要用目标硬件、事务大小和读写比压测 P95/P99 与 lock wait。

### Q19：在线监控和离线评测有什么区别？

**答：** Monitor 看运行中的可用性、延迟、熔断、EWMA 和异常趋势；Evaluator 在有 case/期望的受控环境测 intent 和回答质量。Monitor 不能证明答案正确，离线高分也不能证明生产依赖健康。

### Q20：怎么证明一次优化没有伤害正确率？

**答：** 同一版本化数据集做前后对照，保持 model/index/config；报告质量、延迟、token/cost 和关键 slice；dev 调参后只用 held-out 做发布判断，多次运行看方差。Context 优化特别要测压缩前后任务成功和关键事实保留。

### Q21：如果流量增长 10 倍怎么办？

**答：** 先用 trace 分解 LLM、Chroma、Redis、SQLite 和 rerank 延迟及并发，不直接猜瓶颈。然后：API 多副本；Ticket 到 PostgreSQL；在线统计外置；background task 到队列；query rewrite/rerank 做缓存和预算；按 provider limit 做并发闸门、超时和降级；tenant 数据隔离。

### Q22：这个项目最大的安全问题是什么？

**答：** 不是 Prompt，而是没有认证授权：调用者可自报 user_id，ticket/knowledge/skills/eval 都可修改或读取。上线前必须先建立可信 Principal，再做 resource/action 授权和 tenant filter。

### Q23：为什么不用 LangChain/LangGraph？

**答：** 当前固定链路和状态量不大，直接 Python 能清楚展示 Owner、typed outcome 和失败语义，避免框架状态遮蔽。如果未来需要 durable graph、人工中断恢复、多步 tool loop，再用事实比较框架的 checkpoint、interrupt、observability 和迁移成本。

### Q24：项目最强的一个技术点是什么？

**推荐答：** 如果应聘后端，讲 Ticket owner + idempotency + state/event transaction；如果应聘 Agent Infra，讲 typed synthesis 或 Token compression optimistic commit。不要一次说十个亮点，选一个按 symptom → trigger → mechanism → root cause → contract → test 展开。

### Q25：还有哪些地方你不会过度声称？

**答：** 不把 local n-gram 叫生产 embedding；不把内部 ToolManager 叫完整 MCP Server；不把 API-controlled workflow 叫自主 ReAct runtime；不把 16 个内置评测 case 叫生产准确率；不把单实例 in-memory routing feedback 叫分布式学习系统；不把异步 `create_task` 叫可靠队列。

## 23. 最后自测：不看答案能否讲出来

1. 画出 `/chat` sequence，并标出 candidate 和 published response 的分界。
2. 解释为什么 Intent 只计算一次。
3. 写出 Task outcomes 四态、CoverageGate 条件、并行 synthesis 五态，以及为什么单路使用外围 `single`。
4. 解释 PARTIAL 为什么保留回答又必须升级。
5. 写出 ticket 合法迁移和幂等 fingerprint 字段。
6. 画出压缩并发丢消息的时间线以及 WATCH 修复。
7. 解释 summary、recent history、episodic memory、profile、knowledge 的区别。
8. 解释 availability、quality、UNKNOWN 和 monitor penalty 的区别。
9. 指出至少五个生产缺口，并按 P0/P1 排序。
10. 用 30 秒、3 分钟、10 分钟三个版本讲同一个项目，事实范围保持一致。

如果这十题都能在不看页面时答出来，你就不是在背项目名词，而是在按真实架构和证据讲项目。

## 24. 项目真实性自查：7 个维度、21 个追问

这一章参考图片中“细节颗粒度、决策过程、踩坑经历”三个暴露位置，但问题和答案全部重新落到 DialogPilot 当前代码。使用方法不是背诵：先遮住答案回答，再点击展开对照；凡是只能说“教程默认如此”的位置，都应继续回到代码、测试或实验记录补证据。

### 维度一：参数颗粒度

### Q26：知识库的 chunk size 和 overlap 到底是多少？

**答：** [`KnowledgeBase._chunk_text()`](../mcp/knowledge_base.py) 的默认 `chunk_size=500`，单位是 Python 字符数，不是 Token；导入时按句号和换行累积句子，超过 500 才切片。当前实现没有 overlap，也没有 token-aware splitter。

**为什么这样选：** 这是面向小型客服 FAQ 的简单基线，优点是依赖少、行为确定；缺点是长句可能超过预算，跨 chunk 事实可能断裂。仓库没有 256/500/800 与不同 overlap 的消融数据，所以只能说“当前配置是 500、无 overlap”，不能说它已经最优。

### Q27：一次 Prompt 的 Token 预算如何分配？

**答：** 默认完整输入上限 12000，为模型输出预留 1536，再预留 768 固定 system 开销；扣除当前用户消息后，剩余容量默认 55% 给 memory/knowledge sections、45% 给历史。section 按优先级装入，历史从最新消息向前保留；历史没用完的容量会返还给 section。

**追问：为什么不用精确 tokenizer？** 当前 `TokenEstimator` 是 provider-independent preflight：中文字符约按 `2/3 token`、其他字符约按 `1/4 token` 估算。它换取速度和无供应商依赖，但不是账单级精度；生产版应按实际模型 tokenizer 或 usage 反馈校准，并保留安全余量。

### Q28：检索、缓存和超时的具体参数是什么？

**答：** `/chat` 最终使用 Top-3 知识片段；公开 `/search` 默认 Top-5。检索会保留原 query，再尝试生成 3 个改写 query；每路至少召回 5 条，合并去重后重排到最终 Top-K。`knowledge_search` TTL 是 300 秒，通用工具默认 timeout 30 秒，单 Agent deadline 默认 15 秒。

**追问：这些数值怎么证明？** 它们是代码中的工程初值，不是离线寻优结果。面试时应指向配置 Owner 和失败语义；如果声称“最佳”，必须补 Top-K、改写数、TTL、timeout 的质量/延迟/成本曲线。

### 维度二：技术决策过程

### Q29：为什么选择 ChromaDB，而不是 Faiss 或 Milvus？

**答：** 当前原型同时需要知识库、情景记忆和用户画像三个 collection，以及持久化、metadata filter 和本地/服务端两种运行方式。ChromaDB 用一个较轻的接口覆盖这些需要，适合单仓库演示。Faiss 更接近向量索引库，持久化和 metadata 需要自行补；Milvus 更适合独立、规模化向量服务，但部署和运维成本更高。

**事实边界：** 仓库没有做三者 benchmark，因此这是一项基于项目规模和运维复杂度的取舍，不是性能结论。流量、数据量、多租户和 SLA 变化后应重新选型。

### Q30：为什么直接用 Python 编排，而不用 LangChain/LangGraph？

**答：** 当前主链固定，真正需要表达的是 Owner、typed outcome、deadline、幂等和发布边界。直接 Python 能让这些合同在 dataclass/enum/事务中显式可见，也减少框架状态与项目状态的双重语义。

**替代方案何时更好：** 如果出现可恢复的长流程、人工中断后续跑、多步自主 tool loop 或 durable checkpoint，再评估 LangGraph 等框架。选择标准应是恢复语义、可观测性、迁移成本和状态所有权，不是框架热度。

### Q31：为什么复合请求要多路路由，而不是只按最高意图选一个 Agent？

**答：** “登录失败后又重复扣款”同时包含 Technical 与 Billing 两个独立领域事实。只取最高意图会系统性漏掉另一个问题。当前路由先用 intent、关键词、实体计算领域分数，最高分为 primary，其他非 General 领域达到 0.45 才成为 supporting，并保持主辅顺序。

**代价：** fan-out 增加模型调用、延迟、冲突和归因复杂度，所以简单问题仍只走单 Agent；并行结果必须进入统一 Synthesizer，不能直接字符串拼接。

### 维度三：踩坑与根因修复

### Q32：项目里最典型的并发坑是什么？

**答：** 工作记忆压缩。旧快照交给 LLM 生成摘要期间，新消息可能写入 Redis；如果回来后直接删除并重写 list，就会把新消息一起删掉。根因不是“Redis 不可靠”，而是压缩提交没有验证读取版本仍是当前状态。

**修复与证据：** 慢 LLM 调用不持锁；提交前同时 WATCH 消息 key 和摘要 key，比较完整快照，再用 MULTI 重写。发生变化就放弃本轮压缩。`test_concurrent_write_prevents_stale_compression_commit` 证明冲突时新消息保留。

### Q33：一个 Agent 超时为什么没有让整个请求失败？

**答：** 旧式 `gather` 的全成全败语义会丢掉成功回答，也无法证明每个 required task 有结果。现在所有 Worker 共享 20 秒请求 deadline，每项最多 15 秒，并收敛为 `SUCCESS/TIMEOUT/ERROR/BUDGET_EXCEEDED`；融合层保留成功候选，CoverageGate 标出缺口，Verifier 再以 `incomplete` 拒绝自动发布。

**取舍：** 用户能先拿到有价值但不完整的建议，人工继续处理缺失领域。代价是 API 和监控必须携带失败 Agent、融合状态和生产者证据，不能只返回一段文本。

### Q34：RAG fallback 曾经有什么真假证据问题？

**答：** 工具失败时会返回“知识库暂时不可用”的降级信息；如果 API 只检查 `ToolResult.success` 或非空文本，就可能把运维提示当成真实知识，标记 `knowledge_used=true` 并注入 Prompt。

**修复与证据：** fallback 是诊断投影，不是业务证据。API 只接受真实检索结果列表；fallback 不进入知识 section，也不设置 `knowledge_used`。对应两个测试分别守住降级和真实结果边界。

### 维度四：Owner 与数据合同

### Q35：candidate answer 和 published response 为什么必须分开？

**答：** Agent/Synthesizer 只生产 candidate；AnswerVerifier 才拥有发布资格判断。只有明确 `PASS` 的 candidate 被选为 published response，`REJECT/UNKNOWN` 进入人工路径。Memory 最后只持久化用户真正看到的 published response。

**否则会怎样：** 如果先把 candidate 写入 Memory，随后校验拒绝，下轮模型会读到一条用户从未见过的错误“历史”，形成状态污染和幻觉自强化。

### Q36：工单幂等 fingerprint 为什么不包含生成回答？

**答：** 幂等身份描述客户端操作：`idempotency_key/user_id/conv_id/request_id/question`。同一请求重试时 LLM 文案可能变化，但仍应复用首次创建的 ticket；把回答放入 fingerprint 会把一次人工交接拆成多个操作。

**冲突怎么处理：** 同一个幂等键若绑定了不同稳定输入，`TicketService` 抛 `IdempotencyConflictError`。`BEGIN IMMEDIATE`、数据库唯一约束和进程锁共同保证单应用写者下的并发创建一致性。

### Q37：为什么 Agent 调用成功率不能代表回答质量？

**答：** Provider 返回 200 只证明 availability，不证明内容正确。执行成功率、延迟和 Verifier 的 PASS/REJECT 分开统计；质量使用带 0.5 prior 的 EWMA，10 个样本后才达到完整置信度，路由权重为 availability 35%、quality 45%、latency 20%，最后再乘 monitor penalty。

**UNKNOWN 怎么算：** UNKNOWN 通常表示校验基础设施不可用或无法判断，只增加观察计数，不把它当 0 分惩罚生产者。

### 维度五：评测与实验方法

### Q38：当前评测数据到底有多少，能证明什么？

**答：** 内置数据是 11 条意图 case 和 5 组对话，共 16 个 smoke/regression 样本；质量及格线默认 0.75。仓库还有 50 个确定性测试，它们证明状态机、失败边界、任务覆盖和预算不变量，但不等于 50 条业务准确率样本。

**不能声称什么：** 不能据此声称生产准确率、行业 SOTA 或泛化能力。生产发布需要版本化数据集、关键 slice、dev/held-out 分离和人工校准 Judge。

### Q39：如果要验证 chunk size，从哪组实验开始？

**答：** 固定文档集、embedding、query、Top-K 和 reranker，比较 256/500/800 字符及 0/50/100 overlap。检索层报告 Recall@K、MRR/nDCG、evidence hit；端到端报告 grounded answer、拒绝率、延迟、索引体积和 Token 成本。

**为什么不能只看召回率：** 更小 chunk 可能提高局部匹配却丢上下文，更大 overlap 可能提高召回却制造重复和重排负担。最终选择必须同时满足答案质量和资源预算。

### Q40：怎样证明 query rewrite/rerank 确实有价值？

**答：** 做四组消融：原 query；rewrite only；rerank only；rewrite + rerank。保持模型、索引和测试集一致，多次运行记录均值与方差，并单独检查 query drift、空召回和 reranker 故障降级。

**当前事实：** 代码实现了这条链和失败 fallback，但仓库没有完整消融报告。因此可以讲机制、合同和如何评测，不能讲未经记录的提升百分比。

### 维度六：生产边界与扩展

### Q41：这个项目上线前最大的 P0 是什么？

**答：** 认证授权，而不是继续调 Prompt。当前调用者可以自报 `user_id`，ticket、knowledge、skills、eval 接口也没有可信 Principal 和 tenant 边界。上线前必须增加认证、resource/action 授权、tenant filter、上传/状态变更审计和敏感数据策略。

### Q42：流量增加 10 倍，第一步会改哪里？

**答：** 先加 trace 分解 LLM、rewrite、每路检索、rerank、Agent、Verifier、Redis、Chroma 和 SQLite 的 P95/P99，不凭感觉先换组件。确认瓶颈后再做 provider 并发闸门、缓存/预算、API 多副本、在线统计外置和 durable queue。

**已知迁移点：** 多应用写者场景将 TicketService 从 SQLite 迁到 PostgreSQL；画像更新等 `create_task` 副作用进入有幂等、重试、dead-letter 和任务状态的队列。

### Q43：SQLite 能承受多少并发？

**答：** 仓库没有目标硬件上的压测数据，所以不编数字。当前 WAL、`busy_timeout=5000`、`BEGIN IMMEDIATE` 和进程锁的合同是“单应用写者、小规模工单闭环”，不是横向扩容数据库。

**怎么回答容量：** 给出压测维度：事务大小、读写比、并发 writer、P95/P99、lock wait、超时和恢复；超过 SLA 或需要多副本时迁 PostgreSQL。

### 维度七：个人贡献与事实边界

### Q44：如果原型不是从零写的，你具体负责了什么？

**答：** 已有原型包含 FastAPI、基础意图/RAG/记忆、领域 Agent、Skill、监控和评测。实际新增/收敛的是 DialogPilot 命名与仓库整理、发布校验、持久工单状态机、Token-aware 并发安全压缩、typed multi-Agent synthesis、质量反馈路由，以及生命周期、复合路由、单路 timeout、RAG fallback 四类合同修复和对应测试/文档/CI。

**面试原则：** 可以完整讲懂已有模块，但个人贡献要按 commit、代码 Owner 和测试证据划边界，不把“理解并维护”说成“全部从零原创”。

### Q45：这个项目最难的坑应该讲哪一个？

**答：** 按岗位只选一个讲透。后端岗讲 ticket idempotency + state/event 同事务；Agent Infra 岗讲 compression optimistic commit 或 partial synthesis。使用固定结构：症状 → 触发条件 → 立即机制 → 共享根因 → 正向合同 → 测试证据 → 代价。

**不要这样答：** “最大困难是调 API、配环境、看文档”。这些可以是过程问题，但不足以证明你理解状态、并发、失败和数据一致性。

### Q46：现场追问到没有数据的参数怎么办？

**答：** 先说当前值和代码位置，再区分“工程初值”与“实验结论”。例如：chunk 是 500 字符、无 overlap；这是当前可复现配置，但没有消融证明最优。随后给出你会怎样设计实验、看哪些指标、什么条件下改值。

**底线：** 不虚构 benchmark、线上流量、准确率或事故经历。真实感来自可验证的代码细节、取舍、失败语义和复现实验，而不是把没有发生过的经历讲得更像真的。

### Q47：这算 SOTA Multi-Agent 吗？

**答：** 不能直接声称行业 SOTA。它采用了当前生产系统常见的 manager/worker、结构化任务、共享预算、覆盖检查和 fail-closed 发布边界，但 Planner 仍是可解释的代码启发式，Worker 也没有自主 tool loop。更准确的表述是“按生产 SOTA 思路收敛了任务、预算、覆盖和评测合同”。

**追问：为什么不直接做 LLM Planner？** 客服领域能力少、风险明确，代码 Planner 更便宜、可复现、容易测试。等标注数据证明启发式无法覆盖长尾，再让 LLM 输出同一个 TaskPlan schema，并保留风险规则和 CoverageGate 作为确定性边界。

### Q48：TaskPlan 和普通 RoutingDecision 有什么本质区别？

**答：** RoutingDecision 只回答“调用谁”；TaskPlan 还回答“用户的哪项工作由谁负责、是否必需、风险多高、完成标准是什么”。下游用 task_id 对齐 outcome，所以能识别 missing、duplicate、unexpected，而不是从 Agent 数量猜完整性。

**追问：多个任务能否给同一个 Agent？** 合同允许每个任务有唯一 Owner，不要求 Owner 只能拥有一个任务。当前小型 Planner 按领域合并成一项；未来若同领域任务存在依赖，可保留多个 task_id，并由 DAG 调度决定串并行。

### Q49：请求级预算怎样避免 timeout 叠加？

**答：** `ExecutionWindow` 在 run 开始时用 monotonic clock 固定绝对 deadline。每个 Worker 获得 `min(15s, remaining)`，Synthesizer 也只能消费剩余时间；最多执行 3 个 Agent。超限任务仍产生 `BUDGET_EXCEEDED`，所以预算控制不会破坏覆盖证据。

**追问：为什么只用 wall-clock 还不够？** 当前只实现时间和 Agent 数量，尚缺真实 token、模型调用次数和金额预算。生产版应从 provider usage 聚合 token/cost，并把 reservation/refund 语义加入同一 ExecutionBudget。

### Q50：怎样证明 Multi-Agent 比单 Agent 值得？

**答：** 在同一 held-out 复合问题集比较 single、static fan-out、task-aware fan-out。报告 required-task coverage、route exact/Jaccard、unsafe publish、unnecessary escalation、fan-out efficiency、budget success、延迟、token/cost；不能只比较 LLM Judge 的语言分。

**追问：什么时候应该退回单 Agent？** 子任务强依赖同一上下文、fan-out efficiency 持续低、成本增幅大于覆盖收益，或单 Agent 在关键 slice 已达到同等终态成功率时。Multi-Agent 是受评测证明的策略，不是项目必须保留的身份标签。
