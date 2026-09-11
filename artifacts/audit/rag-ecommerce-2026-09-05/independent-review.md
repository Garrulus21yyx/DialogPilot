# 独立新上下文核查

2026-09-05，只读审查；未修改业务代码，未调用付费模型。

1. Target manager 有历史，但 AgentContextView 到知识工具的转换缺少 recent_relevant_turns → query_history 映射。修复需要同时覆盖 execute/resume，明确顺序、窗口与会话范围。
2. KnowledgeRetriever 给精排 raw query，ResultReranker 截取前1200字符均属实。分别属于query语义与共享rerank输入预算owner。改变query须同步rerank缓存key；改变预算须覆盖通用ToolManager消费者。静态事实不能代替质量消融。
3. SourceRevision支持region/有效期，SourceDocument导入allowlist不支持，直接导入固定region=local、effective_from=now。修复边界包括DTO、revision转换/幂等、投影与检索过滤。
4. GroundedAnswerGenerator逐段ID校验与Target ResponseAssembler单结果透传/多结果ID校验不是同一保障，均不能自动证明自然语言蕴涵。需要确定Target知识答案的语义保障owner并覆盖单结果、组合、fallback和部分成功。

结论：四项归因得到独立代码核验；不宣布RAG质量闭环。
