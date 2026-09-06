# 统一 ConversationAgent 原生规划输出验证

生产 provider v9 使用 application.conversation_agent.planning_output_schema() 作为 submit_turn_plan 的输入 schema。接受 resolved、insufficient_context、out_of_scope 三个互斥状态；要求恰好一个完整的指定工具输出。本地 schema 验证后仍由现有编译器验证实体来源、依赖、能力及授权。原生输出不等于模型服务端保证 schema，也不增加业务工具、部分目标执行或退款写权限。

触发案例中，模型已正确理解非质量退货，但将 order_id 输出为嵌套对象，导致整轮计划无法编译。修复位于 provider 输出边界：把既有应用所有者 schema 发给模型并验证返回形状，而不是在调用者猜测或修补订单 ID。旧文本 JSON 解析仅在 evaluation/legacy_conversation_planning.py 显式保留；生产无文本 fallback。

## 验证

- 85 项规划、ConversationAgent 和 provider 预算测试通过，包括三状态、嵌套 ID、数值 goal_id、重复依赖、错误工具、多个工具和截断。既有编译器实体约束测试继续通过。
- 独立只读复核运行 137 项相关测试通过。复核发现并修复回放请求描述漂移、旧 scope 标注和无效形状统计缺失。
- 实际入口：artifacts/eval/rag-native-planning-input-2026-09-06/cases.json，固定原有订单/政策条件；输出 artifacts/eval/rag-native-planning-mixed1-2026-09-06/summary.json，原始捕获 mixed-cases.jsonl.gz。
- DeepSeek Flash low，规划最低完成预算 4096；全链路共 4 次 API 调用。统一 TargetChatApplication / ConversationAgent 实际调用 order_lookup 与 knowledge_search。
- 实际查询：“耳机已拆封且因个人不喜欢（非质量问题）而申请退货时适用的退货与退款适用规则及条件”。
- 预期来源可见、被引用、知识支持检查各 1/1；答案明确“已拆封耳机非质量原因不适用无理由退货”。同时添加了质量问题说明，表达仍可收敛。

这是已消费的模拟开发案例复测：旧运行没有执行工具，本次成功贯通。相对旧案例，生产 composition 也已改变，不能把最终答案改善全部归因于规划格式。没有证明总体准确率或检索召回率提升；需继续多案例同入口验收。

## 评测边界与复现

检索策略实验固定业务快照和查询；查询生成实验固定上下文与业务工具事实，让统一 Agent 产生真实查询；混合入口验证二者协作。业务失败、入口失败、检索失败与生成失败分别记账，端到端指标保留全部案例。

真实混合入口使用 scripts/run_rag_tool_calibration.py --mixed-business --mixed-case-file <上述 cases.json>，独立数据库和同一冻结 BGE-M3 模型；数据库凭据从环境注入。规划分层回放使用 scripts/run_conversation_plan_replay.py --modes structured_native --current-output-schema；native 保留生产工具定义。文本/旧 structured 模式显式使用 evaluation provider，记录版本。wire_schema_valid=null 表示没有可检查的完整输出，false 表示形状不合法，均不得从成功率分母剔除。
