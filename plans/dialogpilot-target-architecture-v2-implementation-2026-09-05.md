# DialogPilot Target Architecture v2 实施计划

状态：待实施  
日期：2026-09-05  
目标架构：`docs/dialogpilot-target-architecture-v2.zh-CN.md`  
实现基线：`512d1c0`

## 1. 目标与约束

本计划把 v1 的受控执行主链升级为具有统一 Conversation Agent、完整上下文、按需回复合成、
后台 Run 与断线恢复的通用电商客服。

硬约束：

- 修复权威 Owner 和通用合同，不按句式、Intent、商品类别或 badcase 写补丁；
- 简单请求走最短路径，不默认调用主 Agent 或 Multi-Agent；
- 采用模块化单体：按职责、权限、生命周期和变化耦合决定拆分或合并，不预设小接口；
- 一个状态、事实、转换和 Run 生命周期只有一个 Owner；
- 每阶段独立测试、commit、push；未满足退出条件不得标记完成。

每次修复必须先写清：

```text
症状 → 触发 → 直接机制 → 共享根因 → Owner → 正向合同 → 受影响消费者
```

## 2. 保留、替换与非目标

| 处理 | 组件 |
|---|---|
| 保留 | ConversationManager、DeterministicResolver、Encoder、RoutePolicy、TurnPlanCompiler |
| 保留 | WorkPlan、LangGraph OrchestrationRuntime、ResultBoard、ToolManager |
| 保留 | Conversation/Flow/Operation stores、Receipt、Summary、Publication/Delivery |
| 替换 | StructuredTargetCommandRouter → `ConversationPlanner` |
| 收敛 | `_board_response()` → Assembly Policy 的 deterministic fallback |
| 扩展 | WorkPlan Graph → 可恢复的薄 TurnGraph |
| 扩展 | 同步请求执行 → 唯一 Background Run Owner + durable rejoin |

非目标：不重建 Memory，不增加第二套调度器，不把原子 Tool 包成 Skill，不把写 Flow 改成
自由 ReAct，不为商品类别创建专属能力，不一次性替换全部领域 Agent。

## 3. 模块演化规则

不预先创建一套 `conversation/` 微型接口目录。每个阶段先检查现有 Owner，再决定扩展、合并
或拆分：

| 判断 | 处理 |
|---|---|
| 现有模块已拥有语义，只缺合同或输入 | 原地扩展 |
| 两段逻辑共享状态、不变量且总是一起变化 | 合并/保持内聚 |
| 权威、生命周期、权限或依赖方向不同 | 拆分 |
| 只有一个实现且无替换需求 | 普通类或函数 |
| 跨层、多实现、Provider/Store 或关键测试边界 | Protocol/ABC |
| 新层只做一对一转发 | 不创建 |

当前代码的预期演化：

| 现有模块 | 优先处理 |
|---|---|
| `target_turn_context.py` | 扩展为 typed ContextSnapshot Owner；复杂 Binding 独立后再拆 |
| `structured_target_router.py` | 复用输出校验和编译知识，演化/迁移为 Conversation planning，不并存复制 |
| `target_conversation_manager.py` | 保持会话协调；不吸收 Provider、Binding、Composition 或 DB 细节 |
| `target_chat_application.py` | 保持协议与 Publication facade；Assembly 先就近演化，独立变化后再提取 |
| `orchestration_runtime.py` | 保留 WorkPlan Graph；TurnGraph 仅在阶段恢复需求成立后增加 |
| `api/main.py` | 先复用当前 composition root；装配形成独立生命周期后再提取 factory |

God File 以多个权威/变化原因判定；过度拆分以无语义的一对一转发、循环依赖和同时修改大量
碎片文件判定。目标是高内聚、低耦合，不是文件数量最少或最多。

逻辑合同 `ConversationPlanner`、`ConversationComposer`、`ContextSnapshotLoader` 和
`BackgroundRunService` 仍需清楚，但不要求它们各占一个文件或都使用 Protocol。

## 4. 阶段总览

| 阶段 | 交付 | 依赖 |
|---|---|---|
| M0 | 基线与 v2 合同测试 | 无 |
| M1 | Typed ContextSnapshot 接入理解层 | M0 |
| M2 | Conversation Planner 替换 Structured Router | M1 |
| M3 | Provenance-backed Entity Binding | M1-M2 |
| M4 | Response Assembly、compose 与最终验证 | M2 |
| M5 | 薄 TurnGraph 与阶段恢复 | M2-M4 |
| M6 | 唯一 Background Run Owner | M5 |
| M7 | Durable Event/SSE rejoin | M6 |
| M8 | 调用前 Context Budget 与局部压缩 | M1-M4 |
| M9 | 按领域迁移框架 Agent/Subgraph | M5/M8 |
| M10 | 属性、故障注入、heldout 与真实 E2E | 全部 |

