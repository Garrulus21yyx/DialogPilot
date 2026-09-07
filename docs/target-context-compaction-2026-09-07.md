# Target 工作上下文与工具原文

本次收敛的是领域 Agent 的工作上下文，不替换正式 Transcript、Thread Summary 或业务 Receipt。

## 责任与数据流

ToolManager 返回完整结构化结果。共享 ToolResultPersistence middleware 先把正文和 artifact
写入 LangGraph Store；生产装配使用 AsyncPostgresStore，与现有 Checkpointer 共用生命周期管理。
模型消息携带完整的小结果，或明确标记不完整的预览与内容哈希引用。
read_tool_result 按页读取原始快照，不重新调用业务工具；租户、用户、会话、领域和目标作用域来自可信运行上下文。

工具结果索引保存在图状态 tool_observations 中，不随 messages 的清理或摘要删除。
AgentResult 从该索引读取原始 artifact；审批准备状态也读取索引，不再依赖历史消息的位置或长度。
恢复时的大 Fact 值同样投影为可读引用，完整 Fact 不被改写。

## 两个触发点

- 工具返回时：超过单结果输入份额（当前为可用输入预算的 1/5）先保存原文，再投递引用。
  这属于大结果的呈现，不是删除最新工具结果。
- 每次模型调用前：估算完整消息、系统内容和工具 Schema。达到软线后用 SDK ClearToolUsesEdit
  清理旧工具正文；重新计量后仍达到摘要线，才用 SDK SummarizationMiddleware 摘要旧消息。

70% / 85% 是可配置的实验初值，不是已证实的 Claude Code 常数。
可用输入预算已扣除输出预留和协议余量；近似计量不是供应商精确计数。
最近一次模型工具批次的调用及全部返回作为整体保护，当前任务输入在摘要后保留。
摘要和清理只改变工作视图，原始消息先存入 Store，摘要附回读引用。

SDK 负责消息配对、摘要切分、图更新和恢复。项目仅补原文作用域、最新批次保护、预算及失败语义。
已删除无生产消费者的 fit_messages 自写裁剪，以及 ToolManager 的头尾字符截断及旧配置。
当前锁定版本 SDK 会将部分摘要异常转成摘要文本，因此薄子类让异常传播，避免错误文字替换历史。

## 有界失败

- 原文保存失败：当前原始 ToolMessage 留在 checkpoint，停止执行段并报告 RESULT_ARCHIVE_UNAVAILABLE。
  不把内存当成可继续服务的第二套持久存储，不盲重试已执行工具。
- 摘要失败：原消息保留，执行段明确失败，不递归摘要或吞掉异常。
- 清理后仍超出输入预算：CONTEXT_BUDGET_EXCEEDED，不发送必然溢出的模型或摘要请求。
  当前不支持将超窗历史分成多次摘要调用；这不是只会由最新批次过大触发的失败。
- 摘要不收敛：有界失败；不通过无限循环清空任务。主模型调用和摘要调用分别有预算，
  摘要上限绑定 WorkItem.max_steps；不能将该数字表述成两者合计的调用次数。

## 验证与剩余边界

测试覆盖分页还原、作用域隔离、多工具批次、摘要后当前输入保持、保存/摘要失败、
清理后独立结果索引完整性、大结果回读不重复执行，以及 PostgreSQL 关闭重开和进程退出后的恢复。
使用脚本模型验证控制合同，不等于已验证真实模型的摘要准确率。

仍需真实长会话验证否定条件、金额方向及引用保持，并校准阈值和成本；
Store 原文按 TARGET_RESULT_TTL_MINUTES（默认 30 天未访问）保留，使用 SDK TTL 清理，
到期未清扫的记录也不允许回读。正式 Receipt 不跟随该 TTL 删除。
会话删除由现有删除事件驱动原文清理；生产原文读写读取同一删除标记，
对删除期间完成的迟到写入清理后拒绝，避免删除后重新生成可见原文。
启动时由现有 PostgreSQL Store 适配边界为 target-originals 命名空间中的旧
ttl_minutes=NULL 记录补齐期限，expires_at 基于原 updated_at 计算，重启不延长保留期。
后续到期判断与清理由 SDK 负责；其他命名空间、正式 Receipt 不受影响。
迁移将在部署后启动时应用；已超过保留期限的原文会不可读并由 sweeper 回收，
不能据本地测试声称运行中环境已经完成历史清理。
因此本阶段不宣称全链路 verified closure。

提交快照回归共 171 项：170 通过，1 失败，没有跳过；包括真实 PostgreSQL 与进程退出测试。
DIRECT media 的最终回复测试仍失败；相同测试在修改前 HEAD 81dae30
独立导出的源码上原样失败：OCR 已完成，但发布的是整理失败说明，而测试预期原始 JSON。
该回复合同问题仍属于上一阶段全链路审核，不通过弱化断言纳入本阶段的通过证据。

续作：DIRECT media 测试已接入实际要求的 ResponseAssembler 与核验端口，
保留 OCR 调用次数及统一 Publication 断言，改为验证用户可读回复，不再要求原始 JSON。
原文删除竞态、范围隔离、部分清理失败重试、真实 PostgreSQL TTL 及删除 outbox 已纳入回归。
执行诊断独立于工作消息持久化；AgentResult 自身规范化集合类型，Checkpoint 回读不改变结果。

### 真实模型开发冒烟（2026-09-07）

一次 DeepSeek v4 flash 调用，沿用生产模型工厂和 ContextCompaction；原文 Store 使用
InMemoryStore，仅测试摘要语义和视图变换，不作为 PostgreSQL 持久化证据。
输入是固定的未完成换货背景：只授权资格查询、不授权提交，旧价 262.47 USD、
新价 249.01 USD、应退 13.46 USD，支付引用 pay-42，等待选择颜色；附 160 次重复只读检查说明。
最新 lookup 调用及返回单独构成受保护批次。可用预算 4200，overhead 100，软/摘要线 .5/.65。

- 估算工作上下文 2960 → 448 tokens（包括保护内容及引用，不是供应商用量）。
- SDK 实际摘要调用：input 2062、output 156 tokens、latency 2.092 秒。
- 人工查看输出：只查/不提交、两项价格、退款方向、pay-42、待选颜色及未写入均保留；
  最新批次逐消息保持，完整历史可按 hash 回读。
- 输出另有“选择颜色后才能进行任何进一步动作”的概括，可能比输入的待选条件更强；
  因此不将该样本标成独立质量评判通过，更不据此宣称长会话准确率。
- [本次摘要 trace](https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/traces/b3fa94cd36d8a49b22823bb8b4d3b871)
  已通过官方 CLI observations 接口回读，确认 GENERATION、模型、IO、用量及 development 环境。

固定样本不是 heldout；生产默认 .7/.85 未做成本最优校准。正式质量评测仍待多样本、
独立评判和否定/条件强化错误的量化，不能以这一次压缩比替代。

评测合同迁移：dialogpilot-500-v1 的两个 provisional dev 输出预算用例改为
“输出外置且原文可还原”，替换旧“输出被字符截断”断言；manifest 升为
1.0.1-provisional-context-contract 并更新校验和。不是 heldout 调参，也不与旧分数直接比较。
