# M4-T03R structured tool-result context build evidence

日期：2026-09-02。状态：`IMPLEMENTED / M4-T03 IN PROGRESS`。

## Owner 与正向合同

ToolManager/领域服务继续拥有完整结果与业务 receipt；ReAct 只在 provider-step 边界生成
`tool-result-context-v1`。projection 保留 call/tool/status/effect/authority、output/receipt schema、receipt ID 和可解引用
locator，正文只保留 96 estimated-token excerpt。full result 在现有 RunStore ToolExecutionLedger compatibility table 中
按 `(run_id,call_id)` 保存，locator 解析时校验 user/conversation scope、terminal/reconciling 状态和 replayable result；
跨用户/会话、未知/非终态 locator typed fail。

每个 ReAct step 从 checkpoint messages 重建 provider view：旧 tool-result turn 删除 excerpt，但不修改 checkpoint；最新
turn 保留 bounded excerpt。若最终 provider budget admission 仍因累计结果失败，再生成无 excerpt 的 provider view 并只重试
一次 admission；receipt/locator 始终保留。仍超限由统一边界抛 `ProviderContextBudgetExceeded`，provider side effect 未开始。
这不是 summary-of-tool-output，也不让模型摘要成为业务结果 Owner。

冻结合同：`governance/concurrency/m4-t03-tool-result-context-v1.json`。聚焦 ReAct/resume/budget `33 passed in
0.91s`；Ruff/diff checks passed。集成测试构造“原 envelope 超限、清除 excerpt 后可容纳”的精确边界，证明只发生
一次真实 provider attempt 且 locator/receipt projection 不被 emergency compaction 删除。
全量仓库回归：`942 passed in 84.00s`。

## 边界

当前 locator 使用 legacy RunStore compatibility port；M3-T06/T09 迁移 PostgreSQL ToolExecutionLedger 时必须保持 locator
解析合同或发布 forward-compatible resolver。真实 provider tokenizer 校准、ActiveCase relevance 与 WorkingContext 仍未
闭合，因此 M4-T03/M4 Exit 不提升为 VERIFIED/READY。
