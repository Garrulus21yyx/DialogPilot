# M4-T02B ThreadSummary projector build evidence

日期：2026-09-02。状态：`IMPLEMENTED / PRODUCTION MODEL CANARY PENDING`。

## Owner 与正向合同

唯一 `ThreadSummaryProjector` 通过 canonical Conversation projection outbox 读取 PostgreSQL MEM_L0。嵌入式
summarizer adapter 只接收原始 `SummarySourceItem` 并返回 candidate text；range、source hash、generation、
checkpoint、幂等与持久化都由 projector/repository 拥有。durable API composition 已将 `THREAD_SUMMARY` 从 legacy
Redis writer 替换为该 owner，其他 Memory projections 不受此节点迁移。

`ThreadSummaryPolicy v1` 只允许 message/token 阈值、thread idle、conversation finalize、Handoff 或显式 rebuild
触发模型，不按每条事件无条件调用。每个 job 固定一个有界连续 `[from_seq,to_seq]`，稳定 identity 包含 subject、
generation、range、source SHA-256、summarizer/schema version。

## 提交、读取与重建不变量

- commit 在 subject lock 下从 raw L0 重读并校验 source hash；immutable chunk insert 与 expected-version checkpoint
  CAS 在同一个 PostgreSQL transaction 内，故障不会留下已提交 chunk 或跳过 range；
- 同 candidate replay 返回 `ALREADY_APPLIED`，竞争内容、过期 generation/version、source drift 返回 typed
  `ThreadSummaryConflict`；projection watermark 单调且不能超过 source；
- read 验证 current-generation 连续 range、source hash、checkpoint target 和 source watermark；错误显式变为
  `DEGRADED`，缺失/删除 subject 为 `UNAVAILABLE`，没有最新 N 条读取上限隐藏旧事件；
- rebuild 原子切换到新 generation 并推进 CAS fence，然后只从 raw L0 重新生成。旧 generation 留作非权威审计，
  不进入 summary-of-summary；旧 worker 不能移动新 checkpoint；
- 模型/adapter 异常只把 summary checkpoint 标为 `DEGRADED`，不推进 projection watermark、不修改/删除 L0。

Migration `20260902_0019` 以前向演进给 chunks/checkpoint 增加 generation 并迁移 range uniqueness。已经发布且被
migration ledger 记录的 `0018` 保持 byte-for-byte 不变，避免用历史 checksum 改写伪装 schema 演进。

冻结合同：`governance/concurrency/m4-t02-thread-summary-v1.json`。

## 验证与边界

定向测试覆盖 outbox→projector、policy skip/force、固定 range、replay、竞争 CAS、chunk/checkpoint crash rollback、
source corruption、raw-L0 generation rebuild、deletion/epoch fence 与模型 unavailable degradation。

- M4-T02 owner tests：`16 passed in 7.53s`；
- schema/Memory/outbox/DataLocation/retrieval 相关回归：`82 passed`；
- 全量仓库回归：`902 passed in 86.41s`；
- Ruff、schema artifact reproducibility、capability claim audit 与 `git diff --check`：通过。

本节点不宣称真实 production 模型摘要质量、成本/延迟、staging canary 或独立 Memory reviewer 已通过；也不关闭
M4-T03–T08 或 M4 Exit。legacy Redis summary 仍只属于 flag-off 外的兼容路径，不能成为 PostgreSQL checkpoint Owner。