## 5. M0：冻结基线

工作：

- 固定 Target 专项与真实 PostgreSQL 六场景 E2E；
- 记录当前 planner/agent/tool 调用、P95、Token 和 Registry fingerprint；
- 建立 v2 contract test scaffold；
- 将无关的未提交 RAG 工作排除在本计划提交之外。

退出条件：基线可重放，环境失败与产品失败分离，生产语义未修改。

提交：`test(target): freeze v2 migration baseline`

## 6. M1：统一 ContextSnapshot

根因：最近消息和摘要已经加载，却没有进入全局理解端口。

正向合同：Context Loader 一次返回 typed Snapshot；Understanding 接收 PlanningContext，
Domain Agent 从同一 Snapshot 做任务投影，不另读一套历史。

修改：

- 将 recent turns、summary、active workstreams/cases、pending signal、最近 Receipt、
  Publication/Delivery 状态拆成 typed 字段；
- 保存 source ref、watermark 与 READY/LAGGING/DEGRADED/UNAVAILABLE；
- 修改 Understanding 调用签名；
- Encoder 只读取有界确定性特征；
- 迁移期让现有 Router 消费新 Context，但不增加新语义分支。

主要代码（优先复用，是否新增合同文件由实现时耦合审查决定）：

```text
application/target_conversation_manager.py
infrastructure/target_turn_context.py
application/target_understanding.py
```

验证：Snapshot 来源单调；投影失败不清空权威 state；跨 WorkItem 投影隔离；无当前
invocation 越界；全仓无旧 Understanding 调用签名。

提交：`feat(target): connect typed conversation context to understanding`

## 7. M2：Conversation Planner

根因：新增主 Agent 又保留 Structured Router 会产生双重 LLM 理解和双 Goal 权威。

正向合同：DEFER 后只有 `ConversationPlanner.plan()`；Plan Validator 校验结构，RoutePolicy
仍决定合法性，Registry 仍补全 Tool/Risk/Requirement。

修改：

- 定义 PlanningContext、ConversationPlan 和 typed provider outcomes；
- 迁移现有 Goal、Clarify/OOS、实体来源和 Schema 校验，不复制 Router 类；
- 由 Registry 投影有限 capability cards；
- 形成 Deterministic → Encoder → Planner 级联；
- Encoder ACCEPT 时跳过 Planner；
- 移除 `/chat` 对 StructuredTargetCommandRouter 的运行时注册。

主要代码（演化而非并行复制）：

```text
application/structured_target_router.py
application/target_understanding.py
infrastructure/target_conversation_provider.py
api/main.py（仅调整装配；是否提取 factory 由装配内聚性决定）
```

验证：每个 unresolved turn 至多一次 global planning；ACCEPT 时为零；未知 capability、无来源
entity、非法 DAG 均 typed fail；capability 排列不改变合法性。

提交：`feat(target): replace structured router with conversation planning`

## 8. M3：通用实体绑定与指代

根因：参数主要来自本轮文本，缺少统一的来源、候选、时效和歧义代数。

正向合同：Binding Resolver 从当前输入、pending signal、workstream/case、recent transcript、
summary、episode 产生候选；只自动消费唯一、有效、同 scope 的绑定。

状态：`UNIQUE | AMBIGUOUS | MISSING | STALE | UNAUTHORIZED`。

修改：

- 定义 EntityBinding 与各来源 Adapter；
- Planner 只能选择候选或澄清，不能生成业务 ID；
- TurnPlan 保存 consumed binding refs，执行前重验 scope/version；
- 去掉“本轮没 ID 就立即追问”的过早分支。

验证属性：

- 添加无关历史不改变唯一绑定；
- 添加同等候选必然变为 AMBIGUOUS；
- 来源过期后不可消费；
- tenant/user/conversation 永不混合；
- 每个输出 entity 都能追溯 source ref。

示例对话只做见证，生产代码不得出现对应句式判断。

提交：`feat(target): add provenance-backed entity binding`

## 9. M4：回复组织与最终验证

根因：当前多结果按 Owner 拼接；无条件 LLM 重写又增加成本和事实漂移。

正向合同：`ResponseAssemblyPolicy` 只根据结果结构选择：

