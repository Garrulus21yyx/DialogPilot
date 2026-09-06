# RAG API 查询入口校准（开发集）

用户明确允许使用 API，尤其当本地模型能力不足时。成本优化采用复用检索缓存、限制开发批次和记录实际 tokens，不再把 provider-free 作为能力限制。

## 已证实的问题与修复

Conversation Provider 先前仅接收角色模型名，未通过 ModelProfile.request 传递 thinking 配置。20条中文模拟电商开发案例中，Flash默认思考消耗800-token输出预算，12条返回max_tokens且仅有thinking，没有JSON。这些是预算失败，不是已证实的语义误判。

修复统一在请求构造边界：生产注入完整ModelProfile，plan/compose均采用角色请求合同；会话输入预留随实际输出预算增加，且受角色上下文上限约束；完整system/message在调用前通过既有ProviderContextBudget校验；Agent将预算拒绝映射为CONTEXT_BUDGET_EXCEEDED。领域worker原有预算独立保留。68项相关测试通过，独立检查通过。

## 开发结果

| API配置 | RESOLVED | CLARIFY | OUT_OF_SCOPE | INVALID_PROVIDER_OUTPUT |
|---|---:|---:|---:|---:|
| Flash，旧入口默认thinking，800输出 | 8 | 0 | 0 | 12 |
| Flash，显式角色配置关闭thinking，800输出 | 4 | 7 | 4 | 5 |

两轮各20次调用，使用同一20条开发案例，不是40条独立验收题。第二轮不再截断，但仍误判政策问题、生成不合法结构。RESOLVED仅表示计划通过合同校验，不等于问题理解正确或答案正确。不能把关闭thinking宣传为效果提升。

后续开发实验已完成：旧提示词Pro/high/4096为17 RESOLVED、1 CLARIFY、2输出截断；实验提示词Flash/none为9 RESOLVED、2 CLARIFY、4 OUT_OF_SCOPE、5 INVALID；实验提示词Flash/low/2048为18 RESOLVED、1 CLARIFY、1 OUT_OF_SCOPE。4条业务对照保留业务查询/操作路径。结果不是语义正确率，混合业务问题允许同时查订单与知识，不应把没有knowledge_search一律计错。

同模型Flash/none的4条query-only诊断均保留原意，包括“不是”的否定关系。它说明查询能力不能由整个规划器的失败率代替，但任务与提示词同时变化，不构成完整因果隔离。实验提示词保存在artifacts/eval/rag-api-planner-dev-2026-09-06-semantics/system-prompt.txt，可通过--system-prompt显式重放；生产提示词与默认模型配置未推广这些实验变更。

## 复现

```bash
.venv/bin/python scripts/run_api_conversation_planner.py --output /tmp/rag-api-planner-new
MODEL_INTENT=deepseek-v4-pro MODEL_INTENT_REASONING=high MODEL_INTENT_MIN_COMPLETION_TOKENS=4096 .venv/bin/python scripts/run_api_conversation_planner.py --output /tmp/rag-api-planner-pro-new
```

脚本读取现有.env及环境覆盖，使用生产ConversationAgent与Provider提示词，最多20次API调用，SDK重试为0。输出仅包含模拟问题、请求、响应及usage，不写入密钥或服务地址。当前脚本复现修复后的入口；旧入口原始请求以实验捕获为准。

本批没有执行知识工具、PostgreSQL检索或答案生成，不能据此声称RAG全链路完成或最终答案质量提高。后续应把有效完整查询送入真实工具入口，再评估证据与答案。金额尚未按供应商账单核算，以tokens如实报告。
