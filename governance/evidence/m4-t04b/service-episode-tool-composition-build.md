# M4-T04 ServiceEpisode on-demand tool composition

日期：2026-09-02。状态：`COMPOSITION CONTRACT IMPLEMENTED / API CUTOVER DISABLED`。

`ServiceEpisodeMemorySearch` 是 Planner/Agent `memory_search` 的目标 application service。它只从认证的
tenant/user/entity scope、单一 `MemoryRetrievalBinding` 和 authoritative `RetrievalGeneration` 构造请求，不接受模型提供 tenant
或 generation。binding disabled 时在 provider/backend 前返回 `INVALID_CONTRACT/DIRECT_CUTOVER_DISABLED`；policy、backend ID、
generation ID 或 corpus watermark 漂移返回 `CONFLICT`。generation registry 不可用返回 `UNAVAILABLE`，embedding provider 故障只
降级为 lexical route，最终 hit 保留 revision/provenance/source ranks/freshness/index watermark/policy fingerprint。

聚焦 composition/fusion 回归：`7 passed in 0.44s`；全仓：`979 passed in 102.11s`。当前尚未替换 `api/main.py` 中 legacy raw-memory tool，也没有激活 binding；
必须先完成 canonical PostgreSQL offline replay 与真实 `ChatApplication` tool-call E2E，再在同一节点删除 legacy reader/writer/config。
