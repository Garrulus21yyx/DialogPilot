# M4-T04A ServiceEpisode schema and promotion foundation

日期：2026-09-02。状态：`SCHEMA/PROMOTION FOUNDATION IMPLEMENTED / M4-T04 IN PROGRESS`。

## 权威与晋升合同

`ServiceEpisodeCandidate` 只有在 Case 为 `resolved|closed` 且 Case Owner 的 typed outcome verification 为
`ACCEPTED` 时成立；`episode_id=case_id`。普通 assistant/published text、用户反馈、单工具成功或无 Case 的 conversation
不能单独满足构造器。problem、产品版本、症状、材料、动作、authoritative outcome、resolution、root cause、验证 receipt、
source event refs、extractor/schema version 和 deletion epoch 一起进入稳定 provenance hash。

PostgreSQL `service_episode_revisions` 是 immutable canonical owner，`service_episode_heads` 以 expected-version CAS 指向当前
revision；同 revision/provenance replay 幂等，任何内容漂移或竞争 revision typed `ServiceEpisodeConflict`。user 原话与
assistant 文本分列保存；retrieval search 使用 user=A、canonical=B、assistant=D 的数据库生成 tsvector，不把 assistant 文本
伪装成同权威 source。

## 数据位置、迁移与删除

DataLocation v6 在首次 schema/write 前把 `location:service-episode:v1` 晋升 `WRITE_APPROVED`，绑定删除 adapter、proof 与
既有 PostgreSQL deletion-epoch restore fence。forward-only migration `20260902_0020` 建表、安装 v6 fingerprint、增加角色
分权重 lexical projection，并用数据库 trigger 二次检查 subject epoch。conversation tombstone 同事务清除 episode head/revision；
既有 retrieval deletion trigger 清理 search rows并拒绝未完成 projection event。

冻结合同：`governance/concurrency/m4-t04-service-episode-v1.json`。

## Projection 边界与未关闭项

canonical commit 可在同事务写 `SERVICE_EPISODE` projection event；resolver 只接受当前 head、相同 provenance 与当前 subject，
并将 outcome receipt/provenance 写入检索投影。默认 consumer 未切换。legacy corpus 已确认 count=0，用户确认没有旧数据，
因此 backfill 不适用；durable MemoryRetrievalPolicy pointer/rollback 已由 M4-T04B 补齐。旧/new query dark shadow 与真实
heldout relevance/freshness 证据尚未完成，因此 M4-T04 仍为 in progress，M4-T04C 不可启动。

聚焦 PostgreSQL/schema/promotion 回归：`31 passed in 22.06s`；加入 canonical EvidenceReceipt resolver 后全仓：
`971 passed in 99.71s`。Ruff 与 `git diff --check` 通过。
