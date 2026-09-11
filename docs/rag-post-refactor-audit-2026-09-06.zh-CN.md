# 外部结构重构后 RAG 复核

日期：2026-09-06。基线：`40fa44a` 加当前未提交工作区。审计不修改业务实现，不执行推理 API，不声明效果闭环。

## 结论

外部单运行时迁移已进入提交历史，但当前混合工作区仍有启动合同冲突。即使修复启动，真实 Target → Knowledge 的授权、历史、策略上下文以及检索结果状态转换仍未贯通。应先修复这些合同，再进行 query/parent-child/rerank 效果优化。

本轮发现不是全部由重构新引入；“本次新增发现”与“重构引入回归”不能混同。以下核查覆盖现在的生产调用路径，未执行真实服务端到端模型评测。

## Git 与已完成变化

- `git fetch origin --prune` 成功，HEAD/upstream ahead/behind=0/0，无未推送commit。
- 当前有33个已跟踪文件修改，另有未跟踪评测与审计产物。具体清单见审计目录 `git-status.txt`。
- 0030/0031 RAG迁移已经在提交 `ff22710` 进入历史，旧报告的“未提交迁移”已不适用于现在。
- `ed6b7a5` 统一不同执行模式的事实来源转换；知识事实现在是 `KNOWLEDGE_ASSERTED`，不再一概标记为 `VERIFIED_STATE`。这是已修复的来源类型问题，但不代表已经校验知识内容或检索成功。
- `25000c8` 等提交处理了 durable chat 客户端对提交结果的消费；旧runtime/flow/command合同已发生退役。它们改善执行与交付结构，不能替代RAG效果评测。
- 外部迁移计划明确使用 `/tmp/dialogpilot-clean-runtime-qSXCb1` 的干净已提交快照验收，并明确排除共享工作区的RAG policy/Bundle冲突。

## 当前问题（按修复顺序）

### 1. 启动配置合同不一致：本轮本地复现

`core/rag_policy.py` 默认包含 `expansion_query_weight`、`query_expansion_count`、`metadata_hint_weight`；`services/evolution/bundle.py:107` 的白名单不接受它们。`api/main.py:259` 从环境构造默认RAG策略，bootstrap路径会使用该策略。

本轮直接执行 `AgentBundle(version='audit-default', retrieval_policy=DEFAULT_RAG_RETRIEVAL_POLICY)`，得到：

```text
BundleContractError: unsupported retrieval policy keys:
['expansion_query_weight', 'metadata_hint_weight', 'query_expansion_count']
```

这是当前默认配置构造的确定性失败，不是数据库或模型故障。最小连贯修复应同步策略拥有者、Bundle允许字段与范围/组合校验、默认构造、版本指纹、回放和测试；不能在启动端偷偷删除字段。

### 2. 授权上下文未传给知识工具：源码端到端确认

`PostgresTargetAdmission` 在 `infrastructure/target_chat_adapters.py:48` 保存了 `command.authorization_fingerprint`。但 `application/target_conversation_manager.py:315` 构造工具可信上下文时只传 `invocation.metadata()` 和审批信息；`core/identity.py:167` 的metadata没有授权指纹。

Direct与framework都把这份 `trusted_context` 交给ToolManager；`api/main.py::_knowledge_tool_handler` 从中取授权指纹，缺省为空，而 `_retrieve_knowledge` 要求非空。检索设施初始化后，该标准路径会在真正召回前返回 `INVALID_CONTRACT / SUBJECT_OR_AUTHORIZATION_MISSING`。

这不是建议跳过授权校验。修复应由已认证请求上下文到执行器的转换边界保留授权身份与会话范围，覆盖首次执行、durable恢复、resume及用户隔离。不得由LLM参数或临时常量补指纹。这里是源码因果结论，未调用运行服务做在线复现。

### 3. 无证据/检索故障被当作“知识需求已满足”：本轮双执行器复现

`_knowledge_tool_handler` 将任何 `EvidencePackResult` 正常返回为字典。`mcp/tool_manager.py:708` 只要handler返回就包装 `ToolResult(success=True)`；这个值表达调用完成，不表达RAG领域成功。

`TargetToolExecutor` 与 framework result adapter 依据工具成功和 `authority` 产生事实，未检查 `data.status` 或 `evidence_pack`。因此 `INVALID_CONTRACT`、`NO_EVIDENCE`、`UNAVAILABLE` 也可以满足 `knowledge.active_source`。共享的 `fact_from_tool_result` 修正了来源类别，却没有关闭领域结果状态集合。

独立新上下文审查用无模型构造反例确认：Direct和framework在没有证据时均可返回 `SUCCEEDED`。

正向合同应明确：有有效EvidencePack的OK才能满足证据需求；NO_EVIDENCE是明确的无证据结果，应驱动澄清/拒答而非伪装工具异常；UNAVAILABLE是基础设施不可用；INVALID_CONTRACT/CONFLICT走相应确定性失败路径。由领域结果到AgentResult的转换边界统一实现，覆盖direct/framework/组合/部分成功/恢复。不能要求LLM自行理解嵌套的status来弥补程序状态错误。

