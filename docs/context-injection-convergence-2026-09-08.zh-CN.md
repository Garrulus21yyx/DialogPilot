# 主规划与领域 Agent 上下文注入

2026-09-08，基点 `a34102c` 加工作区改动。**消息边界迁移已实现并完成协议/生命周期验证；真实任务质量没有全部通过，R01不宣告整体关闭。**

## 当前实现与所有权

`ConversationAgent` 继续拥有当前原文、历史选择、实体来源和预算裁剪，运行时继续拥有任务/审批状态。`infrastructure/target_model_context.py` 只负责模型消息投影，未新增规划分支、模型路由或状态机。

主规划通过现有 `AnthropicConversationPlanningProvider` 和 `structured_call` 调用：

```text
tools: submit_turn_plan 输出 Schema
system: 固定规划规则 + registry/知识能力合同（确定性排序）
messages:
  user: conversation_summary（仅摘要，稳定到压缩时）
  user / assistant: 真实近期历史，保留角色、顺序和原文
  user text block: runtime_context（状态、历史来源、watermark等）
       text block: current_request（当前原文，JSON转义）
```

历史内容不重复出现在runtime块；历史来源元数据按位置保留，capture可恢复原payload。未知历史角色及非文本历史在模型边界拒绝。JSON标签只是内容分区，不增加指令权限；摘要、历史与用户原文不进入system。能力合同由应用registry提供；身份、待审批和实时工作状态留在动态后缀。

领域 Agent 继续使用 `TargetFrameworkAgent` 的工作历史、工具循环、恢复、归档和压缩。原pinned task现在包含三个明确text block：

- `source_context`：原始用户消息与相关会话背景。
- `runtime_context`：事实、来源、依赖回执、待审批状态等。
- `delegated_task`：本任务objective、参数、要求及动作提议范围。

子Agent只完成委派目标，不能把整条用户消息的其他目标接管过来。压缩仍保护完整pinned task和最新工具调用批次；事实原文仍由归档保存。暂停恢复仍按现有控制修订和操作回执处理，不因输入格式变化重建任务或重复执行动作。

`structured_call` 统一接受原生消息序列。领域结果审核、claim verification、压缩评测和核验重放等现有调用者同步迁移；它们的审核任务内容不因此改写为会话历史。最终回答compose的证据组装合同未在本次规划/子任务迁移中改变。

## SDK、捕获和缓存

安装的LangChain Anthropic适配器会合并连续HumanMessage，保留独立text blocks。因此验收检查真实HTTP请求，不把Python消息对象数量当成供应商消息边界。`FrameworkCapture`仍保存SDK归一化前的消息，角色规范为user/assistant/tool，并保留tool_calls、tool_call_id及status。

`planning_payload_from_request`是现有捕获的审计/重放解码器：支持新布局和历史单JSON记录；不参与线上规划、不改历史产物，也不声称可直接解码任意已合并HTTP请求。通用规划重放与相关诊断读取者已迁移。

固定system与能力Schema放前，摘要两次压缩间不变，历史原文保持稳定；当前请求、审批、来源水位等在后。合法权限、Schema或摘要变化会使对应前缀失效，不能为缓存冻结过期事实。

当前DeepSeek兼容接口采用默认自动前缀缓存，官方明确忽略`cache_control`，本轮未添加无效缓存标记。缓存属于尽力提供，实际读取量独立记录。官方Claude显式缓存及缓存隐私策略仍属于既有provider cache gate的配置范围，本轮未承诺或启用新的Claude缓存策略。

