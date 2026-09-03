# DialogPilot Target Architecture v1 核心实施报告

日期：2026-09-03

## 当前结论

Target Architecture v1 已成为 `/chat` 的唯一编排主链，并完成真实 HTTP 与
PostgreSQL 边界验证。当前状态是“有界 v1 主链已切换”，不是“所有规划能力均已开放”。

已实现：

- 版本化 Agent / Skill / Flow / Action / Requirement / Tool Registry；
- `DIRECT`、`DELEGATED`、`WORKFLOW` 三种 WorkItem 控制模式；
- Conversation-owned Workstream、PendingInteraction、Approval、Resume 和 CAS；
- 确定性状态解析、精度优先 Encoder Fast Path、Registry-backed RoutePolicy；
- LangGraph 1.2.11 静态父图、动态并行派发和依赖波次；
- AgentResult、Fact、EvidenceRequest、Receipt 和 Result Board；
- 写操作 operation key、审批绑定、目标版本、Receipt 和 reconciliation；
- Ticket Receipt 驱动的人工作业权转移和唯一 Publication command；
- Memory 理解补证一次上限，以及 Media reuse/OCR/VLM 选择；
- capability-scoped fail-closed safety gate。
- PostgreSQL event-backed ConversationState CAS 与 Operation Ledger；
- LangGraph PostgreSQL async checkpointer（只保存执行位置）；
- Target Admission、Publication 与 `/chat` 唯一组合入口；
- Ticket Receipt 驱动的人工接管闭环。

有意保持关闭或仍待后续实现：

- `execute_refund` 已支持资格预检、持久确认、拒绝/过期取消、确认后幂等提交，
  并已闭合 `OUTCOME_UNKNOWN → RECONCILING → 权威状态查询 → Receipt`；
- 真正的 Encoder + Structured LLM fallback；当前是有界确定性理解器；
- Product Media/Catalog 的生产适配器；E2E 使用受控工具替身验证编排边界；
- 旧 command-primary、旧 AgentOrchestrator 和旧合同的物理删除。它们不再是
  `/chat` 的运行时回退权威，但仓库中的其他测试/接口仍引用旧模块。

## 六条垂直场景

| 场景 | 执行形态 | 核心断言 |
|---|---|---|
| 订单状态查询 | DIRECT | 只执行一次原子读取，不启动领域 Agent |
| 商品识别 | 单领域 DELEGATED | 只启动 Product Agent，并使用注册 Skill 能力包络 |
| 退款申请 | PREPARE + APPROVAL + CONTINUE_WORKFLOW | 资格通过后发布绑定式确认；确认后复用原 operation/version，写工具至多调用一次 |
| 退款查询 + 商品问题 | MULTI_DOMAIN | 两个无依赖 Worker 同波并发 |
| Product 失败、Refund 成功 | 部分失败 | Refund 结果保留，Product 保持 typed failure |
| 用户要求人工 | WORKFLOW + Publication | 只有 Ticket Receipt 能转移会话 Owner 并宣称创建成功 |

## 第 12B 阶段验证结果

- Target v1 专项测试：76 passed。
- 真实边界：1 个测试连续覆盖六场景，使用真实 ASGI `/chat`、PostgreSQL
  Admission/Event/State/Operation/Publication 表和 async LangGraph checkpoint。
- 订单路径断言为 `DIRECT`，没有派发领域 Agent；单领域任务只运行一个 Worker；
  只有退款状态与商品识别两个独立任务使用 `MULTI_DOMAIN`。
- 人工接管只有在 `support_ticket_create` 返回 committed Receipt 后才把会话 Owner
  转成 HUMAN，并把 Workstream 收敛为 `COMPLETED/COMPLETE`。
- 退款链断言确认前、拒绝和过期均不写；确认后只写一次；相同请求重放、
  篡改确认值、stale signal 和跨会话 signal 均按绑定规则处理。
- 写调用返回未知结果时，Conversation Owner 显式持久化
  `RECONCILING/RECONCILE`；客户端使用新 request identity 和原 approval binding
  发起查询，Ledger 先调用只读 `refund_status`，并由业务 Owner 按原 operation key
  精确对账；同订单的历史退款不能冒充本次结果，也不会再次提交退款。
- Ledger 使用稳定的业务 `operation_fingerprint` 绑定副作用语义；新的轮次快照、
  WorkItem ID 和执行预算不会制造冲突，但参数、目标版本或授权包络变化会
  fail closed。真实边界测试证明未知结果场景的写工具只调用一次。
- 仓库级回归：1128 passed、6 failed。6 个失败均由工作树中另一路未提交的
  RAG 策略改动触发：默认 retrieval policy 已产生 `expansion_query_weight`、
  `query_expansion_count`、`metadata_hint_weight`，但旧 `AgentBundle` 白名单尚未
  接受这些字段；失败不经过 Target v1 新执行路径，本阶段未代替该工作修改或提交。

## 安全门禁语义

门禁按 capability 生效。`execute_refund` 的重复副作用断言失败时，只禁用
`execute_refund`；`order_status` 和 `product_identification` 等无关能力继续运行。
门禁结果必须保留失败 invariant 和 evidence reference，不能被聚合总分掩盖。

## 后续能力退出条件

以下能力不能因主链切换而被误称为已完成：

1. Product Media/Catalog 必须接入真实注册工具并验证来源/版本，才能离开测试替身；
2. Encoder Fast Path 必须用 Accepted Precision/Coverage 验收后才能替换确定性规则；
3. 删除旧模块前必须先迁移剩余消费者，不能通过在新主链增加兼容回退来掩盖。
