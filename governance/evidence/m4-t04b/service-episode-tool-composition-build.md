# M4-T04 ServiceEpisode on-demand tool composition

日期：2026-09-02。状态：`COMPOSITION CONTRACT IMPLEMENTED / API CUTOVER DISABLED`。

`ServiceEpisodeMemorySearch` 是 Planner/Agent `memory_search` 的目标 application service。它只从认证的
tenant/user/entity scope、单一 `MemoryRetrievalBinding` 和 authoritative `RetrievalGeneration` 构造请求，不接受模型提供 tenant
或 generation。binding disabled 时在 provider/backend 前返回 `INVALID_CONTRACT/DIRECT_CUTOVER_DISABLED`；policy、backend ID、
generation ID 或 corpus watermark 漂移返回 `CONFLICT`。generation registry 不可用返回 `UNAVAILABLE`，embedding provider 故障只
降级为 lexical route，最终 hit 保留 revision/provenance/source ranks/freshness/index watermark/policy fingerprint。

canonical PostgreSQL offline replay 已通过。`api/main.py` composition root 已将旧 `memory_search →
MemoryManager.search_long_term` 替换为 `service_episode_search → ServiceEpisodeMemorySearch`，并创建隔离的 retrieval pool、generation
registry、single binding、Postgres backend 与 generation-validated MiniLM query embedder。工具只接受 query/entity IDs；tenant/user 来自
trusted context。无 PostgreSQL service 返回 typed `UNAVAILABLE`，disabled binding 不访问 embedder/backend。

query embedder 只支持 generation 声明的 `all-MiniLM-L6-v2`、384 维和可重放 model digest；model/dimension/digest 或 provider
shape/numeric drift 在数据库查询前 fail closed，普通 provider 故障才允许 lexical degradation。聚焦 lifespan/tool/provider/authority
回归：`23 passed in 1.42s`；全仓：`989 passed in 99.70s`。真实 `ChatApplication` agent tool-call E2E、legacy Evidence adapter/writer 删除与 binding activation 尚待下一节点。
