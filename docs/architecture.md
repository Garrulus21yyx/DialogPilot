# 架构边界与职责归属

DialogPilot 不是一条不断堆 Prompt 的调用链，而是按“谁拥有最终事实”划分边界。下面这张表既是仓库地图，也是面试时解释模块职责的主线。

| 关注点 | Owner（权威模块） | 权威输出 |
|---|---|---|
| HTTP 合同 | `api/main.py` | 经过校验的请求与响应模型 |
| HTTP 身份与权限范围 | `core/auth.py` | 从签名 JWT 的 `sub` 解析出的可信 `Principal` |
| Chroma 部署模式 | `core/chroma_client.py` | 明确选择远程或嵌入式物理存储 |
| 模型分层与推理策略 | `core/model_policy.py` | 按角色校验的模型、推理强度及请求级覆盖配置 |
| 意图识别 | `core/intent_recognizer.py` | 意图、置信度、紧急程度和实体 |
| 任务规划与 Agent 选择 | `agents/agent_orchestrator.py` | 带任务范围、风险、验收条件和 Owner 的 `TaskPlan` |
| 编排合同与预算 | `agents/orchestration_contracts.py` | 任务身份、覆盖投影和共享执行截止时间 |
| 必做任务覆盖 | `services/result_synthesizer.py` | 完成、缺失、失败、重复和意外任务的证据 |
| 并行结果合成 | `services/result_synthesizer.py` | 唯一候选答案、冲突和升级决定 |
| ReAct 执行 | `agents/react_engine.py` | 有界 Worker 循环及闭合的 ReAct 结果 |
| 工具授权与可靠性 | `mcp/tool_manager.py` | Agent 白名单、审批结论、类型化结果和脱敏审计 |
| 业务知识 | `mcp/knowledge_base.py` | 从 ChromaDB 检索出的文档证据 |
| 会话记忆 | `memory/conversation_memory.py` | 有序原始事件、范围摘要与检查点、情景索引和带来源事实 |
| 混合记忆排序 | `memory/hybrid_retrieval.py` | BM25、向量、时效性融合候选及检索指标 |
| 请求 Trace | `core/tracing.py` | Trace/Span 身份及进程内投影 |
| Prompt 上下文 | `memory/context.py` | Token 估算、类型化分区和有界模型输入 |
| 动态规则 | `core/skill_loader.py` | 请求级 Skill Prompt 块 |
| 发布安全 | `services/answer_verifier.py` | `PASS`、`REJECT` 或 `UNKNOWN` |
| 人工升级 | `services/ticket_service.py` | 工单身份、状态、幂等性和事件历史 |
| Bad Case 生命周期 | `services/badcase_registry.py` | 去重观察、证据门禁、复发和审计历史 |
| 在线健康度 | `monitor/performance_monitor.py` | 告警和路由惩罚 |
| 评测数据 | `evaluation/dataset.py` | 带版本、来源、审核状态、校验和及切分完整性的数据 |
| 离线质量 | `evaluation/evaluator.py`、`evaluation/benchmark.py` | 运行时意图/路由报告及确定性分层评分 |

## `/chat` 的时序合同

1. 先读取记忆，再识别当前请求。
2. 意图只识别一次，知识检索和路由复用同一个结果。
3. 只有受支持的业务意图才检索知识库。
4. 从 `TicketService` 读取该用户最多三个未进入 `CLOSED` 终态的事项，作为当前处理状态的权威投影。
5. 构建一个 `TaskPlan`，在共享请求预算内执行职责明确的 Worker。
6. Worker 只能看到白名单工具，最多执行 `REACT_MAX_STEPS`；授权仍由 `ToolManager` 决定。
7. 每个计划任务都必须转换为类型化结果，并检查必做任务覆盖率。
8. 合成唯一候选答案，在发布前校验覆盖、证据、完整性和安全性。
9. 只把校验结论归因给真正产生该候选答案的 Agent 实例。
10. 需要升级时，创建或复用一个幂等、持久化的人工工单。
11. 将校验失败、覆盖失败和工具副作用不确定记录为 provisional Bad Case。
12. 只持久化真正发布给用户的答案，并立即以稳定消息 ID 幂等写入跨会话情景索引。
13. 持久化之后，异步提取有界且带来源的事实操作。
14. 客户端关闭会话时，`finalize` 只补齐未覆盖范围的摘要/checkpoint 和索引 metadata，不删除事件日志。

