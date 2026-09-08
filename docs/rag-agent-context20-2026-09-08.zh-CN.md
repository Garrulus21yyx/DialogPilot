# 真实上下文读取与Conversation Agent：20题诊断

2026-09-08，61d1b48之后。固定此前Doc2Dial100的前20题，未按本次结果选样。Flash/NONE/2048，每题一次、0重试，20次全部有效，无API/协议失败。未改生产提示或检索策略。

## 实际经过什么

官方test archive逐项核对角色及历史原文 → 隔离真实Redis → MemoryManager.project_working_message/project_thread_summary → MemoryManager.get_current_context → TargetTurnContextLoader默认8条 → EntityBindingResolver → ConversationAgent.plan → 当前原生动作provider → 实际query同预算本地检索。

使用的确实是项目自己的MemoryManager、摘要阈值、上下文加载器、预算与Agent代码，不手动把全历史放入TargetTurnContext。存储边界仍有明确夹具：PostgreSQL投影watermark用适配器替代，跨会话ServiceEpisode库为空、没有旧知识EvidencePack，不执行manager工作流、子Agent、生成/核验/发布。不是全生产入口端到端验收。

20题真实输入均为最后8条历史消息，模型payload逐项核对一致。摘要并未触发生成：MemoryManager返回 `{"chunks": [], "covered_until_seq": 0}` 空容器，不能将“20个summary字段”当成“20份有效摘要”。本次没有任何跨会话检索触发，故只验证触发结果为否，未验证memory命中质量。没有人工摘要、人工query或gold输入模型。

## 结果需分开看

| 第一阶段动作 | 数量 | 解释 |
|---|---:|---|
| 直接knowledge_search单query |8|全部候选完整，最终7/8完整 |
| 直接回复/追问 |9|没有本轮检索query，不自动等于9个业务错误 |
| 委派general领域Agent |3|本实验未执行，不能当作完整链路失败 |

在同20题、同488篇/1469块/两路各20/.5RRF/CE/5块2600预算下：

| 输入方式 | 最终完整证据覆盖 | MRR | nDCG |
|---|---:|---:|---:|
| 原句直接检索 |9/20|0.2792|0.3227|
| 人工历史完整query |20/20|0.7117|0.7845|
| Agent第一阶段直接query（其余计尚无证据） |7/20|0.2075|0.2418|

最后一行是第一阶段直出query的证据产出量，不是Agent端到端准确率。3个委派未执行，也没有给直接回复做完整答案评分，不应与人工query结果称为最终答案对照。条件子集8题的最终7/8=87.5%，也不能外推全20题或线上。

## 具体观察

1. 6题RESPOND给出了实质性服务/政策说明，但没有本次检索或已有来源pack，包括驾照撤销、FAFSA4caster信息、地址查看、IRP代办等。说明它能理解话题，却选择直接回答；未证明这些句子全部事实错误。
2. 1题最新消息“No, I'm active now”，模型又问是否已于2003年出院/退役。原问题和回答在实际输入中存在，是理解/继续对话的问题，不能归因于历史没加载。
3. 另2题分别是用户陈述指纹办理要求、询问能否再问问题，追问或邀请继续对话可以合理，不应为了benchmark一律强制检索。
4. 3题委派为幸存者养老金、修改SS地址、National Call to Service福利。需要执行子Agent才能评完整链路，本轮预算止于主Agent第一次规划。
5. 8条query中唯一最终遗漏是被盗车牌：query省略Empire Gold，完整证据已进候选20但未保留至前5。省略是否导致排序损失，尚未作单因素对照，不能直接定因。

上下文还有确定性边界：MemoryManager读出未压缩消息，上层加载器只保留8条；若未到摘要token阈值，早于8条的内容并没有摘要补偿。本次该边界真实存在，但当前多数不查例子仍能说出主题，所以不能把全部行为失败归于它。

## 检查与费用

20次Flash，记录input_tokens124965/output_tokens1757；provider latency总计约28.7秒（非整轮耗时或p95）。检索新增本地CE160对。来源hash、20条窗口、模型实际历史、query评分原文和最终pack/wire审计全部通过。

首次预检误用validation角色索引、第二次误写working投影方法名，均在API之前失败，已修正并记录，未消耗模型调用或丢弃有效结果。原日志预检说明保留，不计为模型失败。

产物：`artifacts/eval/rag-agent-context20-2026-09-08/`。`captures.jsonl`保存实际上下文、动作和完整provider回调；manifest保存源码hash；retrieval/scores/report/audit保存配对证据。复现脚本run_rag_agent_context20.py、evaluate_rag_agent_context20.py、audit_rag_agent_context20.py，使用PYTHONPATH=.与.venv/bin/python，前两者拒绝覆盖已有有效结果。

## 下一项边界

本步已完成用户要求的真实上下文读取与主Agent第一步诊断。下一项围绕取证选择及委派路径验证，不继续换chunk或调权重；真实领域Agent须使用同一上下文合同。区分需要来源的服务事实与普通交流，避免一律强制tool而伤害正常对话。未在本轮修改或宣布该行为修复。