### 4. 历史与检索配置未迁移：旧问题仍在，范围更完整

`TargetConversationManager` 把历史放在 `recent_relevant_turns`，而两个工具执行路径只传 `trusted_context`。handler读取的却是 `query_history`。框架模型可以看到历史，但KnowledgeRetriever仍不一定看得到。

同一转换缺口还涉及 `retrieval_policy/cache_scope/pinned_execution_refs/bundle_version`：当前工具路径会走缺省策略和未固定引用分支。授权修复后这些问题不会自动消失。

应定义明确的请求执行上下文投影：认证信息、带角色顺序的历史、冻结策略和来源generation来自各自权威owner；缓存、重试/恢复使用同一身份。不要把可信授权和不可信对话内容混成无类型字符串表。

### 5. 精排问题未修复；生成前另有一次可见性截断

- `application/knowledge_retriever.py:578`：精排仍接收 `request.query`，没有使用已经计算的standalone/resolved query。
- `mcp/result_reranker.py:97`：正文仍为前1,200字符。
- `mcp/tool_manager.py:943`：最终工具输出默认再按4,000字符预算保留头尾；framework把该内容作为工具消息给模型，完整artifact另行保存。完整存储并不能保证模型看到中间证据。

本轮构造一个中部含唯一证据标记的长工具payload，原始data保留标记，模型可见输出丢失标记，输出3,971字符并带truncated提示。这是可见性机制复现，不是业务样本损失率。

最小修复面包括Query语义和rerank缓存key，以及精排/工具消息各自的token预算与来源可追溯投影。不能仅移除一个截断而让下游再次裁掉同一证据。

### 6. 最终答案PASS仍缺语义支持保障

`application/response_assembly.py:61` 的单成功结果直接透传并标 `SINGLE_VERIFIED_RESULT`；组合路径主要检查claim ID与业务编号正则。它们没有证明答案中的政策断言得到证据支持。

独立审查构造反例：无证据时framework候选“所有订单均可无条件退款。”仍可获PASS；给定“仅未拆封商品可退”的允许事实，同一相反断言也能通过composer检查。它证明校验器允许此状态，不代表真实模型已经输出此答案。

`GroundedAnswerGenerator`仍存在，但 `_build_knowledge_context` 当前只见定义和测试调用；它的段落引用测试不能证明Target主链的发布质量。知识答案需要确定唯一的证据消费/语义保障责任，覆盖单结果、组合、无证据和部分成功；结构化ID校验只能是其中一层。

## 其他旧问题状态

| 项目 | 当前状态 |
|---|---|
| Metadata导入 | SourceRevision支持部分字段，但SourceDocument allowlist仍不接收region/有效期，直接导入region=local、effective_from=now |
| 离线解析 | 仍是text/markdown/json规范文本，非完整PDF/HTML/OCR/表格语义解析 |
| 分块 | 仍为structure-aware 512/64；没有本轮新增分块质量结果 |
| Parent文档内child重检索 | PostgreSQL候选owner未接入 |
| learned sparse | 本地实验具备；默认在线仍Dense+BM25 |
| Embedding配置 | factory仍可默认hash baseline；本轮未读取运行实例活动generation，不能宣称在线一定使用BGE-M3 |
| HNSW | 代码建索引与是否实际使用需分开；本轮没有数据库执行计划证据 |
| 新效果评测 | 未发现可用于证明这轮结构重构后RAG质量提升的同协议新结果；旧60/156条仍只是原有协议证据 |

## 验证与接下来的最小闭环

本轮相关现有测试97通过、14跳过；Bundle/lifespan补充选择集4通过、4跳过。合计101通过、18个环境相关跳过。跳过不能算作数据库或完整启动验证。新增构造探针仍能复现上述问题，说明现有通过集缺少跨边界验收。

审计证据位于 `artifacts/audit/rag-post-refactor-2026-09-06/`：测试日志、Git快照、Bundle/截断探针、独立执行器及答案反例脚本/输出。均无外部模型调用。

下一步顺序：

1. 默认RAG policy → Bundle bootstrap合同通过。
2. 真实Target入口 → Knowledge上下文在execute/resume/恢复中保真，授权仍严格校验。
3. 对所有检索领域状态做模型/表驱动状态测试，证明仅有效证据满足需求，故障/无证据正确传播。
4. 模型可见证据覆盖、答案支持与发布状态在单结果/组合链一致。
5. 再做同query、同corpus、同候选预算的rewrite/精排全文/parent-child消融，报告候选、可见、Top-5、packed和答案分层损失。

本轮仅完成复核。外部结构迁移与RAG语义合同应分别说明验收范围；当前不宜以已通过框架测试或原有检索分数恢复RAG质量已闭环状态。
