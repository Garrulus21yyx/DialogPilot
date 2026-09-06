# 事实支持核查与修复方案（待实现）

## 观察与边界

当前混合入口由ResponseAssembler组织allowed_claims，ConversationPlanningProvider.compose以SYNTHESIS角色（Pro）输出text+support_ids，服务端恢复来源，再由AnswerVerifier（Pro）判断发布。它不是缺少synthesizer；引用绑定验证不保证text可由绑定来源推出。

固定退款NO_APPLICATION事实、同一问题的6个开发变体，现有核验3条支持表述全通过；3条不支持表述中，只拒绝“已经无权退款”，放行“权利不受影响”和“仍有权退款”。false acceptance 2/3，仅该小样本，不是线上比例。原始事实及field_semantics完整进入生成和核验。核验理由把“仍有权”判断成符合语义，证据表明是判断错误，未证明其模型内部原因。

## 正向目标合同

每个最终事实结论都能回到实际原文或结构化事实；结论的主体、时间、条件、否定、范围和确定程度不得超过来源。没有相反证据不等于有正面支持。范围内无记录是观测，不是业务权益判定。

保留现有Agent/工具/assembler。建议：
1. 业务事实owner提供明确事实及解释边界；解释说明不作为新的权益事实。对金额、状态、执行回执等闭合字段，由owner模板输出可信表述，组合层处理顺序和必要连接。模板覆盖既有支持状态，未知走typed unknown，不按个案字符串补丁。
2. 开放政策答案仍由synthesizer生成已有segments。证据标识由系统绑定，不能把“合法引用”当成语义证明。
3. verifier逐项输出claim文本及最终答案位置、支持证据/字段定位、supported/contradicted/insufficient。一segment多断言要拆开，全部事实跨度必须覆盖，不能只验证模型挑出的容易断言。返回缺项或非法位置fail closed；自然语言拆分本身仍需评测。
4. 支持性、问题覆盖、相关性分别判断，避免有依据但答非所问。用户所需但无依据的部分明确表达未知；旁支不是必须展示的上下文。
5. 不支持的附加结论局部删除/改写；所需结论缺证据时有界补检索或追问。最多一轮修订起步，修改后的整份最终答案重新核验；事实时效与权限仍由原发布门检查。不要重跑整个业务链或重复写操作。

首步离线对比现有整段verifier与逐断言verifier，冻结事实和候选答案。开发集加入肯定/否定/未知、时间范围、金额单位、政策例外、跨订单引用等变体，封存另一批用于验收。指标同时报告无依据放行、正确答案误拒、断言覆盖、需求覆盖、修订后正确率、调用数及token/延迟。六例不能充当上线验收。暂不更改默认模型；先验证判定协议是否有效，再考虑本地专用检测器承担初筛。

## 当前原始资料（2026-09-06查阅）

- RT4CHART（2026-07修订预印本）：逐claim局部到整体核验，区分entailed/contradicted/baseless并定位答案与证据跨度。其测试集最佳结果不能外推为中文电商SOTA。https://arxiv.org/abs/2603.27752
- Beyond Document Grounding（2026-07预印本）：工具输出/结构化文档的跨度幻觉检测，说明评测不应只覆盖自然语言政策。其本地专用模型结果不能直接推定适用于本项目。https://arxiv.org/abs/2607.00895
- SURE-RAG（2026-05预印本）：区分证据充分性和自然幻觉检测；不同任务上排名会逆转，不能以一个分数决定部署。https://arxiv.org/abs/2605.03534
- AWS contextual grounding文档：分别评估grounding与relevance。https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html
- AWS Automated Reasoning：从政策建立规则并验证逻辑，仍需检查自然语言翻译与未验证内容。适合可形式化的政策子集，本轮不引入完整规则平台。https://docs.aws.amazon.com/bedrock/latest/userguide/integrate-automated-reasoning-checks.html

本方案是基于代码和失败证据的工程建议，不是已实现或零幻觉保证。本轮仅修正旧核验重放器遗漏conversation_context的评测接线，保持与生产user_context一致；生产协议尚未改变。
