# M4-T04B raw episodic writer retirement

日期：2026-09-02。状态：`BUILD PASS / DIRECT CUTOVER`。

## 目标合同

跨会话可检索记忆只由 canonical PostgreSQL `ServiceEpisode` generation 提供。当前 thread 的 raw event log、summary
checkpoint 和 fact schedule 仍由各自 projection Owner 维护；它们不得向 Chroma raw episodic collection 双写，也不得注册
`episodic_index` outbox target。summary checkpoint 的提交只依赖固定 source range 与 CAS，不依赖跨会话索引副作用。

## 变更与边界

- 删除 `MemoryManager.add_messages/_compress/finalize_conversation` 对 raw episodic writer 的调用，并删除 writer 与
  canonical episodic projector。
- projection enum、PostgreSQL outbox registry、dispatcher、watermark 查询和 frozen concurrency contract 只保留
  `working_window/thread_summary/fact_extraction`。
- stateful acceptance fixtures 改为验证 event-log idempotency、summary range CAS、并发写保留与 retry convergence，不再调用
  raw episodic writer。
- 用户确认没有旧数据，且只读 inventory 的 collection count 为 `0`。因此没有历史数据迁移、backfill、兼容双写或 shadow
  reader；`20260902_0006` 属于尚未发布的新部署 migration，直接修正初始 registry，而不是制造空迁移。

本节点不宣称 legacy Chroma reader/config 已全部删除；该 reader 已不在 API/ChatApplication 主链，后续节点继续收口离线兼容面。

## 验证

- raw writer/projection source search：无 `_archive_messages`、`project_episodic_message`、`EPISODIC_INDEX`、
  `episodic_index` runtime 命中。
- memory/projection focused tests：`77 passed`。
- stateful acceptance tests：`16 passed`。
- full repository suite：`987 passed in 103.25s`。
- schema registry 已由当前 migration chain 重建，claim registry checksum 与 audit pin 同步更新。
