# Target 主链单路径收敛实施

## 当前主线：恢复原始 τ³ 两任务闭环

### 本次续作：审批回复合同与参考项目对照

- implemented：审批说明不足由 AnswerVerifier 输出可修订的 REJECT，不由 ResponseAssembler
  抛异常绕过已有的一次修订；合法审批不放宽授权，核验失败仍不发布。
- implemented / model_quality_pending：在既有回答质量判断中明确用户语言与面向用户表达，内部自述不算有效答复；
  不增加正则删句、第二个审稿 Agent 或模型循环。
- 意图对照：当前为上下文 LLM 规划＋轻量分类快速路径；截图为检索辅助 LLM 分类。
  本次不接新意图层，不改成单 Agent，不把示例编号或批准状态导入当前用户事实。
- 后续基于同数据同预算比较可选意图案例检索；工具健康治理和缓存收益另作有界实验，
  不与当前审批/发布缺口捆绑改造。
- 验证：核验/表达/合同 75 passed；审批交互/真实 PostgreSQL HTTP/Manager 53 passed。
  包括所有 supported/answered/pending/terms 组合、一次修订及再次拒绝不放行。
  本次未重新跑真实 τ³；不声称已解决模拟用户 STOP、重复确认或中文最终兜底。
- 参考：四路线页面 https://garrulus21yyx.github.io/agent-systems-atlas/pages/intent-recognition-four-routes.html
  与 LangChain 官方 multi-agent / human-in-the-loop 文档（2026-09-07 查阅）。
  前者是截图整理的技术路线说明，不是优劣实验；后者支持按需组合而非每轮强制多 Agent。
- 简化取舍：保留唯一上下文与语义入口、SDK 执行、业务审批和凭证；
  统一审批拒绝的含义，复用已有修订路径；不增加微服务或新的语义分诊层。

- in_progress：以 train 任务 0、1 和原 max_steps=80 / completion_budget=4096 重跑当前主链。
- 上次 native-replies-v1 两条均为 ERROR（未成功发布），official_reward=null，不冒称官方评分零。
- 验收：官方 ALL/ENV/ACTION 结果、实际调用与最终业务状态、跨轮追问续接、Publication。
- 只修实际阻断闭环的 owner-level 根因；压缩语义疑点归观察项，不另开专项。
- 不改变模型/任务/预算来掩盖失败；运行在独立测试数据库，不访问生产业务数据。
- pending：两条原任务验收后再测未用于修复的新任务；开发通过不等于整体准确率。

### τ³ 续接修复证据（业务验收仍 in_progress）

- v1 两任务分别在规划合同拒绝和审批恢复异常退出，reward=null；diagnostic-v2
  任务 0 官方 ALL/ENV/ACTION 均为 0（未提交换货），任务 1 为用户模拟器无有效输出，reward=null。
- 连续追问的共享根因已由 PostgreSQL 测试复现：Turn-local work_item_id 被用作 SDK 消息 ID；
  第二次恢复复用 ID，add_messages 原位替换旧提示，新输入没有进入消息尾部，旧交互再次结束执行。
- 修复 Owner 为 TargetFrameworkAgent 的消息输入边界：采用已有 WorkItem.fingerprint 标识执行合同；
  新 revision 追加消息，相同合同重放保持稳定身份，不修改 SDK reducer、不新增恢复运行时。
- 正向验收覆盖 1/2/3 次输入恢复、重新打开 PostgreSQL checkpoint、每轮实际进入模型、
  提示身份唯一、旧工具证据保留、已完成读取只执行一次。修改前 1 passed / 2 failed，修改后领域测试 23 passed。
- 相关回归 102 passed；τ³ 适配测试需额外加载官方评测环境，单独运行，不把缺包 skip 当通过。
- 原任务同预算重跑目录：artifacts/eval/tau3-main-dev2-2026-09-07-resume-v3/。
  当前仅证明恢复组件修复，不宣称换货闭环或整体质量已通过。

### 写操作恢复边界（继续保持业务验收未闭环）