这个顺序保证记忆系统不会把“模型生成但未通过校验的答案”误认为已经展示给用户。

## 上下文预算合同

`MemoryManager` 拥有持久化记忆状态。批量写入的每轮消息获得会话内连续序号，并追加到 Redis 原始事件日志。Token 压力出现时，只选择最老、尚未覆盖且边界固定的序列范围，最近对话仍保留原文。模型只摘要这个固定范围，产物是携带 `from_seq`、`to_seq`、来源消息 ID 和来源哈希的不可变 Chunk。Redis CAS 只推进摘要检查点；后来到达的新消息位于范围之外，不会使当前摘要失效，只有竞争同一检查点的写入者可能赢得这次状态迁移。

长期情景记忆存储带重叠的原始会话 Chunk，而不是只存摘要。向量和 BM25 候选按用户隔离，并排除正在由 Redis history 承担的当前 `conv_id`；两路可以独立降级，时效性只能重排已经相关的候选。加权 RRF 生成最终排序并保留 `message_id/event_seq/role/chunk_index` 定位。最相关的两个旧会话命中会从 Redis 原始事件日志各展开命中前后两条消息，总窗口上限 1200 estimated tokens；无法展开时保留命中原文。结构化摘要服务于 Prompt 上下文和元数据，不是长期检索的唯一事实来源。

`add_messages()` 是完整轮次进入情景索引的时机 Owner：Redis 追加成功后立即用稳定 `message_id` 和确定性 Chroma ID `upsert` 原始事件。运行时索引失败不回滚已发生的 Redis 事件，压缩和显式 `finalize` 会以同一 ID 幂等补偿；只有归档成功的范围才允许推进摘要 checkpoint。会话结束操作覆盖调用开始时观察到的高水位，如果随后出现更大序号，则返回类型化并发写入结果。长期用户状态由带来源 ID 的类型化事实组成，生命周期闭合为 `active / superseded / retracted`；Prompt 中的用户画像只是活跃事实的投影。

`ContextAssembler` 单独拥有“记忆数据如何转换成 LLM 输入”的职责。记忆、检索知识、画像和 `active_tickets` 放在带标签的数据分区中，真正的用户/助手历史仍然保留消息角色。活动工单 priority 为 90，高于历史画像/情景/摘要；订单、退款和账户的当前事实仍必须以实时业务工具结果为最高权威。装配器预留输出容量、从最老历史开始裁剪，并且不会伪造助手确认消息。

## TaskPlan、预算与并行结果代数

编排器把所选能力转换成 `TaskPlan`。每个必做任务都有稳定 ID、唯一 Owner、明确范围、风险和成功标准。`ExecutionWindow` 为全部 Worker 和合成阶段提供共享截止时间，同时限制单 Agent 超时和最大 Agent 数量。每项任务最终只能进入：

```text
SUCCESS / TIMEOUT / ERROR / BUDGET_EXCEEDED
```

`CoverageGate` 对比计划和结果，拒绝缺失、失败、重复或意外的必做任务证据。只有 `ResultSynthesizer` 能把有序任务结果转换为候选答案。发布校验器在调用模型之前，会先确定性拒绝覆盖不完整的候选。

## 路由质量反馈

Agent 调用成功只说明模型供应商返回了结果，不代表答案质量合格。因此每个 Agent 实例分别维护可用性和答案质量统计。校验器的 `PASS`、`REJECT` 更新带样本置信度的 EWMA，置信度在前 10 个样本逐步建立；`UNKNOWN` 只增加基础设施异常计数，不改变答案质量。只有直接生成候选或成功参与合成的实例会收到归因，冲突或未知合成不会错误惩罚某个 Worker。