参考：[DeepSeek缓存](https://api-docs.deepseek.com/guides/kv_cache/)、[Anthropic兼容字段](https://api-docs.deepseek.com/guides/anthropic_api/)。

## 验证结果与限制

- 主回归：452通过，21因未设置外部数据库跳过；包括消息保真、原生HTTP请求、稳定前缀、预算、Schema、审核、重放、审批和压缩。
- 随后使用现有测试PostgreSQL容器，为测试创建隔离数据库：94通过，覆盖任务恢复、审批往返、原生工作历史和压缩。与主回归有重叠，不能相加。
- 增补多内容块pinned task压缩性质测试后的边界套件：61通过、1数据库用例跳过。已验证各批次大小/尾部约束下，任务块、最新工具批次和归档原文保持一致。
- `git diff --check`通过；所有已发现`structured_call`调用者均迁移为messages参数。

真实模型先冻结8条主规划（旧开发6+新措辞2）及2条子任务，使用生产provider/agent。主8例均保留本轮主题或否定/指代；原weak术语得到术语相关澄清，没有再次查alerts。**这不是8例完整任务成功**：当前并行工作区新增respond输出分支，Discovery出现未经检索的直接建议，若干通用政策问题继续索取订单号。本批不能与旧版Schema做纯呈现因果比较，也未验证检索到答案的完整链路。

子任务首轮合成证据缺来源字段，仅得到INVALID_EVIDENCE后触达步数限制；已保留。修正夹具后首例结果落盘因datetime失败，未保留调用明细，按最多4调用计，不将其算通过。保存修正后每次模型结束即独立落盘：

| 子任务 | 结果 | 解释 |
|---|---|---|
| 充电盒保修，原文另含取消订单 | 3调用，SUCCEEDED | 检索保修，引用合成政策的12个月；未接管取消目标。候选回复含内部任务说明，不能视作用户最终发布文案验收。 |
| 拆封试听、无质量问题退货，原文另含物流 | 2调用，TERMINAL_FAILURE | 实际可见证据完整，query保持委派范围，但继续扩大检索，4个工具调用触及预注册3步上限。不是历史串题，也不能称完成答案。未增大阈值或重跑此语义失败。 |

保留明细的18次调用共58,982输入tokens，其中31,744报告cache_read；输出2,006。另一次丢失记录最多4调用，不能并入精确usage或命中率。原预注册累计上限29，保守实际调用上限22；不宣称速度提升或跨租户缓存复用。

证据：

- `artifacts/eval/context-injection-convergence-2026-09-08/`：初始manifest、8规划及无效夹具2子任务。
- `artifacts/eval/context-injection-domain-valid-2026-09-08/`：落盘失败的manifest，不算成功证据。
- `artifacts/eval/context-injection-domain-durable-2026-09-08/`：有效夹具2子任务、逐调用capture和完整结果。

## 交付状态

改动在工作区，未提交/推送/部署；原有及并行业务改动保留。本次证明的是模型消息边界、缓存前缀和恢复/压缩合同，不把结构修复等同于所有模型语义达标。下一项应冻结稳定的最终规划输出合同，再验respond与知识取证的选择以及代表性多轮任务；不回到JSON移位置或单例提示堆叠，不开启微调。

## 后续任务进展修复（工作区，基点 a8cea45）

本次继续收敛既有状态与投影，具体合同见[对话运行合同](conversation-turn-contract.md#partial-input-and-per-task-evidence-progress)。允许分次提交有目标归属的字段，保留已填值和来源；剩余等待升级版本，仅恢复输入与依赖就绪的任务。独立问题分次补答使用原检查点，已完成结果保留。审批仍按原动作/参数/版本消费，补充信息不产生新授权。

ResultBoard逐项覆盖和证据关联进入同一响应作者/核验上下文。知识工具的进展按来源证据项累计，换query、重新排序、诊断变化或重组旧证据不再重置停滞计数。来源内容/版本变化和一般业务工具结果变化仍算新观察。此处只判断新增观察；不把一次空检索等同于整题无答案，也不代替模型的语义充分性判断。

确定性主回归1104通过、25无DB跳过；另隔离Postgres91通过，两套有重叠。本轮API0；实现未提交或部署。既有真实模型的取证选择/预算失败仍保留，不能凭本次回归宣布总体语义质量关闭。
