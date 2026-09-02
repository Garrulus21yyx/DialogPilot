# M4-T01 MemoryProjectionResult build evidence

日期：2026-09-02。状态：`IMPLEMENTED`。

## Owner 与正向合同

`MemoryProjectionResult` 关闭 Memory read algebra：`READY/LAGGING/DEGRADED/UNAVAILABLE` 与
`NOT_NEEDED/NO_MATCH/HITS/UNAVAILABLE/CONFLICT` 分开表达。READY 强制所有 active target watermark 覆盖
PostgreSQL source watermark，且不能隐藏 omitted range、conflict 或 raw fallback。

`PostgresMemoryProjectionReader` 以 tenant/user/conversation scope 读取 source 与当前 generation watermark。
projection lag 时从 canonical turns 回读最近窗口，并按 request_id 排除已作为 current user message 提交的本轮
inbound；每个 target 的 included/omitted range 都进入 typed result。legacy Memory 仍提供 summary/profile/
episodic，但 vector/BM25 任一 backend 失败写 reason code；失败不能投影成 `NO_MATCH`。source 与 raw fallback
均不可用时，ChatApplication 在 Intent/Agent/Tool 前返回 retryable `memory_projection_unavailable`。

durable online facade 使用该 reader；未切换路径继续使用 legacy direct read，避免本任务越权改变离线 Eval/
CLI 行为。`memory_load` stage 记录状态、水位、ranges、conflicts、retrieval outcome 与 fallback，供后续 Trace/Eval
断言。

## 验证

验证覆盖 lag→raw fallback、current-turn exclusion、backend unavailable≠no-match、READY hidden omission/lag
fail closed，以及 unavailable 在 inference 前停止。

- Memory reader/legacy diagnostics/ChatApplication/lifespan 定向：`61 passed in 10.17s`；
- 全量回归：`886 passed in 73.68s`；
- 变更文件 Ruff 与 `git diff --check`：通过。