- resume-v3：任务 0 已通过连续追问，在关闭带排队任务的等待点时异常；任务 1
  官方得分 0，用户模拟器把确认和 STOP 放在同一消息，写操作没有执行。不修改官方终止规则。
- 等待关闭的原合同只清空 ready_items，没有为未启动任务形成结果。现给未启动任务
  CANCELLED/EXECUTION_WAIT_CLOSED，已返回结果、事实与 Receipt 保留；不撤销远端操作。
  测试覆盖排队数 0/1/3、内存/真实 PostgreSQL、重复关闭、已成功的独立任务保留。
- Alembic 默认 fileConfig 禁用了既有应用 logger，遮蔽真实异常。使用 SDK 参数
  disable_existing_loggers=False；重复迁移后实际错误日志可送达，未新增日志框架。
- wait-v4：两条任务均 ERROR/reward=null；恢复后的 WorkPlan=None。保留下来的完整日志证明
  SDK 允许类型清单遗漏 ApprovalPolicy / ActionReconciliationDefinition，嵌套构造失败导致计划丢失。
  在 checkpoint Owner 注册两个既有类型；不在调用者用空字典/空计划掩盖损坏。
- 新增 ACTION/WORKFLOW × 全部五种 ApprovalPolicy 的 SDK 往返测试，同时检查计划 fingerprint
  和 AgentResult.pending_action。修复前 10 failed，修复后 10 passed。
- 编排及 PostgreSQL 基础回归 60 passed；架构合同 20 passed。仍需原任务的官方业务验收。
- 已知恢复失败的共同验收缺口：只测试一次正常执行或单次恢复，没有覆盖连续输入、
  写合同嵌套类型、排队与关闭的组合。修复分别归消息身份、执行终态、checkpoint 注册的 Owner；
  不将它们扩大为新框架，也不以单条案例通过恢复 closed 状态。

### 固定任务最终复测：checkpoint-v5（提交 ae00d85）

| 原始开发任务 | 官方 ALL / ENV / ACTION | 实际换货调用 | 结果 |
|---|---|---|---|
| 0 | 1 / 1 / 1 | 1 次，两个商品，参数及数据库最终状态正确 | 业务评分通过；回复质量待修 |
| 1 | 0 / 0 / 0 | 0 次 | 模拟用户在确认消息中带 STOP，官方运行器终止前未向应用投递该确认 |

- 两任务均 EVALUATED，无 target_runtime_failed；max_steps=80 / completion_budget=4096、模型、
  任务、官方终止与评分规则未改。不得把这两条开发样本写成泛化准确率，也不重跑择优。
- 任务 0 第 7 轮留有已提交 Receipt，但使用中文兜底；第 9 轮出现内部分析式措辞混入用户文本。
  官方案例没有自然语言断言，业务得分 1 不证明这些回复合格。质量缺口仍打开。
- 任务 1 的最后确认没有进入 target_trace；必须区分模拟器提前终止与应用收到确认后不执行。
  同时，准备动作前的初步确认、真正的动作审批、审批期间追问的连续性仍需统一审查，
  不通过删 STOP、自动批准或放宽写权限解决。
- 相关扩大回归：227 passed / 2 skipped。两项为内存账本不适用的数据库作用域组合，
  真实 PostgreSQL 对应测试已运行；另 PostgreSQL 基础/编排 60 passed，架构合同 20 passed。
- 所有本轮 τ³ 运行的临时数据库与临时 Redis 已由 runner 清理；仅保留评测轨迹与错误日志。
- 下一项限定为审批/追问和用户表达的合同：从已保存轨迹检查谁拥有待确认动作、
  哪条消息是有效授权、哪些内容允许发布。先设计统一修复与正向验收，再运行固定任务；
  不按商品类别写分支，不调整模型预算换取通过，不继续深入压缩。
- 整体状态：in_progress；尚未完成两条业务/回复联合验收、fresh 样本及独立复核。

此前压缩子项：implementation_verified / quality_review_pending（观察项，不是当前主线）。
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
