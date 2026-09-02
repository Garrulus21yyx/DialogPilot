# 客服 Route Bundle Enable / Rollback Runbook

状态：M2-T06R build 合同。本文不授权生产 canary；生产执行仍需对应 Gate Manifest 与 release 审批。

## 1. Owner 与不变量

- `RolloutManager` 是 rollout state、admission 与 `active/canary/shadow` pointer 的唯一写 Owner。
- 一次 invocation 在 admission 时冻结 `bundle_version/bundle_hash/route_policy_ref/
  knowledge_backend_ref/knowledge_generation_ref/corpus_manifest_ref/retrieval_policy_ref`；后续 pointer
  变化不能改写该快照。
- 每次 assignment 至多一个 `publisher_version`。Shadow 只有观测权，不能发布、写工具、写 Transcript、
  建 Ticket 或写 Memory。
- active/canary/rollback admission 前都重新验证 Bundle 与 pinned refs；不兼容或不可用时 fail closed。
- 已进入写工具 `INVOKING` 的 invocation 依 ToolExecutionLedger receipt/reconciliation 结束，禁止换 Bundle
  重跑；read-only in-flight 只能按 pinned generation 完成，generation 不可用时返回 typed conflict。

## 2. Enable 前检查

当且仅当 release manifest 明确允许相应阶段时执行：

1. 核对 candidate 与 previous Bundle 均已注册且内容哈希不变。
2. 核对 route policy、Knowledge backend/generation、corpus manifest 与 retrieval policy refs 全部具体、
   可读取且 fingerprint 一致。
3. 核对 candidate 的模型部署、工具描述目标和 runtime schema 兼容。
4. 核对 hard-signal 告警、值班人、事件审计读取与人工 safe-response 队列可用。
5. Shadow graduation 后仅进入 5% canary；达到样本与质量门槛后才允许 25%，再经审批进入 active。

默认 soft rollback 门槛由以下服务端配置拥有：

- `ROLLOUT_SOFT_MIN_CANDIDATE_SAMPLES=100`
- `ROLLOUT_SOFT_MIN_BASELINE_SAMPLES=100`
- `ROLLOUT_MAX_REJECT_RATE_DELTA=0.05`
- `ROLLOUT_MAX_LATENCY_P95_RATIO=1.20`
- `ROLLOUT_MAX_COST_MEAN_RATIO=1.20`

硬信号 `unauthorized_tool/privacy_leak/cross_user_retrieval/wrong_write` 任一出现即触发 rollback 决策，
不等待 soft 样本。

## 3. Enable 操作

1. `start_shadow`：校验 graduation、runtime compatibility 与 execution refs；固定 refs；active 仍是唯一
   publisher。
2. `promote_canary(5)`：重新校验 pinned refs，原子删除 shadow pointer 并设置 canary pointer。
3. 观察 verified pass rate、p95 latency、mean cost 与所有 hard signal。
4. `promote_canary(25)`：门槛通过后单调升档；不可降百分比规避新版本注册。
5. `promote_active`：重新校验后，在同一事务中把 previous 标记 retired、candidate 标记 active、交换
   active pointer 并清除 canary pointer。任何进程故障必须使整笔事务回滚。

审计必须记录 actor、from/to state、evidence、observed_at、candidate/baseline version；请求 Trace 记录
assignment stage、Bundle version 与 pinned refs fingerprint。

## 4. Rollback 操作

1. Incident Commander 先提交 hard/soft signal 或手动 reason；不要在调用方修改 pointer。
2. `RolloutManager.rollback` 在单一事务内停止 shadow/canary admission，再验证 previous 的 runtime 与
   pinned refs。
3. previous 兼容时：candidate 进入 `ROLLED_BACK`，previous 恢复 `ACTIVE`，active pointer 原子交换；
   交换完成后 previous 才成为新请求的 sole publisher。
4. rollback 前已取得 candidate assignment 的 invocation 继续使用原 pinned refs；禁止交给 previous
   重跑。其 Knowledge generation 已不可读时返回 `PINNED_EXECUTION_REFS_UNAVAILABLE`。
5. 事务中崩溃时重新读取 pointer/state：只能观察到 rollback 前或 rollback 后的完整状态，不能手工拼接。

## 5. Previous 不兼容

如果 previous 的 schema、runtime、backend/generation 或 corpus manifest 已不可用：

- 禁止强制 pointer 回退；candidate 进入 `BLOCKED_FORWARD_FIX`，active pointer 保留用于审计，但
  `publisher_version` 为空，新 admission 返回 `rollout_admission_blocked`。
- 对用户只返回安全、无事实声明的 typed failure；具备 M4-T07C 单主 Handoff 能力后可改为受控 Handoff。
- 值班 Owner 注册并验证 forward-fix Bundle。新 Bundle 可从 blocked active 开始 shadow/canary；成功
  active 后将 blocked Bundle 置 retired。
- 不允许通过重启、清库、改百分比或客户端参数恢复旧 candidate。

## 6. 恢复 canary 的条件

必须同时满足：根因和受影响版本已记录；hard signal 已清零；previous/forward-fix refs 可读取；故障与
rollback crash tests 通过；新版本重新取得 graduation/release 审批；值班 Owner 明确确认观察窗口、
样本门槛和下一次自动 rollback 条件。已 `ROLLED_BACK` 的 Bundle 是终态，修复必须使用新版本号。

## 7. 演练验收

- promote-active pointer swap 前崩溃：previous 仍 active，candidate 仍 canary。
- rollback 停止 admission 后崩溃：candidate 仍完整 active，不出现半切 pointer/state。
- successful rollback：新请求只选 previous；rollback 前 assignment 仍固定 candidate。
- previous generation 不可用：不强退、不发布，进入 `BLOCKED_FORWARD_FIX`。
- shadow 与 canary 分桶均无双 publisher；写工具 unknown effect 不因 rollback 再执行。
