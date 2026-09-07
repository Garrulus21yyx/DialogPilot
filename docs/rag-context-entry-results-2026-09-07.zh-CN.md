# 真实 Context 入口测试

本轮使用生产build_target_runtime工厂、隔离PostgreSQL/Redis、默认电商registry、当前消息admission、TargetConversationManager.prepare、PostgresMemoryProjectionReader与TargetTurnContextLoader。历史按官方真实角色存入conversation_turns，未手工构造TargetTurnContext。Flash固定、20条既定开发会话，20次模型调用。当前入口验证停在规划，后续检索使用实际已验证WorkPlan中的knowledge_search.query进行本地重放；不称HTTP/业务执行/最终答案端到端。

## 结果

- 原始历史189条；Context模型前投影保留147条；16/20会话受到默认最近8条限制。
- 147条可见历史逐一比对官方角色及去除首尾空白后的文本，一致。
- 隔离环境没有运行后台历史投影worker；冷投影由生产PostgresMemoryProjectionReader原文回读，Context标记DEGRADED/CURRENT_CONTEXT_PROJECTION_LAGGING。没有把降级强改READY。这不证明热投影、历史压缩、跨会话Memory已经验收。
- 实际规划15条KNOWLEDGE_QA，1条CLARIFY，4条OUT_OF_SCOPE。公共政府案例在电商registry下被拒绝应单列，不能直接算检索失败，也不能放宽线上领域来刷公共集成绩。
- 15条实际知识query，候选Top20和精排Top5均14条完整证据。全20分母下均14/20；相同20条raw直接检索为候选17、Top5为16。这两个全量数字包含路由差异，不是query改写纯收益。
- 同15条查询的raw/实际query比较见matched-report.json。用户原消息、Context、最终规划、模型实际请求与响应均保留，可逐例定位。

候选重放固定本地BGE-M3＋Python BM25，每路40、RRF .5/.5 k10、融合20、CrossEncoder精排5。生产后端和LLM listwise精排未在本轮执行。人工参考query仍保持独立对照，不能替换缺失的实际工具参数。

## 执行与校验记录

首个脚本尝试只写历史turn、没走当前消息admission，事件水位为0；第二个会话又触发全局turn key重复冲突。它不是有效主链结果，1次模型调用记录保留在rag-context-entry20-2026-09-07。修正版加入真实当前消息admission，历史key包含会话ID，重新运行20次。因此本轮总请求21次，有效对照20次。

捕获格式存在result包装；最初从SDK响应直接找goals漏掉query。已改从验证后的WorkPlan参数提取，并离线重算，未增加API调用。这是评测提取修复，不是RAG新增改写。

tests/test_target_turn_context.py与tests/test_doc2dial_history_roles.py合计7项通过。隔离数据库保留用于审查；未修改线上数据或默认Context窗口。

下一步验收应分别覆盖热投影、需要历史摘要的长会话和中文电商领域问题，并运行真正knowledge_search及ToolMessage/生成。当前数据首先证明之前“直接注入全部历史”的实验与主链输入不等价，后续优化必须沿该入口捕获query后再进行便宜的分层对照。
