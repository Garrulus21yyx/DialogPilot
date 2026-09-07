# Target 主链单路径收敛实施

当前续作：implementation_verified / quality_review_pending。
真实压缩开发评测已运行：8 个新样本 × 2 种最新工具批次，沿用生产实现及默认 .70/.85 阈值。
结构检查 16/16，自动 judge 16/16，但主执行助手复查提出 2 项语义异议，不能标为质量闭环。
记录输入/输出、SDK 用量、延迟与原文回读；没有修改生产 Prompt，没有业务写入。
详见 docs/target-context-compaction-eval-2026-09-07.md。后续仍需独立裁决、真实续接和 fresh benchmark。
HTTP 测试装配和旧原文 TTL 迁移已补齐，集中回归 1000 passed、0 skipped。
真实摘要质量、独立新上下文复核和 fresh benchmark 尚未完成，不恢复 verified closure。

依据：target-chain-convergence-audit-2026-09-07.zh-CN.md。用户已授权实施；迁移后不保留旧的可运行入口或自动回切。保留工作树无关修改。

2026-09-07 续作顺序：先补压缩原文生命周期及评测材料，随后严格回到 1→2→3→4→5。
本次不先跑业务案例找补丁；按会话删除 Owner、执行诊断 Owner、SDK 适配边界、
回复/交互合同和恢复提交窗口逐项审查；集中运行验证放在设计与迁移之后。
压缩真实模型评测与最终验证同批执行，禁止把组件测试当成摘要质量成绩。

1. done：修正答案检查、证据覆盖、执行完成的权威与 API/trace/Publication 公开投影；删除 requirement→verified 推导。新增 target-outcome-contract.zh-CN.md 与状态组合测试。540 passed，12 PostgreSQL 相关测试因缺 TEST_DATABASE_URL 跳过；不宣称数据库验证完成。
2. implemented：统一结构化模型调用与错误诊断到当前 SDK，删除被替代协议入口及消费者，不新增第二套 provider 路径。
3. implemented：简化回复核验并统一交互/审批/部分成功的结果组合，保留精确操作授权。
4. implemented：核对续接、持久化窗口与过时证据规则，迁移相关消费者。
5. in_progress：性质测试与集成测试 1000 passed，包含真实 PostgreSQL 进程恢复；真实模型质量与独立复核仍待完成。

每个完整可验证阶段单独 commit/push。不得把第一阶段完成等同全部收敛；不得以未通过验收的旧链作为自动 fallback。

## 续作实况（不恢复 closed 状态）

- 状态/诊断：模型异常在 SDK 调用边界标记 stage/type/retryable；程序适配错误不冒充 Provider 故障。
  API 使用稳定错误码；工具与执行诊断保存在 AgentResult.execution_feedback，不再解析可压缩消息。
- SDK：规划、合成、恢复决策、答案评判及其评测消费者改用同一结构化 SDK 调用；
  删除旧 local_planning_client 和两条被替代实验脚本，不保留自动回切。
  callback 装配收敛到 TurnRuntime 根调用；生产启动集成测试覆盖此接线。
- 回复：普通对话不强制事实核验；业务答复的语义评判与任务完成、证据覆盖分开。
  审批仍需精确动作及已核验说明，已完成结果不因后续失败丢弃。
- 恢复：单审批槽位串行派发 action-capable work，保留尚未启动的排队任务；
  恢复校验原目标、Registry、revision。集合规范化归 AgentResult，不在 Orchestrator 补字段。
- 已运行集中回归：208 passed（包括 PostgreSQL 子图进程退出恢复）；扩大到 996 项时，
  992 passed、4 failed。稳定错误码和诊断反序列化两项属于本次迁移缺口，已修复并针对合同回归。
  另两项 HTTP 测试在独立导出的修改前 HEAD fe3a5af 中同样失败：缺少当前回复/审批核验装配。
  尚未以这组旧测试证明全部 HTTP 业务闭环；不得将它们忽略后声称全绿。
- 剩余：统一这两项 HTTP 测试的生产装配证据、真实压缩质量/成本评测、
  旧原文保留期限迁移、独立新上下文复核与 fresh benchmark。实现与 verified closure 分开。

### 本轮验收更新

- 上述两项 HTTP 缺口已修复：测试显式装配现行 ResponseAssembler、核验和知识生成端口，
  知识与退款 fixture 返回当前业务合同；审批、副作用、防重、跨会话拒绝及对账断言保留。
- 新 HTTP 消息的无类型编号先由语义层绑定，测试不再假定它已具备 order_id 来源。
  明确绑定后的 Encoder ACCEPT 行为继续由 Encoder 专项测试验证；生产快速路径未改动。
- PostgreSQL Store 启动迁移旧原文 TTL，按原更新时间计算；SDK 继续负责清扫。
  真实 PostgreSQL 测试验证重复启动不续期、过期原文不可回读、其他命名空间保留。
- 当前工作树集中回归：1000 passed、0 skipped、4 warnings，91.51 秒。
  警告为 SDK thinking/forced-tool 提示及测试 fork 弃用提示，不隐瞒为零警告。
  脚本模型 HTTP 测试证明合同与接线，不作为真实语言质量成绩。
- 当前剩余：真实压缩质量/成本评测、独立新上下文复核、fresh benchmark。

最终续作验证：工作树 999 项中 997 passed、2 failed（上述既有 HTTP 缺口），无跳过。
将待提交 index 独立导出，245 项相关回归全部通过，包含真实 PostgreSQL，未依赖未暂存文件。
真实 DeepSeek 摘要开发冒烟 1 条，工作视图估算 2960→448 tokens；最新批次与原文引用保持。
Langfuse observations 已回读 IO、模型及 token；语义查看发现条件可能被概括得更强，
故不计为正式质量通过。细节与 trace 链接见压缩文档。
