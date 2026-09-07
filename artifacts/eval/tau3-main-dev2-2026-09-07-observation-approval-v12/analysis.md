# v12 固定原任务实跑

- 基线 9b03a0a；原 train 任务 0、1，seed 300，DeepSeek v4 flash，completion budget 4096。
- 两条均 ERROR，官方分数 null；无换货写入，不得记为通过或官方 0 分。
- 任务 0 第 1 轮、任务 1 第 3 轮在同一 schema 边界失败：
  composition_model → ConversationProviderOutputError → structured_output_schema_invalid →
  ValidationError(maxContains, path=result.segments)。没有进入语义核验。
- 旧合同限制所有无支持标记的交互文本最多一段；这把段落组织方式误当成了交互合法性。
  该条不保障事实、审批或待补输入覆盖，后者已经由完整候选的核验负责。
- 下一修复移除 schema 与 renderer 中同一冗余限制，保持总输出有界、当前输入绑定、事实引用和语义核验。
  不是增加段落阈值，也不按任务/商品特化。
- 本轮不能验证写入后状态或最终回复改善；尚未走到该阶段。结果及失败原样保留，不挑样本重报。
- 存在既有 LangGraph Store 退出 pending-task 警告、LiteLLM provider cost 提示；不作为此次业务失败原因。
