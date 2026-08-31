# 项目讲述与技术取舍

这页回答两个面试问题：**“你的项目到底解决什么问题？”**和**“为什么这样设计？”**。完整调用链和源码定位请看[完整架构教程](./)。

## 30 秒版本

DialogPilot 是一个使用 Python 和 FastAPI 实现的多 Agent 客服后端。我不是把所有能力塞进一个 Prompt，而是先用 LLM、本地字符 n-gram 和规则融合识别意图，再把请求转换成带任务范围、风险和验收条件的 `TaskPlan`，由 General、Technical、Billing、Account Security 等 Worker 在共享截止时间内执行。

每个 Worker 内部使用有界 ReAct，只能调用 ToolManager 暴露的白名单工具。ToolManager 而不是模型负责风险分级、写操作审批、超时、审计和结果脱敏。并行结果必须先通过 `CoverageGate`，证明所有必做任务都有闭合结果，再由 `ResultSynthesizer` 处理顺序、冲突和部分成功。最终只有 `AnswerVerifier` 明确判为 `PASS` 的候选可以发布；失败则确定性转入带幂等键和状态历史的 SQLite 人工工单。

记忆层以 Redis 原始事件日志为事实来源，按 Token 压力压缩固定序列范围，同时把带重叠的原始会话 Chunk 写入 ChromaDB。长期召回融合 BM25、向量和时效性排名，结构化事实保留来源及替代/撤销状态。这样既能控制上下文长度，又不会因为只搜索摘要而丢失订单号、错误码等精确信息。

HTTP 边界从签名 JWT 中取得用户身份，不信任请求体提交的 `user_id`。请求级 `TraceId` 串联 HTTP、ReAct 和工具调用。校验失败、任务覆盖失败、工具副作用不确定以及可信用户反馈会进入独立的 Bad Case 状态机；复现和修复后的样本只作为 provisional dev regression，不冒充人工 Gold 或全新 heldout。

## 主链路怎么讲

```text
请求与可信身份
  → 读取记忆
  → 多信号意图识别
  → 按业务意图检索知识
  → 构建 TaskPlan
  → 有界并行 Worker / ReAct / Tool
  → CoverageGate
  → ResultSynthesizer
  → AnswerVerifier
  → 发布答案或创建人工工单
  → 持久化真实发布结果
  → 异步抽取事实与记录 Bad Case
```

## 关键技术取舍

### 为什么融合三路意图信号？

LLM 擅长处理自然语言歧义；本地字符 n-gram 是确定性语义降级路径；规则对订单号、错误码、退款词和金额等结构化特征更稳定。三路并发控制延迟，加权投票保留各来源分数，出现误路由时能够定位到底是哪一路判断出了问题。

代价是系统比单模型分类复杂，因此合同明确规定只分类一次，知识选择和 Agent 路由必须复用同一个 `IntentResult`，避免两个识别实例或两次调用产生分叉结论。

### 为什么只对业务意图检索知识？

知识库能提升业务回答的事实性，但把检索结果塞进问候、闲聊或人工转接请求只会污染上下文、增加延迟和重排成本。因此由意图结果控制检索门，而不是每条请求都无条件执行 RAG。

### 为什么是 TaskPlan，而不是简单选择几个 Agent？

General、Technical、Billing 和 Account Security 不只是不同 Prompt，它们对应不同业务责任与风险边界。Orchestrator 生成的不是 Agent 名单，而是 `TaskPlan`：每个必做任务都有稳定 ID、唯一 Owner、范围、风险和完成标准。

Worker 共享一个 `ExecutionWindow`，最终结果只能是：

```text
SUCCESS / TIMEOUT / ERROR / BUDGET_EXCEEDED
```

`CoverageGate` 负责完整性，`ResultSynthesizer` 负责去重、冲突、输出顺序和部分成功证据。这样并发不会把“有 Agent 返回”误当成“用户问题已经完整解决”。

### 为什么外层确定性规划、内层有界 ReAct？

