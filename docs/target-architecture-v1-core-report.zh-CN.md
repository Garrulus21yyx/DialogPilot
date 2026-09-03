# DialogPilot Target Architecture v1 核心实施报告

日期：2026-09-03

## 当前结论

Target Architecture v1 的新核心合同和内存级执行闭环已经实现，但尚未切换
`/chat` 主链。当前状态是“新核心可执行”，不是“迁移完成”。

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

尚未实现：

- 新 ConversationManager 对 `/chat` 的主链接管；
- ConversationState 和 OperationLedger 的 PostgreSQL 实现；
- LangGraph durable checkpointer；
- 当前业务 ToolManager 到新 WorkItem Runtime 的正式适配；
- 旧 command-primary、旧 AgentOrchestrator 和旧合同的删除；
- 基于真实 PostgreSQL 和 HTTP API 的最终 E2E。

## 六条垂直场景

| 场景 | 执行形态 | 核心断言 |
|---|---|---|
| 订单状态查询 | DIRECT | 只执行一次原子读取，不启动领域 Agent |
| 商品识别 | 单领域 DELEGATED | 只启动 Product Agent，并使用注册 Skill 能力包络 |
| 退款申请 | WORKFLOW | operation key、审批、Receipt、重复调用不产生第二次副作用 |
| 退款查询 + 商品问题 | MULTI_DOMAIN | 两个无依赖 Worker 同波并发 |
| Product 失败、Refund 成功 | 部分失败 | Refund 结果保留，Product 保持 typed failure |
| 用户要求人工 | WORKFLOW + Publication | 只有 Ticket Receipt 能转移会话 Owner 并宣称创建成功 |

## 验证结果

- Target v1 专项测试：54 passed。
- 仓库级回归（阶段 4 后）：926 passed、156 skipped、3 failed。
- 3 个仓库级失败来自未提交 RAG policy 字段与旧 Bundle 白名单不一致，以及
  stateful ticket 测试缺少 PostgreSQL URL；均不属于 Target v1 新代码路径。

## 安全门禁语义

门禁按 capability 生效。`execute_refund` 的重复副作用断言失败时，只禁用
`execute_refund`；`order_status` 和 `product_identification` 等无关能力继续运行。
门禁结果必须保留失败 invariant 和 evidence reference，不能被聚合总分掩盖。

## 下一步退出条件

只有满足以下条件，Target Architecture v1 才能标记迁移完成：

1. `/chat` 只经过新 ConversationManager；
2. Conversation、Operation、Receipt 和 Publication 各有唯一 PostgreSQL Owner；
3. LangGraph checkpoint 只保存执行位置，不替代业务事实；
4. 旧主链和重复权威被删除；
5. 六条场景通过真实 HTTP/PostgreSQL E2E；
6. 专项测试、全量测试、文档和运行时声明一致。

