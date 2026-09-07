# Target 会话主链收敛：职责、恢复与验收

日期：2026-09-07。范围是现有 Target 客服主链，不重建 Agent 平台。

## 一条执行链

```text
Admission / 后台 Run
  → prepare_turn：上下文 → 状态续接 / Encoder / ConversationAgent.plan → Policy / Compiler
  → [PreparedTurn checkpoint]
  → execute_work_plan：提交已计划状态 → 现有 LangGraph WorkPlan
      DIRECT 原子工具 / DELEGATED create_agent / ACTION 或 WORKFLOW
      → ResultBoard → 计算追问、审批、完成等状态决定
  → [ManagedTurnResult checkpoint]
  → commit_turn_state：提交同一状态决定，关闭不再需要的执行等待
  → assemble_response：确定性模板，或 ConversationAgent.compose → 核验 / 至多一次修订
  → Publication / Delivery
```

借鉴相似客服项目的短链路、任务级工具权限和可复现实验，而不复制其部署组件。
不新增意图 LLM 分诊层，不强制多 Agent，不把每种商品写成 Skill。
知识和业务回答共用表达入口；知识检索提供 Evidence，不另启一个客服答案生成器。

| 职责 | 唯一负责人 | 框架与业务的边界 |
|---|---|---|
| 会话上下文与语义 | Context Loader / ConversationAgent | 一个上下文入口；只有未被状态或快速路径处理的请求进入全局模型 |
| 合法计划及状态决定 | ConversationManager / Policy / Compiler | 决定业务含义，框架不猜授权、状态或审批 |
| 节点、领域循环及恢复 | LangGraph / create_agent | 复用既有运行时，不另写循环或恢复引擎 |
| 工具与副作用 | ToolManager / 原业务服务和 OperationLedger | 保留身份、权限、目标版本、Receipt、未知结果对账 |
| 结果与公开表达 | ResultBoard / ConversationAgent.compose | 领域说明是内部上下文，不是事实证明，也不直通用户 |
| 正式消息 | Publication / Delivery | 核验后的同一文本和同一回复身份 |

## 本轮根因及正向合同

### 状态消费必须可恢复

旧 prepare 在等待上下文和模型前消费 pending；崩溃后只有 consumed ID，没有原续接决定。
旧执行还可能先创建审批或追问，再失去尚未保存的结果。

现在 prepare 只计算，保存原状态、计划、续接线程及有序状态转换。
执行前提交已保存的输入消费和计划接受；执行后的状态决定也先 checkpoint，再提交。
审批期限、追问绑定等决定从 checkpoint 重放，不在提交重试时重新生成。

提交使用现有 ConversationState CAS，无新表、通用账本或第二份会话真相。
当前权威状态必须精确匹配该决定的起点或某个已提交前缀；只继续其余转换。
不把“版本更大”视为成功，不覆盖其他轮次的并发修改。冲突是明确失败。

### 恢复同一计划不是再次消费输入

续接计划已被执行图接受后，外层可能尚未保存结果。
此时按 WorkPlan fingerprint 复用既有 execute/checkpoint 恢复：
完成或等待中的同计划返回已保存结果，未完成节点继续执行。
只有新的续接计划才使用 Command(resume)，保留原控制版本及依赖检查。

### 暂时故障不是完成结果

规划使用现有 SDK ModelInvocationError 的分类。
暂时故障抛出，prepare 节点保持未完成；有 checkpoint 时后台 Run 才可重试同 invocation。
永久错误、无效输出和预算不足明确终态，不通过重试重新读取同一失败快照。
无 checkpoint 的测试/嵌入装配不宣称持久恢复。

### 一个回答作者、一套核验

Target 删除独立 knowledge_generator 注入与知识/业务两套合成核验控制流。
单领域、多领域、知识、业务、部分失败和绑定追问共用 ConversationAgent.compose。
简单确定性结果可用模板，内部领域文字不作为模板或事实。

PendingInteraction 绑定真正等待的字段，requested_inputs 作为表达/核验上下文，不进入事实引用目录。
模型可用一个仅含 text 的段落提出问题，无须再选输入或等待状态标记。
仅对已绑定 NEEDS_USER_INPUT 任务免除重复 outcome 标记；PARTIAL、BLOCKED 和独立失败仍需说明。
核验器检查全部待补信息的语义、无虚构前提及最终文本；PENDING_ACTION 绑定尚未执行的动作。
审核耗尽和无效输出是终态，暂时调用故障才可随 checkpoint 重试；检查点本身不赋予重试资格。
追问不重复申请审批。独立知识服务不可用是一个任务结果，不提前截断其他任务的追问。
知识引用和来源有效性仍在最终候选提交前检查；无法核验不发布模型草稿。
知识库独立诊断和离线生成实验保留，不是另一条客服生产链。

## 升级边界

本轮 TurnRuntime / PreparedTurn 为 v2。恢复入口统一检查运行版本，不依赖恰好进入哪个节点。
旧在途 v1 缺少可重放状态决定，不能透明恢复。
遇到它明确返回 turn_checkpoint_version_unsupported，不回退旧引擎、不删除 checkpoint，
也不重新执行可能已经提交的写操作。部署前应完成或单独对账旧在途任务。
已保存业务 Receipt 和历史公开对话仍然保留。完成的历史结果允许只读重放，不再次运行节点。

## 验收与非目标

最新追问修正：305 项相关合同/集成测试通过（包含 PostgreSQL，无跳过），独立审阅问题已修复。
新增组合测试覆盖无标记追问、输入数量、语义判断、独立失败、修订错误分类及 SDK 输出解析。
这些测试使用脚本化模型检验合同，不证明模型识别漏问或虚构前提的准确率。
v10 真实任务失败及环境重试均保留，当前真实业务闭环仍 open；下面原收敛计数为历史阶段记录。

无模型验收覆盖：

- FIELDS 回复 × 上下文、计划、执行、提交后故障位置。
- 批准 / 拒绝 / 过期 × 同样的故障位置。
- CAS 每个合法前缀的重放、重复提交和无关状态冲突。
- SDK 暂时 / 永久失败在相同 invocation 的不同恢复行为。
- PostgreSQL checkpoint 和业务连接关闭重开后，复用原决定、保留已完成工具结果。
- 单一/多个追问与独立失败组合；引用、来源撤回、审批说明和事实支持检查。

合同测试和独立审阅证明的是所列实现行为，不是模型准确率。
本轮扩大回归 1093 passed（一个既有 fork 警告）；恢复入口门禁补齐后，
状态/恢复/应用组在真实 PostgreSQL 上另跑 95 passed，无跳过。
独立 fresh-context 审阅在本轮范围内未发现剩余具体阻断。
真实模型评测等待本轮审查、实现、回归和独立复核完成，再按原固定任务运行；
失败和模拟器异常分别留存，不挑选最好分数。

本轮不增加意图案例检索、工具自适应平台、MCP 部署、商品专用规则或新 Memory。
后续分别用有/无案例检索、工具故障注入做对照，不能与当前业务闭环成绩混报。

参考：LangChain [Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
（2026-09-07 核对）：主 Agent 管理对话、子 Agent 隔离任务上下文并返回结果。
这是成熟职责划分的参考，不是 SOTA 或业务成功率保证。
