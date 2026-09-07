# Target 主链单路径收敛实施

依据：target-chain-convergence-audit-2026-09-07.zh-CN.md。用户已授权实施；迁移后不保留旧的可运行入口或自动回切。保留工作树无关修改。

1. done：修正答案检查、证据覆盖、执行完成的权威与 API/trace/Publication 公开投影；删除 requirement→verified 推导。新增 target-outcome-contract.zh-CN.md 与状态组合测试。540 passed，12 PostgreSQL 相关测试因缺 TEST_DATABASE_URL 跳过；不宣称数据库验证完成。
2. pending：统一结构化模型调用与错误诊断到当前 SDK，删除被替代协议入口及消费者，不新增第二套 provider 路径。
3. pending：简化回复核验并统一交互/审批/部分成功的结果组合，保留精确操作授权。
4. pending：核对续接、持久化窗口与过时证据规则，迁移相关消费者。
5. pending：运行性质测试与集成测试；真实模型和进程恢复证据与实现完成分开报告。

每个完整可验证阶段单独 commit/push。不得把第一阶段完成等同全部收敛；不得以未通过验收的旧链作为自动 fallback。
