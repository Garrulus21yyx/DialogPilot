# 归因修复与两条新 τ³ 开发任务

日期：2026-09-07。运行代码：89cf2a4。固定 train 任务 2/3，seed 300，各运行一次；不是 heldout 成绩，不择优、不替换任务。运行配置和源码指纹见 manifest.json。既有其他任务的 dirty 文件没有纳入本次提交。

## 结果

| 项目 | 任务 2：商品数量＋三件商品退货 | 任务 3：商品数量＋待发货 T-shirt 改款 |
|---|---|---|
| 模拟对话 | user_stop，5 次模拟用户调用均返回文本 | user_stop，5 次模拟用户调用均返回文本 |
| 实际写工具 | return_delivered_order_items，1 次 | modify_pending_order_items，1 次 |
| 写入结果 | #W2378156 → return requested | #W4776164 → pending (item modified) |
| 官方 ENV 严格重放 | 1；db_match=true | 1；db_match=true |
| 官方 ACTION 辅助评分 | 0 | 0 |
| 官方 ALL 总分 | 不可得，不是 0 | 不可得，不是 0 |
| 最终对用户表达 | 已办理退货，退款 $1,285.12 | 已改为紫色 polyester/S/v-neck，补收 $0.05 |
| 应用 task_completed | false：一个后续目标被标为 BLOCKED | true |

两条均在较早回复中明确回答“10 个可用 T-shirt 选项”。这与任务的自然语言断言一致，但仅是轨迹人工检查，不替代官方 LLM 裁判结果。两条均发生一次重复确认，不宣布完整客服体验通过。

### 为什么 ALL 没有分数

业务模拟已经完成并保存完整轨迹，随后官方 NLAssertionsEvaluator 调用默认 gpt-4.1-2025-04-14，因未配置 OpenAI 凭证抛错。原 runner 将模拟和评分放在同一异常边界，导致 task-N.json 标为 ERROR，并跳过后面的 ENV/ACTION。

原始 task-N.json、轨迹、manifest 均保持运行时原样。新增 task-N-deterministic-scores.json 从该轨迹调用原版官方 ENV/ACTION，strict_replay=true，在新的 benchmark 内存环境验证最终数据库；没有重新调用 DialogPilot、没有重办业务。文件绑定原始轨迹 SHA256。

没有偷偷换裁判模型，也没有用“返回一条成功文案”代替业务成功。ALL 依赖 DB 和 NL_ASSERTION；后者不可得，因此不计算总分。ACTION 为辅助诊断，不在这两个任务的最终 reward_basis 中：两条均少了一次参考轨迹中的 get_product_details(6086499569) 只读调用，其余列出的动作匹配，包括实际写操作。保留 ACTION=0，不为匹配参考调用而给生产逻辑补特例。

## 归因证据

前置提交 89cf2a4 已接通审批模型和真实领域 Worker 的 SDK callbacks，并在原异常转换边界保留安全 cause chain、代码位置、阶段、重试属性和目标身份；归档并行失败保留每个 tool call 的原因，成功证据不会因后续异常丢失。

Langfuse 官方 CLI 完整分页回查：

- 任务 2：477 observations / 5 页，未截断；审批 generation bfe890731461990b，包含 conversation_id、turn=4、approval_id。
- 任务 3：348 observations / 4 页，未截断；审批 generation e8720ffd35e52ba2，包含对应身份。
- 这些是原运行的 observation 数，不是模型或业务工具的唯一调用数。云端回查发现父子层创建不同 callback 导致重复 span，已在 LangfuseTraceSink 统一复用 SDK handler；由 LangChain 合并继承的 callbacks，不自写 span 去重器。原云端记录不删除、不改写。
- 新增 SDK 真实内存导出测试：两个并发父调用，各运行两次模型、一次工具；恰好导出四个 generation、两个工具 span，并保留父关系。
- 两条实际任务没有触发领域 API 异常；异常归因由受控错误测试验证，不将正常轨迹称为真实 API 故障演练。

云端会话：

- https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/sessions/tau3-9a6f29cc2bf64a12974a83bc77bf493e
- https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/sessions/tau3-87b5c74953d844b8bb1256f657ae2f22

## 仍需处理的业务缺陷（本轮不加案例特化补丁）

1. **重复确认**：领域 request_user_input 先问是否办理，用户明确同意；下一轮才创建正式 Action Approval，再问一次。两条任务都有同一机制，责任边界是信息追问与已准备动作审批的转换，而不是模拟用户没确认。
2. **完成状态语义不一致**：任务 2 写入后，领域模型调用 report_blocked，理由却是“已经全部处理，无需进一步操作”。工具忠实产生 BLOCKED，恢复链仍留下该目标，使 task_completed=false。云端保留了模型工具参数、AgentResult 和最终回复，可明确排除写入失败；是否存在目标重复恢复或完成出口误用，还需检查目标生命周期，不应把所有 BLOCKED 强制改成成功。
3. **金额成本统计缺口**：模拟用户模型调用正常，但 LiteLLM 对 deepseek-v4-flash 价格/供应商映射报错。token usage 有记录，不能宣称美元成本完整。这与官方裁判缺凭证是不同错误。

## 评分边界修复

运行后修复 runner：每个官方 evaluator 独立保存结果或原异常链；模拟完成与评分未完成分开。某一 evaluator 失败不再遮蔽其他结果，ALL 不可得时 official_reward=null，整体仍为 EVALUATION_INCOMPLETE。测试逐项注入 ALL/ENV/ACTION 失败，验证其余评分保留，不改变官方评分函数或参数。

最终针对性回归：112 passed，0 skipped，包含真实 PostgreSQL；一条既有多线程 fork 弃用警告。独立 reviewer 对评分隔离和 callback 复用均审查通过。新增回调去重在真实 SDK 内存导出中验证，未为验证这项修改重新运行上述业务任务。

本轮边界：完成归因链修复和两条新任务执行；不宣称所有业务缺陷修好，不用测试数量替代任务成绩。HTTP/SSE、多领域隔离、线上写接口原子性未由这两条任务覆盖。原始日志与完整模拟器调用保留在本地 artifacts；公开提交仅含可核查轨迹、评分、摘要与配置，无项目访问密钥。
