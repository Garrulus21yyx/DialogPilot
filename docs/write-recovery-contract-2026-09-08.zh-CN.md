# 业务未知结果的有界恢复

本轮补齐原故障实验暴露的恢复契约：业务 owner 已能幂等处理同操作，但执行层无法利用这项保证，导致写入前超时持续停在对账。实现现在使用注册能力决定是否重放，并将恢复预算、调度时间和人工核查工单持久化。

## 当前合同

`ActionReconciliationDefinition.recovery` 承载 `WriteRecoveryPolicy`，它属于受信任的能力注册表，不由 Agent 自行声明。默认生产注册的退款、取消订单、修改地址、冻结账户使用 `IDEMPOTENT_OPERATION`；人工转接动作使用 `RECEIPT_ONLY`，不假设其动态转接上下文适合同操作重放。

两个模式都先查回执。查询无结论时，`IDEMPOTENT_OPERATION` 可使用**原操作标识、原业务参数、原目标版本**重放；`RECEIPT_ONLY` 继续查询。重放资格来自业务 owner 的幂等合同，不是把“查不到”解释为“未执行”。原审批覆盖同一业务操作，不产生新操作或扩大审批范围。每次写入仍经过现有的工作修订检查。

生产默认最多三次恢复尝试，间隔一秒。一次恢复尝试包括一次查询以及在能力允许时的一次重放，因此最多为首次派发加三次恢复派发。次数在 I/O 前通过 PostgreSQL CAS 占用，`next_recovery_at` 同时记录恢复占用期限；结果仍未知时改为下次查询时间。取消或进程中断不会重置计数；重建后按持久时间等待或接管。运行中的执行节点自动推进到成功或人工出口，现有持久 Checkpoint 负责进程重建后的节点恢复。

每次恢复仍然保留同一操作指纹。变更模式、预算、业务参数、目标版本等会改变指纹，旧操作不能静默获得更大的重放权限。旧记录缺少恢复字段时按零计数读取；旧 WorkItem 未携带恢复能力时保留历史单次对账行为，不自动升级审批权限。新发布的注册能力指纹变化，挂起的旧任务应遵循现有版本校验或重新准备审批，不能篡改旧指纹继续执行。

## 业务执行和并发

`CustomerOperationsService` 已在事务内通过订单/账户锁、操作锁、请求摘要和唯一约束控制业务写入。幂等命中在版本复核前返回原回执；未命中才校验当前业务版本和资格。因此重放可覆盖“原请求没发出”“原请求还在运行”“原请求已提交但回执丢失”三种情况。

恢复层 CAS 协调多个执行实例，业务事务最终保证同操作防重。晚到的旧结果无法覆盖已被其他实例推进的记录。CAS 竞争是可重新读取的类型化冲突；操作身份冲突直接拒绝，不进入重试循环。数据库和内存操作记录共用单调预算及工单绑定校验。

客户业务工具将事务回滚的明确业务异常转换为 `ToolRejected`，与传输异常区分。过期版本、参数冲突和资格拒绝停止自动执行，进入人工核查；不会一直重试同一份失效审批。未知状态下得到未提交结果也保守停止并交人工，不据此改写原任务。

## 人工出口和对外状态

预算耗尽或明确业务拒绝时，操作记录进入 `MANUAL_REVIEW`。`PostgresWriteRecoveryReview` 使用租户、用户、会话和操作标识生成稳定工单幂等键，通过现有 PostgreSQL 工单 owner 创建核查任务及其 outbox。工单含原操作标识、参数、目标版本和指纹，人工应先核实原业务操作，避免另建操作重复处理。

创建工单后把 `manual_ticket_id` CAS 回写操作记录。若在工单提交与 CAS 之间中断，重建会复用原工单。原业务任务进入 `PAUSED / MANUAL_REVIEW`；结果为 `BLOCKED`，不携带伪造的业务成功回执。处理状态与业务结果分开：首次明确拒绝或操作级对账确认未提交时，反馈为 `NOT_COMMITTED`；此前结果未知、随后仅重放请求被拒绝时，原操作仍为 `UNCONFIRMED`。操作记录保存最近结果、观察范围及错误说明和来源，工单与回复消费同一份事实，不能从“转人工”反推“业务结果未知”。旧记录丢失的说明不凭空重建。HTTP 响应保留 `manual_review_actions`、`recovery_status=MANUAL_REVIEW` 和 `task_completed=false`，并按上述结论解释自动处理为何停止。

