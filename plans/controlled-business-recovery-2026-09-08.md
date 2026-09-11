# 受控业务执行与恢复实证

## 当前实施：RECOVERY-CONTRACT-2（用户已要求执行）
- 基线 HEAD 454e2f2；保留其他任务工作区修改。沿用 planning-with-files、ce-tool-management。
- done：确认业务 owner 已有事务锁、幂等参数摘要和回执，但恢复能力未进入 action 契约；未知结果无预算和人工出口。
- 目标：注册的幂等能力才允许在对账后用相同参数/版本/键重放；CAS 持久化恢复次数、下次时间；并发/晚到结果不回退；拒绝结果停止重试；预算耗尽持久化人工任务，业务结果仍标为未知。
- done：迁移能力快照/指纹/Checkpoint、操作记录 codec、执行与工具结果、会话和响应投影；恢复模式两种、CAS 预算单调、同操作重放、业务拒绝类型化、工单幂等与人工暂停状态均落地。
- done：生成状态/并发测试，真实 PG 延迟原请求和恢复取消；原 200 次矩阵保留分布，改用生产注册恢复能力，预算不因进程重建重置。查询/重放/工单插入后的取消或中断均有重建验收。
- done：v3 原矩阵 200/200 恢复、200 条业务记录、重复 0；279 项配套、62 项 HTTP、4 项投影验收通过，合计 545 passed、2 个明确不适用的内存 scope 跳过。所有 PG 测试执行。源码 manifest 复核无变化。
- 交付文件：application/{capability_registry,default_capability_registry,work_item,write_workflow,conversation_state,target_conversation_manager,target_chat_application,response_assembly}.py；infrastructure/{langgraph_checkpoint,postgres_target_runtime,target_workflow_execution,postgres_write_recovery}.py；mcp/customer_operations_tools.py；tests/{test_write_recovery,test_registered_write_recovery_postgres,test_write_recovery_projection,test_controlled_business_faults,test_target_http_postgres_e2e}.py；scripts/run_controlled_business_faults.py；docs/write-recovery-contract-2026-09-08.zh-CN.md。
- 证据：artifacts/eval/controlled-business-recovery-2026-09-08-v3/ 的 manifest、cases、summary、verification 和四组 JUnit/log。开发期暴露并修正 reconciliation tuple 在 Checkpoint 反序列化后变为 list 的问题；真实业务测试准备误用了 ACTION+flow 的组合，改用支持的 WORKFLOW，未放宽生产契约。
- 状态：实现和声明范围内验收完成；未提交、未推送，保留其他任务修改。下一步是按新文档使用真实简历口径；持续不可用和版本变化的人工出口不计入 200 次自动恢复分母，旧 v2 的 80% 保留为历史基线。

- 基线 HEAD：3ba6683；工作区已有大量其他任务修改，保留，不提交或覆盖。
- 范围：既有动作审批 → GovernedWriteRuntime → PostgresOperationLedger → CustomerOperationsService；PostgreSQL Checkpoint；响应序号和 ACK。
- 目标：审批绑定参数/版本/操作；未知结果只对账；同操作最多一条业务记录；重建运行时后可恢复；ACK 单调且可按序续取。
- 初步证据：上述实现已存在。scripts/run_controlled_refund_fault_matrix.py 实际验证回答语义，不构成执行恢复指标证据。当前缺口是可复现、连接真实业务 owner 的故障实验。
- 不预设 98%，不将模拟传输异常表述为机器宕机或生产可用性。

## 步骤
1. done：核对审批、操作记录、回执、Checkpoint、交付的现有契约与测试。生产 owner 已实现目标机制，本轮未修改生产执行语义。
2. done：新增 tests/test_controlled_business_faults.py 和 scripts/run_controlled_business_faults.py；新增 PostgreSQL 分页续取与 ACK 排列验收。
3. done：v2 矩阵 200 passed；配套 gates 205 passed / 2 个明确不适用的内存 scope 跳过；数据库测试均执行。summary.valid_run=true。
4. done：docs/controlled-business-recovery-2026-09-08.zh-CN.md 已记录原理、范围、80% 实测口径与简历表述；最终汇总和逐案记录一致，manifest 中源码哈希复核无变化。

## 实验预注册
- gap：RECOVERY-EVIDENCE-1。
- 假设：现有受控执行 owner 可在审批中断、重复执行、写入前/后超时及取消中断后恢复，同操作最多一次业务提交。
- 数据：独立测试库、200 个独立订单/操作；固定种子打乱五类故障，各 40 次，无 LLM 调用。
- 固定变量：生产业务 owner、PostgresOperationLedger、GovernedWriteRuntime；最多 4 次恢复调用。
- 指标：成功恢复/全部注入；重复业务行数；执行/对账时序；实际提交数；失败记录不得排除。
- 采用标准：200 次均执行并留证，零重复业务写入；真实结果决定简历数值。Checkpoint 和 delivery 独立集成测试不混入该分母。
- 交付状态：本轮受控实验及证据交付完成；未提交、未推送。未声明 98% 达成，未声明生产恢复率或开放域可靠性关闭。

## 诊断与实验记录
- 初期测试开发遇到 PYTHONPATH、集合语法、数据库 tuple 行取值问题，均为测试工具问题，未改生产逻辑。
- v1（artifacts/eval/controlled-business-recovery-2026-09-08-v1）矩阵 160 failed / 40 passed：统计查询错误地用原始操作键匹配业务幂等键，漏计真实业务写入。转换 owner 是 MCP 业务工具，其键包含用户/会话前缀；改为按独立订单统计全部退款行，避免复制键转换逻辑。v1 无效证据保留。
- v2 矩阵：五类各 40 次；160 次自动恢复，40 次派发前超时保持未知，真实业务行 160，重复行 0。未知结果的故障注入位置对恢复系统不可见，因此不能据此添加盲重试，也不能将这 40 次算成功。
- 配套 Checkpoint / delivery gates 与故障矩阵分开计数。矩阵不冒充 HTTP/LLM 或进程崩溃成功率。
- 最终证据：artifacts/eval/controlled-business-recovery-2026-09-08-v2/{manifest.json,cases.jsonl,matrix.log,matrix.xml,gates.log,gates.xml,summary.json}。200 + 205 项通过；编译检查和相关 diff whitespace 检查通过。
- 下一步：简历使用文档中“200 次注入、零重复业务写入”的有证据表述；若另行要求提高自动恢复率，需要先定义业务 owner 可确认未提交或幂等重放的契约，以及不可恢复场景的处理方式，再重新预注册实验，不能把未知算成功或删减分母。
