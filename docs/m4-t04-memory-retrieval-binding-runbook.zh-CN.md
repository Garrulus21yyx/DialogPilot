# M4-T04 Memory retrieval binding 切换与回滚

适用范围：`corpus=SERVICE_EPISODE`。本 runbook 只描述 build/dark-shadow binding；没有 M4 Exit APPROVE 与
`POSTGRES_RETRIEVAL_GA` 时不得把默认 mode 切到 `ACTIVE`。

1. 读取 tenant 当前 `retrieval.memory_retrieval_bindings` 行并固定 `version`。确认 active/previous 是完整的
   `policy fingerprint + backend generation + corpus generation` tuple，不能分别修改三个字段。
2. 初始化 dark shadow 时用 `initialize_shadow()`：active/previous 均指 verified legacy，candidate 指 PG ServiceEpisode。
   重复相同 tuple 幂等；任何字段不同都返回 conflict。
3. M4-T04C bounded canary 只能用 `compare_and_swap(expected_version)` 把 mode 改为 `PINNED_CANARY`。每次 invocation
   在 admission 时复制完整 binding；运行中不重新读取 pointer。
4. promotion 到 `ACTIVE` 必须绑定正式 GateDecision、真实 inventory/backfill reconciliation、same-query shadow 和
   deletion-fence 报告，并以一次 CAS 将 candidate 变成 active、旧 active 变成 previous、candidate 清空。
5. 任何 continuity/provenance/freshness、UNAVAILABLE/CONFLICT、延迟或删除 fence gate 失败时，调用
   `rollback(expected_version)`；该操作在一次版本递增中恢复 previous policy/backend/corpus tuple并清空 candidate。
6. CAS conflict 时停止操作、重新读取全行并重新评估，不得按字段重试或覆盖。已开始 invocation 按其 pinned snapshot
   完成；rollback 只影响后续 admission。

当前默认状态必须保持 `SHADOW` 或 `LEGACY`；本 runbook 不是生产切换授权。
