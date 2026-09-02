# M4-T04 direct-cutover current-thread Memory boundary

日期：2026-09-02。状态：`CURRENT THREAD BOUNDARY IMPLEMENTED / SERVICE EPISODE TOOL CUTOVER PENDING`。

目标架构要求 `ChatApplication` 固定读取当前会话窗口，跨会话 ServiceEpisode 只由 Planner/Agent 按需检索。原
`MemoryManager.get_context` 同时读取当前 thread 并无条件调用 legacy raw-memory search，使应用入口预检索后 Agent 仍可再次调用
`memory_search`，形成重复检索与两个跨会话语义 Owner。

新增 `MemoryManager.get_current_context`，只读取 working window、range summary 与 active profile，确定性返回空
`relevant_history/retrieval_hits`。`PostgresMemoryProjectionReader` 在真实应用路径优先调用该端口，非空用户 query 也报告
`retrieval_outcome=NOT_NEEDED`；legacy `get_context` 暂仅保留给尚未切换的离线 fixture，不再位于 PostgreSQL
`ChatApplication` 主链。异常、watermark lag 与 raw current-thread fallback 语义不变。

聚焦 Memory projection/ChatApplication 回归：`26 passed in 3.35s`；全仓：`976 passed in 103.20s`。新 ServiceEpisode tool composition 与 offline replay
尚未完成，因此 direct-cutover binding 保持 disabled。
