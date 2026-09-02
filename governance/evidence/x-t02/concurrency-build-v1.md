# X-T02 concurrency and multi-replica build evidence v1

日期：2026-09-02。状态：`IMPLEMENTED / PRODUCTION FAILOVER REHEARSAL PENDING`。

## 根因与正向合同

现有 PostgreSQL outbox claim 只以可复用的 `worker_id` 判断 owner。租约过期、由同名进程重新领取后，旧进程仍可能 ACK/release 新 claim。RunStore 工具位点只有永久 `executing`，无法区分“调用前崩溃”和“跨过调用边界后 effect 未知”。共同根因是调度身份、claim epoch 和业务 effect authority 没有分离。

修复后的合同：

- conversation invocation 继续由 PostgreSQL unique identity、conversation row lock 和 version CAS 单写；16 个独立进程争用相同 invocation 只产生一份 turn/event/invocation/start-outbox。
- start/projection outbox 的递增 `attempt` 是 fencing epoch；ACK、release、renew 同时匹配 outbox、worker 和 epoch。renew 不能复活已过期租约。
- 工具 call intent 先 `claimed`，handler 前原子进入 `invoking`。过期的调用前 claim 可重领；只读 invoking 可重算；写 invoking 必须进入 `reconciling`，仅 Domain Owner 的 `NOT_COMMITTED` receipt 允许同 operation 再 claim。
- retrieval generation identity 不可变；active pointer 以 version CAS 在单一 PostgreSQL transaction 切换，并发候选最多一个胜出。shadow/迟到 projector 不移动 pointer。
- Redis 仅是可丢弃 cache/single-flight；断链旁路执行相同 retrieval contract，compare-token release 阻止迟到 owner 删除新 lease。

冻结合同：`governance/concurrency/x-t02-concurrency-contract-v1.json`，SHA-256
`bf8139f0f3d48bbdcc414f990df1cc99e79238d59cf243bad908ce18f05c8cc4`。

## 验证

- 多进程：16 个 spawn process 对同一 invocation 并发 admission，结果 `1 Created + 15 Existing`，所有 durable identity count 为一。
- lease crash/迟到 worker：旧 epoch 即使复用相同 worker name，也不能 ACK、renew 或 release；当前 epoch 可续租。
- tool effect：expired write invocation 连续 claim 均保持 reconciliation；权威 `NOT_COMMITTED` 后才生成新 token；旧 token 无法提交终态。expired read-only invocation 可安全重领。
- 双 active：两个 READY generation 以相同 pointer version 并发 promote，恰好一个成功、一个 typed conflict，数据库 active count 为一。
- Redis/网络故障：cache 操作失败为 miss，不改变 retrieval typed outcome；owner-token fencing 与 stale-cache backend-unavailable 测试通过。
- 聚焦并发/故障套件：`76 passed in 22.99s`；全量仓库：`820 passed in 51.49s`；ruff 与
  `git diff --check` 通过。

## Runbook 与未闭合生产证据

[PostgreSQL / Redis 多副本故障切换 Runbook](../../../docs/postgresql-redis-multi-replica-failover-runbook.zh-CN.md)
SHA-256 为 `78e0f521f3e80111ff43a7b4b34b85fd68f20d479d92aba84485409d25937727`，定义旧主 fencing、claim freeze、schema/data reconciliation、effect receipt 对账和分阶段恢复。

本次没有真实双机 PostgreSQL promote、Redis cluster partition、生产流量 drain 或独立 SRE 签署，因此不把 build test 冒充生产 failover rehearsal。SQLite RunStore 仍只是 M3-T06 前的兼容 tool-claim owner；多副本生产 Agent ledger 切换必须由 M3-T06 完成 PostgreSQL 迁移和单主 release action。
