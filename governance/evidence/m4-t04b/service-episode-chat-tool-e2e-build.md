# M4-T04 ChatApplication ServiceEpisode tool E2E

日期：2026-09-02。状态：`CHATAPPLICATION TOOL PATH PASS / RAW EPISODIC WRITER REMOVAL PENDING`。

`build_service_episode_tool` 现在是 API 与测试共用的唯一 tool manifest/handler Owner。`ChatApplication` canonical route evaluation
构造 Technical route 后，由 Agent evaluation adapter 通过真实 `MCPToolManager.call` 调用该工具；handler 只把 invocation
identity metadata 中的 tenant/user 与模型可提供的 query/entity IDs 交给 `ServiceEpisodeMemorySearch`。测试证明返回 canonical
episode ref/provenance，且 search 收到的 scope 与 invocation identity 一致。

Authority registry 已删除旧 `memory.prior_event → memory_search/memory-hit-v1` requirement/producer adapter；启动 gate 只接受
`memory.service_episode → service_episode_search/service-episode-hit-v1`。`MEMORY_EVENT` locator schema 暂保留给 canonical current-thread
event 引用，但不再有 raw cross-session tool producer。

聚焦 ChatApplication/ToolManager/Authority/lifespan：`37 passed in 1.40s`；全仓：`990 passed in 102.73s`。API target reader 已切换，
但 `MemoryManager` 内 raw Chroma episodic writer、projection enum/config 和离线兼容方法仍待下一节点删除；常驻 binding 仍未激活。