```text
TEMPLATE             简单状态、Receipt、审批、OOS
PASS_THROUGH         单一且已验证的完整候选
CONVERSATION_COMPOSE 多结果、部分失败、依赖或必要前文
```

修改：

- Result Board 生成 AllowedClaims；
- Composer 只读取 AllowedClaims、Outcomes、MissingInput、Receipt 摘要与必要前文；
- 多 MissingInput 统一形成一个绑定式 InteractionRequest；
- compose 后验证实体、数值、状态、Receipt、否定和不确定性；
- compose 超时回退 deterministic response；
- Publication 只接收 verified candidate。

验证属性：结果排列不改变事实集合；新增独立失败不删除成功结果；compose 不产生额外业务
claim；Publication 重放不重新 compose。

提交：`feat(target): add governed conversation response assembly`

## 10. M5：薄 TurnGraph

根因：WorkPlan 执行可恢复，但 planning/compose 仍在图外，无法精确恢复阶段。

正向合同：TurnGraph 只协调阶段；现有 WorkPlan Graph 作为子图复用；复杂逻辑继续由各端口
Owner 实现。

```text
prepare_context
→ fast_path / plan
→ validate_compile
→ work_plan_subgraph
→ assemble / compose
→ verify_candidate
→ prepare_publication
```

实现规则：

- Graph 模块保持执行编排内聚，不吸收 Context、业务状态或 Publication 权威；
- 简单 Node 可就近实现；只有可独立复用/变化的阶段才提取服务；
- 阶段 artifact 带 version/fingerprint；
- 已有 Receipt/Result 时恢复不得重跑 Tool；
- 阶段迁入图后删除 Manager 中的重复执行分支；
- Publication 数据库提交仍走幂等应用边界。

验证：在每个节点边界故障注入；Receipt 后崩溃不重复 Tool；compose 后崩溃复用候选；
stale/cross-scope resume fail closed；图内外无双 Owner。

提交：`feat(target): checkpoint the complete turn lifecycle`

## 11. M6：后台 Run Owner

先写 ADR，在以下方案中只选一个：

- 复用 PostgreSQL Admission/Outbox，增加薄 Worker；
- 使用 LangGraph Agent Server background run/rejoin。

选择依据：代码复用、lease/cancel、stream replay、部署复杂度、依赖和可测性，不以框架名气
决定。未选方案不得作为并行生产路径。

正向合同：

```text
ACCEPTED → QUEUED → RUNNING
RUNNING → WAITING_INPUT | WAITING_APPROVAL | RECONCILING
RUNNING → COMPLETED | RETRYABLE_FAILURE | TERMINAL_FAILURE | CANCELLED
WAITING_* → QUEUED（合法 signal）
```

修改：Admission 原子保存 Run/Outbox；HTTP 返回稳定 run_id；Worker 使用 lease attempt fencing；
进程重启领取未完成 Run；HTTP/SSE 协程取消不改变 Run。

验证：双 Worker 竞争、lease 接管、旧 attempt ACK 拒绝、幂等 Admission、cancel/complete 竞争、
进程崩溃恢复和写副作用至多一次。

提交：`feat(target): execute admitted turns as durable background runs`

## 12. M7：Durable Event 与 SSE rejoin

正向合同：Event Store 提供稳定单调 cursor；SSE 只订阅与重放；Transcript、Invocation、ACK
继续校准正式状态。

修改：

- 定义版本化公开事件，只含安全进度与正式 Publication；
- 支持 `after_event_id`/`Last-Event-ID` 和范围鉴权；
- disconnect 只结束订阅；
- cursor 过期后 typed fallback 到查询 API；
- 客户端按 event_id/response_id 幂等显示；
- ACK 不由 cursor 推导。

验证属性：任意断连点的最终事件集合与连续订阅一致（允许重复、不漏正式事件）；cursor 不跨
scope；重连不重新生成 Publication、不重新提交业务任务。

提交：`feat(target): add resumable conversation event streaming`

## 13. M8：Context Budget 与局部压缩

根因：持久 Thread Summary 不能处理一次模型调用中的巨大 Tool 输出或工作消息。

正向合同：每次 plan/domain/compose 前由 `ContextBudgetManager` 计算输入预算并生成
ContextBuildReport；全局 Summary 仍由原 Projector 唯一维护。

裁剪顺序：

```text
外置大载荷 → 去重/去无关 → 压缩已完成过程
→ 用 Thread Summary 替换旧消息 → 缩小目标/按需读取
→ typed CONTEXT_BUDGET_EXCEEDED
```

