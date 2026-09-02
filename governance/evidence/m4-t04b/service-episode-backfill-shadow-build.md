# M4-T04B legacy inventory and query-shadow contract

日期：2026-09-02。状态：`CONTRACT IMPLEMENTED / REAL INVENTORY AND POINTER PENDING`。

## 根因与资格边界

当前 legacy episodic writer 的 canonical metadata 只有 `user_id/conv_id/message_id/event_seq/role/archive_reason/
memory_version`，不包含 tenant、Case ID/status、Case Owner outcome verification 或可解引用 source event refs。因此旧
assistant 文本即使写着“已解决”，也不能推断成 ServiceEpisode。`ServiceEpisodeBackfillPolicy` 只读取显式 metadata；缺少
tenant/source/Case/accepted verification、Case 未闭合或 subject tombstoned 时，均返回
`RETAIN_CONVERSATION_ONLY` 和逐项 reason codes。inventory 按 legacy ID 稳定排序并输出 watermark、count、eligible/retained
count 与内容/metadata hash，同 snapshot 重放与输入顺序无关。

这意味着现有未增强的 raw-memory v4 corpus 按正向合同应是零可晋升，而不是做启发式 backfill。真实 Chroma 实例的
count/hash/watermark 尚未采集，本 evidence 不伪造生产 inventory 数量。

## Policy 与 shadow

`MemoryRetrievalPolicy` 冻结 legacy Vector/BM25/Recency=`.30/.60/.10`、RRF k=`60`、lexical pool=`20`，并由
`HybridMemoryRetriever` 暴露 fingerprint；recency 仍只能重排 dense/lexical union。`EpisodeQueryCapture` 对同一个 query hash
记录 legacy/target status、generation、逐 route source rank、policy fingerprint、provenance 和 freshness refs，明确区分
`MATCH/RANKING_DIFFERENCE/CORPUS_DIFFERENCE/UNAVAILABLE/CONFLICT`，不把语料差异归因成权重差异。

`MemoryRetrievalBinding` 把 policy/backend generation/corpus generation 作为一个不可拆 tuple，并提供整体 rollback 语义；
durable PostgreSQL pointer/CAS 尚未落地，因此本 slice 不满足 M4-T04 第 9 项，也不启用 target consumer。

聚焦 policy/inventory/shadow 与 legacy hybrid 回归：`32 passed, 3 skipped in 1.24s`；全仓：
`965 passed in 98.98s`。Ruff 与 `git diff --check` 通过。
