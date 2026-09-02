# M4-T04B legacy inventory and query-shadow contract

日期：2026-09-02。状态：`NO LEGACY DATA / BACKFILL NOT APPLICABLE / QUERY SHADOW PENDING`。

## 根因与资格边界

当前 legacy episodic writer 的 canonical metadata 只有 `user_id/conv_id/message_id/event_seq/role/archive_reason/
memory_version`，不包含 tenant、Case ID/status、Case Owner outcome verification 或可解引用 source event refs。因此旧
assistant 文本即使写着“已解决”，也不能推断成 ServiceEpisode。`ServiceEpisodeBackfillPolicy` 只读取显式 metadata；缺少
tenant/source/Case/accepted verification、Case 未闭合或 subject tombstoned 时，均返回
`RETAIN_CONVERSATION_ONLY` 和逐项 reason codes。inventory 按 legacy ID 稳定排序并输出 watermark、count、eligible/retained
count 与内容/metadata hash，同 snapshot 重放与输入顺序无关。

这意味着现有未增强的 raw-memory v4 corpus 即便有记录也不能按文本启发式晋升。2026-09-02 对当前配置
`localhost:8001/episodic` 执行只读 inventory：collection count=`0`、eligible=`0`、retained=`0`、watermark=
`chroma:episodic:count:0`、empty inventory SHA-256=`4f53cda...b945`；用户确认没有旧数据，故 backfill 明确为
`NOT_APPLICABLE`，不建设或执行迁移 writer，也不把空源表述成迁移成功。机器报告：
`legacy-episodic-inventory-v1.report.json`，且不导出 raw content。

## Policy 与 shadow

`MemoryRetrievalPolicy` 冻结 legacy Vector/BM25/Recency=`.30/.60/.10`、RRF k=`60`、lexical pool=`20`，并由
`HybridMemoryRetriever` 暴露 fingerprint；recency 仍只能重排 dense/lexical union。`EpisodeQueryCapture` 对同一个 query hash
记录 legacy/target status、generation、逐 route source rank、policy fingerprint、provenance 和 freshness refs，明确区分
`MATCH/RANKING_DIFFERENCE/CORPUS_DIFFERENCE/UNAVAILABLE/CONFLICT`，不把语料差异归因成权重差异。

项目没有 legacy 运行数据，old/new runtime shadow 不适用；原比较器只保留为离线 fixture attribution 工具，不形成第二条
reader。`MemoryRetrievalBinding` 已按 direct-cutover 收敛为一个 disabled 新 target；offline replay/主链 E2E 前不启用 consumer。

聚焦 policy/inventory/shadow 与 legacy hybrid 回归：`32 passed, 3 skipped in 1.24s`；加入真实空库存报告与
ServiceEpisode EvidenceReceipt resolver 后全仓：`971 passed in 99.71s`。Ruff 与 `git diff --check` 通过。