必须保留：当前输入、否定/取消、未完成目标、业务对象、pending signal、write constraint、
approval/operation/receipt。框架 middleware 只压缩局部工具循环，不回写全局 Summary。

验证属性：预算不超界；关键项不丢；Tool call/result 配对；原始 Transcript 不被修改；相同
输入和策略版本产生相同报告。

提交：`feat(target): enforce model-call context budgets`

## 14. M9：领域 Agent 框架适配

目标不是“全部换框架”，而是消除长期双 ReAct 运行时，并为确实需要的工具循环提供细粒度
checkpoint。

步骤：

1. 选一个只读、工具调用丰富的领域做等价试点；
2. 用 governed Tool wrapper 保留 allowlist、身份注入、Schema、Receipt；
3. `AgentContextView → framework messages → AgentResult` 只做薄适配；
4. 跨轮子图 thread 绑定 workstream_id；
5. 子图只返回 Result/Facts/refs，不合并完整 messages；
6. 一个领域切换后删除该领域旧生产消费者。

验证：可见 Tool 集、参数、异常、Evidence、AgentResult 等价；越权在发现与执行两处拒绝；
子图不跨 Workstream；DIRECT 和写 Flow 不被 Agent 化。

每个领域独立提交，例如：

`refactor(target): run product domain through governed agent subgraph`

## 15. M10：收敛验证

验证层次：

- 属性：Binding provenance、Context budget、Plan 闭包、merge 排列不变、幂等；
- 状态机：Run、Flow、Approval、Reconciliation、Delivery；
- 生成式：多候选、多目标、无关上下文、长输出、注入和跨 scope；
- 故障注入：每个图节点、lease、Receipt、Publication、SSE 断连；
- E2E：真实 ASGI/PostgreSQL、断线/重启/恢复、多领域部分失败；
- heldout：不得为新失败增加生产 case 分支；
- fresh-context review：代码、合同、Trace、文档一致。

固定相同模型、工具、数据、Registry 和预算比较：

```text
A. 单 Agent
B. Conversation Agent + Domain Agents
C. B + Encoder Fast Path
```

指标：完成率、最终业务状态、工具/参数、指代、多轮 pass^k、未授权、重复副作用、P95、Token、
不必要主 Agent 调用率。

能力级 hard gate：跨租户、未授权 Tool、重复写、stale resume、重复 signal、错误权威覆盖、
unsupported final claim、重连产生新 Publication 或新业务执行。

提交：`test(target): close v2 conversation runtime invariants`

## 16. 提交与退出清单

每个阶段提交前必须满足：

- [ ] 正向合同和 Owner 已落到代码；
- [ ] 所有生产者、消费者和异常路径已迁移；
- [ ] 没有句式/Intent/类别/test-id 特化分支；
- [ ] 拆并符合职责、权限、生命周期和变化耦合；无 God File 或一对一转发碎片；
- [ ] 属性或状态机测试通过；
- [ ] 代表性集成/E2E 通过；
- [ ] Trace 能区分 provider、projection、contract、business 与 environment failure；
- [ ] 文档状态与实现一致；
- [ ] 仅 stage 本阶段文件；
- [ ] commit 后 push 当前分支。

## 17. 总体完成标准

1. DEFER 后只有一个 Conversation Planner，不再有独立 Structured LLM Router；
2. 最近对话、摘要、状态、Receipt 和交付状态进入有界 Planning Context；
3. 所有跨轮实体有 provenance，歧义 typed 化；
4. 简单请求不额外调用主 Agent，真正复杂任务才委派领域 Agent；
5. compose 按需调用且最终文本再验证；
6. planning、执行、compose 均能从明确 checkpoint/Receipt 恢复；
7. HTTP/SSE 断开不取消 Run，重连不重复回复或业务动作；
8. 持久 Summary 与局部压缩没有双写；
9. 子 Agent 上下文隔离，跨轮状态绑定 Workstream；
10. 没有过度防御、特化补丁、God File 或接口碎片化；
11. 属性、状态机、生成式、heldout 与真实 PostgreSQL E2E 共同通过。

## 18. 首个代码切片

只实施 M1：

```text
typed ContextSnapshot
→ Manager 接线
→ Understanding 协议迁移
→ Router 仅做迁移期 Context 消费
→ 来源、隔离和降级属性测试
```

它修复“上下文已读取但未进入全局理解边界”这一共享根因，不引入新 Agent、Graph、Run 服务
或任何句式/业务类别补丁。
