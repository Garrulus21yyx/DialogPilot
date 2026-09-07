# 审批问答链路修复与审查

状态：implementation_verified_by_targeted_tests / e2e_verification_open。不是全项目验收通过报告。

## 两个缺陷与共同根因

开发轨迹中的动作提案被 `InteractionBoundaryMiddleware` 当作结束模型循环的信号；`TurnRuntime` 又跳过 WAITING_APPROVAL 的回复组织，`TargetChatApplication` 用动作名和参数 JSON 拼出确认问题。结果是：提案后的信息询问未回答，用户看到内部执行参数。

正确归属：准备器负责尚未执行的精确动作；领域 Agent 负责继续查询和解释；审批状态负责绑定参数、版本和操作身份；现有 ResponseAssembler/AnswerVerifier 负责最终文案；Publication 负责提交。SDK 负责工具协议和循环，不会自动生成经过业务核验的客服确认说明。不存在需要新建审批引擎的理由。

## 本次修改

- 提案不再终止模型循环；仍可读取工具并生成普通候选。每个片段一个待审批动作，额外/重复提案通过现有 middleware 反馈，不执行该批工具。已有提案与证据不会因后续模型失败丢失。
- 待审批动作和候选回复可以共存。既有 Flow 的持久审批状态与领域提案都进入同一回复组织路径，不按工具名、商品类型或基准任务写展示分支。
- 确认说明使用原始事实与待执行参数核验；必须说明重要条件、未执行状态并请求批准。只有已核验文本才携带 approval_operation_key。参数仍从持久审批绑定读取，不从自然语言反向解析。
- 纯礼貌语/无事实断言的提问可用 NON_FACTUAL；含金额、对象、付款方向的确认问题仍需拆出事实核验。这是文本核验的语义分类，不是按问号放行。
- 应用移除动作名 + JSON 的客服文案。核验失败保留原待审批状态并交付安全回复，不发布可批准的确认请求；后续轮次可生成同一待审批动作的说明，查询既有 Publication 避免重复生成审批消息。
- 审批消息复用普通回复已有的事务内目标版本校验。未增加新授权存储、checkpoint 或消息系统。

## 验证范围

最终定向组合 192 项通过（真实 PostgreSQL；涉及框架循环、审批/拒绝/续接、Publication、工作控制、回复核验、状态持久化）。新增测试还覆盖：提案后读工具、重复提案、后续模型异常、Flow 与领域两种来源、核验失败后下一轮补发、文本变更拒绝。

新增验收不仅断言文案：同时断言没有写调用、原操作绑定保留、准备读取不重复、核验输入含准确参数、批准信号不因核验失败而发布，以及数据库发布边界拒绝无效目标版本。语义核验测试使用受控判决检验应用合同，不冒充真实模型正确率。

## 真实开发任务：未通过，不能收尾

运行目录：`artifacts/eval/tau3-dev1-2026-09-07-approval-v2/`；retail train task 0、seed 300、completion budget 4096。沿用配置模型，没有替换供应商或增加基准特化规则。运行时工作树未提交，之后继续简化了审批核验要求文案；本记录不等于最终提交的可复现成绩。

- 官方 reward=null，状态 ERROR；不是 reward=0，也不是成功。
- 5 个 Target 轮次；第 1、3、4 轮回答核验返回 UNKNOWN / invalid_contract / ValueError，降为安全回复。
- 第 5 轮返回 conversation_provider_output_invalid；τ³ 适配器停止。没有进入本次修复的动作提案审批阶段，因此不能据此判定审批已实测通过。
- 部分候选含明显应核查的额外承诺，例如从“一次换货调用”推断“一次退货和一次补发”。不能为了让任务跑完跳过核验。
- 现有记录没有保留这些 ValueError 的具体合同位置，无法在此证明是工具输出结束条件、Schema、跨度覆盖还是证据引用导致。仅凭 invalid_contract 不能归咎于模型，也不能放宽校验。
- Langfuse 官方 observations CLI 回读本次 session `tau3-0417c0bf1356403282cac0eccfb0ce98` 返回 11 条领域模型 GENERATION；本次 session 下未见 planner/composer/verifier generation。核验判决来自评测 Target trace，不能称全模型链路已在 Langfuse 贯通。

## 全链路审查：保留什么、还缺什么

| 边界 | 判断与下一步 |
|---|---|
| create_agent / LangGraph | 当前 Target 已复用模型—工具循环、消息对象和 checkpoint。本次不新增第二套循环或 HITL 引擎。 |
| ToolRuntime / Skill / 业务审批 | 权限、身份注入、operation key、Receipt 是业务责任，应保留；工具参数序列化用于协议和存储是正常的，不应把所有 json.dumps 都删除。 |
| DomainResult → 回复 | 提案截断和候选丢弃已修；普通文本中问问题却没调用 request_user_input 时，不会自然获得 PendingInteraction。后者仍是开放合同，不能用关键词补丁解决。 |
| 并行提案 → 会话审批 | `bind_action_approval()` 当前取首个提案；已有其他 checkpoint 的审批时直接返回原状态。单审批容量与多提案输出之间尚未形成明确的排队/冲突结果，不能声称已完整支持多动作并发审批。 |
| 明确审批 + 尚待补充输入 | Resolver 当前阻止审批决定越过 pending interaction，包括拒绝。取消/拒绝是否应先行需要明确状态转换合同，而不是继续加优先级分支。 |
| compose / verify | 复用已有组件；仍有多个手动结构化输出解析入口以及缺少具体错误原因的问题。先保留可追溯的类型化合同错误，再判断能否统一到已锁定 SDK 的结构化输出能力；不能靠新增重试或更宽解析掩盖失败。 |
| 核验 → API/评测 | API 当前主要由 requirement coverage 推导 verified/grounded/complete。安全弃答可能被标成完成；应分开“文本可安全交付”“信息需求已满足”“业务操作已完成”。本次未改写这些公共字段以免把接口迁移混入审批修复。 |
| 内部模型观测 | 领域 Agent 已有官方 Callback；planner/composer/verifier 的原生调用在 core.llm_metrics 中主要记录 usage。需接现成 SDK/OTel 集成及同一 session，不能再写正文采集器。 |
| 持久化/重放 | 现有业务审批与框架 checkpoint 继续分工；没有发现需要再造 SQLite/内部 resume 的理由。本次定向测试不是崩溃窗口全覆盖证明。 |

最小后续收敛顺序：核验错误可追溯与全模型观测 → 核验真实失败原因及结构化输出适配 → API 完成状态口径 → 多提案/交互共存的明确合同 → 新鲜任务与独立复核。没有独立新上下文复核和新鲜 E2E 通过前，维持 verification_open。

参考：2026-09-07 核对的 [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。框架提供暂停与决策机制，不提供本项目的业务事实或客服语义保证；本项目未获得同预算基准上的 SOTA 证明。
