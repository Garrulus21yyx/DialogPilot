# M4-T04 canonical PostgreSQL offline replay

日期：2026-09-02。状态：`OFFLINE REPLAY PASS / CHATAPPLICATION CUTOVER PENDING`。

replay 从空 PostgreSQL schema 构造四个 canonical projection fixtures：目标 tenant/user 的 E401 与重复扣款 episode、同 tenant
不同 user 的相同 E401、不同 tenant 相同 user 的相同 E401。测试注册 immutable ServiceEpisode generation、写入受 deletion fence
约束的 projection、将 fixture binding 从 disabled 单向激活，再通过 `ServiceEpisodeMemorySearch → PostgresHybridBackend →
ServiceEpisodeRetriever` 执行两条 query。

机器报告 `service-episode-offline-replay-v1.report.json` 固定两个 query hash、scope hash、expected/hit IDs、revision、provenance、
lexical/recency ranks、freshness、generation watermark 和 policy fingerprint，不保存 query 原文。结果：case count=`2`、
Recall@K=`1.0`、forbidden tenant/user leak=`0`、result=`PASS`。报告由同一 evaluator 重放并逐字段相等验证，不能手工改结果绕过测试。

聚焦 PostgreSQL replay/composition/fusion：`8 passed in 1.22s`；全仓：`980 passed in 103.15s`。这证明新检索链的离线正向合同，不代表 API 已切换；真实
`ChatApplication` tool-call E2E、legacy reader/writer 删除和唯一 composition root 替换仍待下一节点，常驻 binding 继续 disabled。
