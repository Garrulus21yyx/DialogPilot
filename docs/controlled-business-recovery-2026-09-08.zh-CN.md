# 受控业务执行与故障恢复：实现和实测口径

> 本文保留 v2 旧实现基线（160/200）。后续已补齐幂等恢复和人工出口，当前实现及 v3 实测见 [业务未知结果的有界恢复](write-recovery-contract-2026-09-08.zh-CN.md)。下文“当前”均指当时的 v2。

本轮在既有生产实现上新增真实 PostgreSQL 业务故障实验和断线续取验收，没有另建一套演示执行器。原简历中的“200 次故障注入、98% 恢复”没有现成证据，不能直接沿用。

## 实现责任与数据流

1. 动作准备由 `infrastructure/target_action_preparation.py` 负责，`application/action_approval.py` 将动作绑定到会话审批。参数、目标实体版本、操作标识和动作能力约束进入 WorkItem；`operation_fingerprint` 保持跨轮次的业务身份，修改参数不能复用旧操作。
2. `TargetWorkflowExecutor` 从可信上下文构造审批授权和会话范围，交给 `GovernedWriteRuntime`。没有授权时停在 `WAITING_APPROVAL`；版本或操作不匹配时以类型化冲突拒绝。
3. `PostgresOperationLedger` 将操作记录写入 PostgreSQL，通过当前记录和版本 CAS 争夺状态迁移。应用内锁只协调单个运行时，跨实例的正确性依赖 PostgreSQL CAS 和业务 owner 的幂等事务。
4. `CustomerOperationsService` 在事务内校验业务资格、目标版本和幂等键，提交退款申请并返回回执。CAS 本身不能替代业务写入防重；二者之间存在“业务已提交，执行记录未写回”的故障窗口。
5. 超时或取消中断后的 `EXECUTING` / `OUTCOME_UNKNOWN` / `RECONCILING` 先走 `_ToolReconciler`，按操作查询业务回执。查到匹配回执才能记为 `COMMITTED` 并重放；无法证明结果时保留对账状态。明确 `NOT_COMMITTED` 的结果才允许受控重试。
6. `AsyncPostgresCheckpointOwner` 持久化 LangGraph 工作进度；它与业务操作记录分别拥有任务进度和业务执行事实。Checkpoint 重放仍须经过操作记录防重。
7. `PostgresResponseDeliveryService` 持久化响应序号，通过 `list_after(after_seq)` 续取。ACK 允许重复、乱序，状态按 `SELECTED → DELIVERED → READ` 单调推进。

## 200 次实验

预注册见 [实施计划](../plans/controlled-business-recovery-2026-09-08.md)。固定种子 `20260908`，五类场景各 40 次，随机打乱顺序。每个场景使用独立订单、会话、操作标识和不同的业务参数；每次恢复调用都重建执行器和数据库操作记录适配器。恢复预算为四次调用。

使用真实 PostgreSQL、生产 MCP 业务工具、生产写入与对账适配器和退款业务 owner。故障发生在工具调用边界，不调用 LLM。数据库由测试 fixture 创建并清理，不写生产库。

| 场景 | 次数 | 自动恢复 | 业务记录数 | 关键检查 |
|---|---:|---:|---:|---|
| 等待审批后丢弃执行器并重建 | 40 | 40 | 40 | 审批前零写入，授权后执行 |
| 原请求仍在执行时进入重复调用 | 40 | 40 | 40 | 重复调用先对账，CAS 竞争后从回执恢复 |
| 工具派发前超时 | 40 | 0 | 0 | 查不到回执时保持未知，不重新派发写入 |
| 业务提交后丢失返回结果 | 40 | 40 | 40 | 从真实数据库回执恢复 |
| 业务提交后 worker 取消 | 40 | 40 | 40 | 保留执行中记录，重建后先对账 |
| 合计 | 200 | 160 | 160 | 重复业务记录 0 |

自动恢复定义为预算内返回 `SUCCEEDED`、拥有匹配回执且存在一条业务记录。因此本矩阵自动恢复率是 **160/200 = 80%**。40 次安全停留在对账状态不计入恢复成功，也不从分母剔除。“200 个测试通过”表示符合成功与未知两类预期合同，不代表 200 次业务均自动完成。

为什么不能把写入前超时直接算作失败后重试？故障注入器知道异常发生在派发前，恢复系统只看到传输异常。没有回执不能证明旧请求以后不会执行。当前适配器没有给出可确认的未提交结果，所以继续对账是其实际支持的行为；本轮没有为提高数字而放宽这个边界。

## 配套验收与边界

最终验收为矩阵 **200 passed**，配套 gates **205 passed、2 skipped**；两项跳过仅为内存后端不适用的数据库 scope 测试，PostgreSQL 验收均执行。汇总 `valid_run=true`，运行前后 manifest 中源码哈希一致。配套 gates 覆盖审批修订、参数/版本冲突、租户隔离、幂等事务、操作 CAS、未知结果对账、持久 Checkpoint 重建、真实子进程退出后恢复，以及响应续取和 ACK。

新增响应测试在 PostgreSQL 中发布三条响应，每次重建交付服务、按一条一页续取，并遍历重复 READ / DELIVERED ACK 的全部排列，检查序号顺序和状态不回退。

200 次矩阵是**业务执行边界的故障实验**，不包含自然语言提案准确率、HTTP 重连或 200 次进程崩溃。真实进程退出的 Checkpoint 测试属于单独 gate，不能混入 200 次分母。结果只证明此次固定故障分布和预算下的行为，不能直接推断生产恢复率。

## 复现与证据

```bash
# TEST_DATABASE_URL 指向允许创建隔离数据库的测试 PostgreSQL 实例。
PYTHONPATH=. .venv/bin/python scripts/run_controlled_business_faults.py \
  --output artifacts/eval/controlled-business-recovery-new-run
```

输出目录必须不存在。脚本保留源码哈希、HEAD、工作区 diff 哈希、逐案事件与回执、pytest 日志/JUnit 和汇总；缺少 PostgreSQL 配置时直接拒绝运行。只有明确“不适用于内存后端”的两项历史 scope 测试允许跳过，数据库验收跳过不算有效运行。

- 当前矩阵：[v2 逐案记录](../artifacts/eval/controlled-business-recovery-2026-09-08-v2/cases.jsonl)、[矩阵日志](../artifacts/eval/controlled-business-recovery-2026-09-08-v2/matrix.log)、[最终汇总](../artifacts/eval/controlled-business-recovery-2026-09-08-v2/summary.json)。
- [v1](../artifacts/eval/controlled-business-recovery-2026-09-08-v1/) 保留统计错误：工具给幂等键加了用户/会话前缀，初版评测用原始操作标识查表导致漏计。v2 按独立测试订单统计所有退款记录，覆盖更完整；v1 不作为通过证据。

## 简历可用表述

建议避免用此次无法支持的 98%：

> **受控业务执行与故障恢复：** 将 Agent 动作提案绑定业务参数、目标版本和操作标识，通过审批授权、PostgreSQL CAS 操作记录及业务幂等回执控制执行；对未知结果先对账，结合持久 Checkpoint 恢复任务进度，并通过响应序号与单调 ACK 支持断线续取。在真实 PostgreSQL 上完成 200 次执行边界故障注入，覆盖审批中断、并发重复调用、超时与取消，未出现重复业务写入。

若需要写恢复指标，可追加“自动恢复 160/200，其余 40 次保持安全对账状态”，并在面试中说明上述分母和故障分布。不能写“恢复成功率 98%”或“200 次宕机全部恢复”。