这是对该业务任务的人工核查交接，不会自动转移整段会话，也不会自动替人工判定退款成功或取消已提交业务。人工工单沿用现有工单管理流程；原未知操作不会因为工单存在而被标记成功。

## 验收与实测

此前 v3 恢复实现的历史结果：**545 passed、2 skipped**。其中矩阵 200、配套 gates 279、HTTP 回归 62、人工核查投影 4 项通过；跳过仅为内存后端不适用的数据库 scope 检查，PostgreSQL 验收全部执行。运行前后 manifest 中源码哈希一致，汇总保存在本地 `artifacts/eval/controlled-business-recovery-2026-09-08-v3/verification.json`。这些历史数字不能替代后续效果知识修复的验收。

后续效果知识修复区分处理状态、操作是否提交、最近观察的范围及来源；覆盖首次拒绝、未知后的重放拒绝、再次派发取消、旧成功记录和持久化。PostgreSQL 效果/工作流/状态套件为 **128 passed、2 skipped**；独立复核通过。真实模型的目标拆分与重复确认仍未闭环，不能将这些程序测试转换成自然语言任务成功率。当前交付快照的测试记录持续维护在 `plans/conversation-direct-capability-convergence-2026-09-08.md`。

- `tests/test_write_recovery.py` 穷举两种能力模式下的 `4^3` 条查询结果序列，在内存和 PostgreSQL 上验证预算、顺序、终态和幂等人工出口；另外验证查询/重放取消、工单提交后中断、能力指纹、Checkpoint 和操作记录往返，以及存储不能回退预算。
- `tests/test_registered_write_recovery_postgres.py` 使用四类真实业务 owner 和生产 `TargetWorkflowExecutor`，验证写入前/后超时、版本变化、持续不可用及人工工单防重；8 个恢复实例与延迟原请求交错执行时，共享一条业务写入和同一个回执。
- 原五类各 40 次的矩阵保持不变，启用生产注册恢复能力：审批中断、并发重复调用、写入前超时、写入后超时、提交后取消。本轮 200 次检查通过，自动恢复 200/200，业务记录 200，重复业务记录 0。旧实现 v2 的 160/200 保留为历史基线。
- 200 次矩阵不包含持续不可用或业务版本冲突；这些属于独立的人工出口验收，不能合并后宣称全部业务自动恢复。它也不是自然语言能力、生产可用性或 200 次机器宕机测试。

复现：

```bash
# 显式配置 TEST_DATABASE_URL 为测试实例；脚本新建并清理隔离库。
PYTHONPATH=. .venv/bin/python scripts/run_controlled_business_faults.py \
  --output artifacts/eval/controlled-business-recovery-new-run

# 补充 HTTP 和人工核查投影验收（同样使用 TEST_DATABASE_URL 的隔离库）
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_target_http_postgres_e2e.py tests/test_target_chat_cutover.py \
  tests/test_action_dialogue_convergence.py tests/test_target_architecture_e2e.py \
  tests/test_write_recovery_projection.py
```

历史证据保存在本地 `artifacts/eval/controlled-business-recovery-2026-09-08-v3/`，包含逐案 JSONL、源码哈希、矩阵与配套 gates 日志、JUnit 和 summary；额外 HTTP 验收记录为 `http-gates.log/xml`。旧实施记录位于 `plans/controlled-business-recovery-2026-09-08.md`。当前修复与交付状态见[维护计划](../plans/conversation-direct-capability-convergence-2026-09-08.md)；不能用旧产物证明新代码的检查状态。

## 简历表述

> **受控业务执行与故障恢复：** 将 Agent 动作提案绑定业务参数、目标版本和操作标识，通过审批授权、PostgreSQL CAS 操作记录及业务幂等回执控制执行；未知结果先对账，再依据业务幂等契约重放，持久化恢复预算并在异常持续时转人工核查，结合 Checkpoint 和响应序号、单调 ACK 支持任务恢复与断线续取。在真实 PostgreSQL 上完成 200 次受控故障注入，全部恢复且未出现重复业务写入。

面试时应同时说明固定故障分布、三次恢复预算，以及持续不可用/版本冲突转人工的边界；不再沿用没有证据的“98%”。