外层 `TaskPlan` 固定必做工作、风险、截止时间和覆盖要求，使客服流程可复现；ReAct 只负责一个任务 Owner 内部的工具选择。这样保留了 Worker 根据观察结果继续行动的能力，又防止自由委派抹掉任务身份或绕过工具授权。

### 为什么按 Token 而不是消息条数压缩？

消息数量无法准确代表模型输入长度。DialogPilot 估算 Prompt Token，保留最近原始轮次，只把最老且尚未覆盖的固定序列范围压缩成不可变 Chunk。Redis 乐观事务只推进该范围的检查点；后来到达的消息序号更大，不会让正在生成的摘要失效。

压缩也不是唯一归档触发器。会话结束 API 会用确定性消息 ID 幂等写入情景记忆并推进检查点，但不删除原始事件日志。归档失败时检查点保持不变。用户画像只是活跃类型化事实的投影，每条事实仍保留来源消息和 superseded/retracted 生命周期。

### 为什么长期记忆使用混合召回？

摘要适合放进有界 Prompt，却可能丢掉订单号和错误码。系统因此检索原始情景 Chunk，分别得到 BM25 与向量候选，再用加权 RRF 融合并加入较小的时效性信号。每条结果保留来源排名，效果可以用 `Recall@K`、`MRR` 和 `nDCG` 测量，而不是只凭一段流畅回答判断。

### 为什么工具权限不能交给模型？

模型只提出“想调用什么工具”，无权决定“是否允许调用”。ToolManager 根据 Agent 白名单、工具风险、宿主审批和执行状态机作决定；只读工具可以并发，潜在写操作串行并默认要求审批。超时、取消和未知副作用以类型化结果返回，审计记录只保存脱敏参数和结果摘要。

这能回答用户提示注入场景：即使用户要求忽略规则或伪造审批，模型也拿不到白名单外工具，写操作也无法绕过宿主授权。

### 为什么发布校验必须 fail-closed？

`AnswerVerifier` 拥有候选答案能否发布的最终决定权。如果解析失败或校验模型异常时默认放行，就等于绕过安全边界。因此结论集合闭合为 `PASS / REJECT / UNKNOWN`，只有 `PASS` 发布，其余状态进入安全人工转接。

校验结论也用于在线路由反馈，但不混淆模型可用性和答案质量。`PASS`、`REJECT` 更新真正候选生产者的样本感知 EWMA；`UNKNOWN` 只记录校验基础设施异常，不降低 Agent 质量。

### 为什么工单服务拥有独立状态机？

API 和 Orchestrator 可以申请人工升级，但只有 `TicketService` 负责工单身份、幂等、持久化、合法状态迁移和事件历史。这样 Controller 不能随意发明状态，相同 `request_id` 的重试也不会创建重复工单。

当前使用 SQLite 是为了让个人项目易部署；服务合同保持窄边界，多副本写入时可以迁移 PostgreSQL，而不用重写 Agent 主链。

### 为什么 Bad Case 不等于 Ticket 或 Trace？

Ticket 负责用户人工处理流程，Trace 负责一次请求的诊断；二者都不拥有“工程缺陷是否复现、修复、验证或复发”的事实。`BadCaseRegistry` 因而单独持久化脱敏观察、合并重复症状，并要求 Owner、expected、fixture 和证据哈希齐全后才能进入 `REPRODUCED`，提交修复后才能进入回归验证，关闭后复发则自动重新打开。

## 面试时应该诚实说明的指标边界

调用 `/eval/run` 得到结果时，必须同时说明数据集规模、模型、日期和运行配置。仓库目前拥有 500 条 provisional 分层评测、25 篇检索文档、公开数据适配器及确定性分层指标，但还没有获得独立人工仲裁的 Gold 数据。

因此可以说：

> 我建立了覆盖意图、路由、RAG 和 Stateful 合同的版本化回归体系，并用 Macro-F1、Owner Exact Match、Recall@K、MRR、nDCG 及 Owner fixture 验证不同层级。

不能说：

> 项目已达到生产准确率，或 500 条数据全部属于人工 Gold。

内置 11+5 条用例只是 smoke test，也不能代替代表真实业务分布的 heldout 评测。