反馈只有在同类型存在至少两个存活实例时才能改变选择。统计记录因此显式暴露 `routing_pool_size` 和 `adaptive_routing_active`；单实例池仍收集健康度，但不会声称能把流量路由到不存在的备份实例。

## 失败语义

- 未知意图且置信度低时，系统请求用户澄清。
- 普通模型调用失败可以回退到 General Agent；被拒绝、等待审批、执行失败或超预算的 ReAct 结果不得回退，避免丢失原任务的安全证据。
- 工具超时、熔断或执行失败返回受控降级结果。
- Worker 无法发现或执行白名单外工具。只读调用可以并发，潜在写操作串行执行，并在默认策略下要求宿主审批。
- 校验器失败返回 `UNKNOWN`，绝不等同于 `PASS`。
- `REJECT` 和 `UNKNOWN` 发布确定性人工转接说明，并设置 `escalated=true`。
- 工单持久化失败时不能宣称升级成功，而是要求客户端携带相同 `request_id` 重试。
- 压缩模型失败时，对同一固定来源范围执行有界确定性摘要；竞争的检查点迁移不能同时提交。
- Agent 超时或异常是类型化结果。部分成功可以合成但必须升级；全部失败或不可校验的结果按 fail-closed 处理。
- 请求预算耗尽仍以 `BUDGET_EXCEEDED` 绑定到原计划任务，不能从覆盖证据中静默消失。
- 账户安全任务由 `AccountSecurityAgent` 负责，而不是 Billing Agent。
- `UNKNOWN` 可观测，但不会被当作 Agent 答案质量差。
- 人工转接任务由真实、无工具的 `EscalationAgent` 执行，Task Owner 与实际响应能力一致。
- `CHROMA_MODE=remote` 下服务不可用会阻止启动；只有显式 `embedded` 模式写本地路径，避免双存储分叉。
- 对外 HTTP 投影删除被拒候选正文和原始 Agent 错误，可信内部结果只保留在服务边界内。

## 工单状态代数

`TicketService` 是唯一允许修改工单状态的 Owner。状态集合闭合为 `OPEN`、`IN_PROGRESS`、`WAITING_CUSTOMER`、`RESOLVED` 和 `CLOSED`。`CLOSED` 是终态，`RESOLVED` 可以重新进入 `IN_PROGRESS`。每次合法迁移与工单更新在同一个 SQLite 事务中写入 `ticket_events`，重复提交相同状态是无操作。

`list_active_tickets(user_id)` 只投影尚未进入 `CLOSED` 的最近事项；API 最多把三项作为 `active_tickets` 数据 section 注入所有 Worker 的声明上下文。这个投影不复制或修改状态，历史对话也不能覆盖 TicketService 的当前状态。

幂等指纹绑定稳定的客户端操作，而不是模型每次生成的措辞。因此即使模型输出不确定，重试也能安全复用第一次创建的工单。

## Bad Case 状态代数

`BadCaseRegistry` 拥有另一条独立且闭合的生命周期：

```text
CANDIDATE → TRIAGED → REPRODUCED → FIXING → REGRESSION_PASS → VERIFIED → CLOSED
```

候选观察也可以进入 duplicate、not-a-bug、product-decision 或 privacy-rejected 等终态。进入 `REPRODUCED` 必须提供类型化评测层预期、Owner fixture 和证据哈希；进入 `REGRESSION_PASS` 必须提供修复提交。已关闭问题再次出现时，系统原子增加发生次数并重新打开为 `TRIAGED`。导出的样本永远只是 dev、provisional、consumed regression；运行时和导出器都无权把它声明为 Gold 或 fresh heldout。

## 扩展点

- 新增 Agent：定义专属 Prompt 并注册到 Orchestrator 池。
- 新增 Tool：向 ToolManager 注册类型化 `Tool`。
- 新增业务行为：添加 `skills/<name>/SKILL.md`。
- 更换模型供应商：通过 Anthropic-compatible 配置边界替换。
- 多副本写入：在不改变 `TicketService` 与 `BadCaseRegistry` 合同的前提下，将 SQLite 替换为 PostgreSQL。
