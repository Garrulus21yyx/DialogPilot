# M4-T03C versioned ContextPolicy build evidence

日期：2026-09-02。状态：`POLICY SLICE IMPLEMENTED / M4-T03 IN PROGRESS`。

## Owner 与正向合同

`ContextPolicyV1` 现在拥有 node/task/route section 选择与 baseline priority。active tickets `90`、Knowledge `85`、
Profile `75`、Relevant History `65`、Conversation Summary `55` 不再由 API/Memory producer 的任意常量决定；producer
只提交 typed `ContextSection`，policy 在装配边界重写已知 tag 的优先级。未知扩展 tag 暂保留其显式 priority，不能
覆盖五个已冻结 tag。

闭合 node algebra 为 legacy/router/planner/worker/verifier/synthesis，route algebra 与 `RouteMode` 一致并额外保留
legacy。未知 node/route typed `ContextPolicyError`。Router 只接收 ActiveCase/ServiceContinuity/summary/profile/
commitment 的有界 section/ref，不接收详细 Knowledge 或跨会话 history；Worker 在任务声明 context refs 时只接收
对应非 entity section，entity refs 仍由独立 typed field 传递。ChatApplication 已按实际 pinned route 以 planner node
装配，不再只走无上下文的默认选择。

`PromptContext.selection_trace` 对每个输入 section 暴露 `SELECTED/TRUNCATED/DROPPED_POLICY/DROPPED_BUDGET/
QUARANTINED/EMPTY`、reason、policy priority 与 legacy producer priority；scoped task view 同步裁剪 trace。该 trace
提供旧 producer priority 与新 policy decision 的 shadow 对账事实，不把投影改成新的业务权威。

冻结合同：`governance/concurrency/m4-t03-context-policy-v1.json`。

## 边界

本 slice 没有实现 M4-T03A WorkingContext 字段/source/TTL/reducer，也没有关闭 provider native cache privacy policy
或累积 tool result receipt/locator compaction。Active tickets 当前仍是 legacy bounded section，任务相关 ActiveCase
选择归 M4-T03B。因此 M4-T03 继续保持 in progress。

聚焦 context/application/orchestration 回归：`96 passed in 5.68s`；全量仓库 `924 passed in 86.37s`；Ruff 与
`git diff --check` 通过。
