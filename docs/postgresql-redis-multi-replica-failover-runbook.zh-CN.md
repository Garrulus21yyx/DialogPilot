# PostgreSQL / Redis 多副本故障切换 Runbook

适用节点：`X-T02`。本文定义故障期间的单主边界和恢复顺序，不构成生产切换授权。生产演练证据、值班人和基础设施供应商步骤仍须由 Platform / SRE 签署。

## 1. 不变量与权威

- PostgreSQL 是 conversation、invocation、durable outbox、projection watermark 和 retrieval generation pointer 的唯一权威。
- Domain business system 是写工具业务 effect 的唯一权威；本地 ledger 只能保存 claim、binding、状态和 receipt 引用。
- Redis 只承载可丢弃 cache、single-flight、限流和通知。Redis 丢失不能触发 PostgreSQL 回退、SQLite 升主或业务 effect 重放。
- `worker_id` 只用于运维定位；outbox 的递增 `attempt` 是 fencing token。ACK、renew、release 必须同时匹配两者。
- retrieval generation 内容不可变；唯一 active pointer 只能通过 `expected_version` CAS 原子移动。

## 2. PostgreSQL 故障切换

### 2.1 检测与隔离

1. API 和 worker 收到 typed PostgreSQL unavailable 后 fail closed；禁止切回旧 SQLite writer。
2. 停止新的 start-outbox、delivery 和 projection claim；保留 inbound 请求的可重试身份，不生成替代 invocation/run。
3. 在基础设施层 fencing 旧 primary（撤销路由/写权限或完成 STONITH）后，才允许 promote replica。不能仅凭健康检查超时假定旧主已失效。
4. 新主开放前确认 `SELECT pg_is_in_recovery()` 为 `false`，并确认只有一个可写 endpoint。

### 2.2 恢复与对账

1. 运行 `PostgresMigrationRunner.verify()`；schema head、线性 migration ledger 和 DataLocation registry 必须一致。
2. 对 `workflow_invocations(invocation_key)`、response identity、outbox identity 和 retrieval active pointer 做唯一性/count/hash 对账。
3. 已过期 claim 由新 worker 领取同一 outbox identity，产生新 `attempt`；旧 attempt 的 ACK/renew/release 必须失败。
4. 已进入调用边界且 effect 未知的写工具保持 `reconciling`。先向 Domain Owner 查询稳定 operation key；仅权威 `NOT_COMMITTED` 允许再调用，`COMMITTED` 固化原 receipt/result。
5. 先恢复只读 API，再恢复 projection，最后恢复可能产生业务 effect 的 worker。每阶段观察重复 response/effect、lease ownership rejection 和 pointer CAS conflict。

## 3. Redis 分区、重启与故障切换

1. Redis timeout/断链按 cache miss 处理；Retriever 执行相同 Owner 查询，不能把 cache 故障投影成 `NO_EVIDENCE`。
2. single-flight 获取失败允许有界旁路重算，但稳定 evidence identity、generation 和结果合同不变。
3. compare-token Lua release 只能删除调用者自己的 lease；迟到 owner 不得删除新 owner 的 lease。
4. Redis 恢复后不从 Redis 回填任何权威事实。允许自然重热 cache；需要清空时按 namespace/generation 定向处理，不能据此移动 active pointer。

## 4. Retrieval generation 故障切换

1. build/shadow generation 永不自动 active。
2. promote 必须读取当前 pointer version，并在同一 PostgreSQL 事务执行 CAS；并发候选中最多一个成功。
3. CAS conflict 是预期负向结果。重新读取当前 pointer 和候选状态后重新决策，禁止覆盖 version 或手工改两行状态。
4. 迟到 projector 只能写其 pinned immutable generation；不能写 active generation 的别名，也不能移动 pointer。

## 5. 停止与回滚条件

出现任一情况立即重新冻结 effect worker：检测到两个可写 PostgreSQL primary、同一 invocation 出现两个 execution pointer、同一 response identity 有不同 payload、无权威 `NOT_COMMITTED` 却再次调用写工具、active generation 数量不为一、或 Redis 故障改变 retrieval typed outcome。

恢复采用 forward-fix 或完整 restore，不执行伪 downgrade，不删除已提交 conversation event。生产恢复完成的最低证据包括故障时间线、primary fencing 证明、schema/数据 reconciliation、in-flight tool receipt 对账和 held-out 双 active/迟到 worker 结果。
