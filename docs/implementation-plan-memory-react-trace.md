# Hybrid Memory + ReAct Security/Trace 实施计划

## 目标

把两个简历建议变成仓库可运行、可测试的事实：

1. 长期记忆不再只检索压缩摘要，而是保存原始情景片段，用 BM25、向量召回和时间排序做 RRF 融合，并提供 Recall@K/MRR/nDCG 评测。
2. Agent 获得有界 ReAct 工具循环；工具调用必须经过 Agent allowlist、风险/审批策略、闭合状态机和可追踪审计，TraceId 能跨 API、Task、Agent 和 Tool 透传。

## 根因与正向合同

### 长期记忆

- 现状症状：`_store_episodic()` 把摘要作为 Chroma document，`_search_episodic()` 只做向量 Top-K。
- 根因：摘要同时承担“压缩上下文”和“长期事实源”两种权威，精确标识符与原始细节在压缩后不可恢复。
- 目标合同：原始片段是可检索事实载体；摘要只做背景投影。候选集由用户隔离后的向量与 BM25 产生，时间仅对相关候选排序，RRF 负责融合；空查询/存储故障确定性返回空结果。

### ReAct、权限与 Trace

- 现状症状：Agent 只有单次模型调用；`MCPToolManager.call()` 没有 Agent 身份、审批状态、调用 ID 或审计记录。
- 根因：工具定义、授权、执行、输出投递和观测没有共同的调用合同。
- 目标合同：每个调用都有 `trace_id/call_id/agent/tool/status`；Agent 只能看到并执行 allowlist 工具；高风险工具在默认模式下没有明确批准就不执行；ReAct 有最大步数和闭合终态；工具长输出在回写模型前有界压缩。

## 阶段

| 阶段 | 状态 | 验收 |
|---|---|---|
| 1. 混合长期记忆 | done | 原始片段写入、BM25+vector+recency RRF、指标与测试 |
| 2. 工具安全与 Trace | done | allowlist、审批、调用状态、审计、上下文 TraceId |
| 3. 有界 ReAct 集成 | done | Anthropic tool loop、max steps、输出压缩、Agent 集成 |
| 4. API/文档/Page | done | 响应证据、配置、简历 STAR、完整教程和线上验收 |

## 非目标

- 不把现有确定性 TaskPlan 强行迁移到 LangGraph。
- 不实现远程 MCP transport/server/session；内部工具运行时继续如实命名和说明。
- 不允许模型直接执行退款、账户修改等真实副作用；高风险工具保持审批边界。
- 不虚构线上准确率或延迟收益，只报告可复现测试和离线检索指标。

## 变更记录

- 阶段 1：新增 `memory/hybrid_retrieval.py` 与 `tests/test_hybrid_memory.py`；Chroma v2 记录以原始重叠片段为 document，摘要退回 metadata/Prompt 背景；用户内向量池和 BM25 语料独立降级后用加权 RRF 融合，时间只重排相关候选；提供 Recall@K/MRR/nDCG。完整回归 56 passed。
- 阶段 2：新增 `core/tracing.py` 和 `tests/test_tool_security_trace.py`；`MCPToolManager` 增加 Agent allowlist、风险/读写属性、宿主侧审批、调用终态、输出截断、参数哈希和脱敏审计；TraceId 通过 contextvars 传播到并行 asyncio Task。完整回归 60 passed。
- 阶段 3：新增 `agents/react_engine.py` 和 `tests/test_react_engine.py`；领域 Worker 在确定性 TaskPlan 内执行有界 Anthropic `tool_use/tool_result` 循环，只读调用可并行、潜在写调用串行；拒绝、待审批、工具错误和超步数均为失败闭合终态，编排层保留 `react_status/react_steps/tool_call_ids` 且不以 GeneralAgent 静默覆盖证据。完整回归 65 passed。
- 阶段 4：API 返回 `trace_id/tool_audit/memory_retrieval` 及 ReAct outcome 证据；Compose/.env 补齐 ReAct、审批和输出边界；README、architecture、pitch 与完整教程同步为真实实现。Page 扩展为 26 个编号章节、56 道项目追问和两项 STAR 表达，并新增 Evidence Board。GitHub CI/Pages 绿色；Chromium 桌面 1440px 与手机 390px 验证无横向溢出、无 console error/warning，期间修复目录 `scrollIntoView` 误滚正文和手机双 sticky 遮挡锚点两个线上 UI 问题。
