# 电商知识工具 A/B/C 实施与验收

日期：2026-09-06。基线：40fa44a。范围：共享 knowledge_search 的契约、证据可见性和知识来源维护；保持现有 Conversation／领域 Agent 编排。A/B 已分批提交 b148145、bbbbc44，混合任务发布修复 46ca073。本报告不把工程测试通过解释为真实问答准确率提升。

## 问题与统一方案

| 归属 | 核查问题 | 当前实现与负责组件 |
|---|---|---|
| A 配置 | 默认 RAG 参数与 AgentBundle 校验允许集不一致 | core/rag_policy 统一规范化与校验；Bundle、在线 KnowledgePolicy 使用同一约束 |
| A 身份 | 系统授权、检索策略和来源代际没有贯通知识工具 | TargetChatApplication → 持久化 execution_context → PreparedTurn → ToolManager；系统注入授权指纹、策略和 generation；恢复保留原快照 |
| A 状态 | handler 正常返回被误当成已经获得知识事实 | knowledge_tool_contract 区分调用成功、有效证据、领域结果；Direct 和 framework 均按同一代数创建 facts／满足 requirement |
| A 状态 | 未知／损坏输出可能进入知识事实 | 完整校验状态、EvidencePack、来源、偏移和文本；未知结构转类型化失败 |
| B 查询 | Agent 已消解问题后，RAG 再次改写；召回与精排问题不一致 | Tool 固定 RESOLVED 模式；旧搜索适配器显式 HISTORY；resolved_query 贯穿召回、精排、打包及相应缓存身份 |
| B 查询 | Direct 未携带历史时点和适用范围 | ConversationAgent 的 knowledge_options 编译为工具参数；使用与 Tool 相同的闭合参数校验；未知条件保持未知 |
| B 可见性 | 精排只见前 1200 字符；ToolManager 和 Agent middleware 再截断 | 精排保留完整候选；知识专用紧凑证据序列化跳过通用字符串裁切，完整 artifact 保留来源 |
| B 预算 | 结构化 generator／reranker 绕过原有 provider 预算门 | BudgetedAnthropicClient 在真实 SDK request 边界计算消息、工具 schema、协议与输出预留；超限类型化失败，不发送请求；Verifier 继续使用原统一门 |
| B 回答 | Direct 返回证据 JSON；领域草稿缺统一支持检查 | Direct 复用 GroundedAnswerGenerator；领域／混合草稿检查引用并经过 AnswerVerifier；语义检查后再次读取权威来源 |
| B 引用 | Tool、composer、Verifier 使用不同引用标识 | 三者使用同一个 evidence_id → 原文／来源映射；原始 chunk ID 保留在 artifact |
| B 部分结果 | 知识弃答吞掉独立订单结果和已提交操作凭证 | ResponseAssembler 只移除知识相关结论，保留独立结果、VERIFIED_STATE facts 和 receipts |
| B 校验 | 模型返回字符串 "false" 可被 bool() 转成真 | AnswerVerifier 严格验证布尔类型及 PASS／grounded／reason 的一致性；损坏结果进入 UNKNOWN |
| C 来源 | 导入缺地区、商品范围、渠道和生效时间 | SourceDocument → SourceRevision v2 → SQL 读取 → ContextCandidate → EvidencePack → 模型证据视图；业务 Metadata 纳入版本身份 |
| C 版本 | 同正文不同 Metadata、历史政策、未知 manifest schema 含义不明确 | 新版本身份包含 Metadata；manifest 支持明确 v0／temporal-v2 且版本纳入 hash；保留原 v0/v1 身份兼容 |
| C 时态 | 仅保存最新文档，或选新版失败后回落旧版 | 活跃代际包含历史 revision；先按 as_of 选择最近生效版本，再判断过期／撤回和适用范围；新版过期／撤回不复活旧版 |
| C 适用范围 | 相似度或提示性 metadata 路由被当成适用资格 | 同一来源谓词用于 Dense、BM25 和原文复验；已知地区／渠道／商品条件筛选时包含 global／通用来源；region_hints 仍只是召回提示 |
| C 更新 | 并发导入覆盖其他更新；导入后重新推导出未入库的 revision | 导入使用数据库 advisory lock；来源、projection、generation 按既有 BUILDING → READY → ACTIVE 流程发布；直接返回写入 owner 的版本回执 |
| C 成本 | 每次更新重复嵌入全部来源 | 同 embedding profile、同 revision、同偏移、同 retrieval_text 才复用不可变向量；只嵌入变化片段，索引仍构建新代际 |
| C 撤回 | 旧向量／缓存、精排或生成等待期间仍能使用撤回来源 | 来源层单向 withdrawn_at；召回、缓存／新包复验、最终组装来源授权均检查撤回；不依赖重建索引成功 |
| C 结构 | Markdown 表格／列表条件可能在切块时拆散 | structure_aware 保留表格、连续列表及 fenced code 原文块与偏移；超过预算的原子块明确拒绝导入 |

## 正向契约与边界

```text
Agent 完整查询 + 已知业务适用条件
  → 系统授权／策略／代际／本 turn 当前时点
  → 词面和向量召回（同一个来源适用谓词）
  → 融合 → 完整候选精排 → 预算打包 → 权威来源复验
  → 原文证据 ToolMessage + 完整 artifact
  → Agent 回答或 Direct 生成 → 引用与语义验证
  → 权威来源发布资格复验 → Publication
```

`OK` 表示拿到有效证据，不表示证据覆盖全部问题。`NO_EVIDENCE`／`AMBIGUOUS` 不满足知识 requirement；`UNAVAILABLE` 为可重试失败；契约损坏、来源冲突进入确定失败。知识不足、核验失败时按受控弃答发布，不伪装成业务政策。

最终来源授权检查在语义核验之后执行，并对活跃 generation 和来源行持共享锁。该检查事务完成是授权判断时点；在此之前提交的撤回阻止本次回答使用来源。已经发布的历史回答不回溯改写。尚未组装的持久化结果重新检查来源；已完成 Publication 的重放仍是原历史记录。

同一 source_id 表示同一政策的时间序列；并行的地区政策使用不同 source_id。同一来源、同一生效时刻的冲突版本拒绝导入；重新发布撤回内容需要新的明确生效时点。撤回是单向状态，不删除审计原文。所有版本撤回后仍可保留物理索引，但检索返回无可用证据。

当前时点冻结在持久 turn 的 knowledge_as_of；用户明确指定的历史 as_of 优先。候选缓存使用准确时点，因此不同 turn 的“当前”查询一般不共享候选缓存；同一快照与显式相同时点可以复用。转换／向量／精排缓存仍按各自身份复用。这是正确性优先的当前成本限制，没有用任意时间取整跨越生效边界。

## 可量化验证

- 单元／组件选择集：177 passed，5 个环境跳过；覆盖查询、状态、引用、组合回答、导入、结构和预算边界。
- 真实 PostgreSQL 选择集：23 passed；覆盖来源更新／代际、原文回读、历史时点、适用条件、单向撤回、启动接线及 turn 恢复。
- 数据库 migration head：20260906_0036；升级链及不可变来源保护纳入真实库测试。
- 增量导入／来源选择集：真实 PostgreSQL 10 passed（与前述选择集部分重叠，不相加）。新增向量复用测试断言：导入一个新来源时只嵌入该来源；更新一个来源时只嵌入变化版本；旧导入回执始终指向真实入库版本。
- 结构测试组合 4 种前文长度 × 3 种 overlap，检查每个原文字符被覆盖、来源偏移精确、原子块不碎裂。
- 真实 PydanticAI → SDK 请求测试：小上下文窗口 + 长证据时 generator 和 reranker 均在 HTTP 前拒绝，网络请求数为 0。
- 两次独立复核发现并修复：混合结果丢失、引用映射缺失、布尔误读、结构化调用预算、Direct 适用参数、manifest identity、导入回执竞态、撤回竞态。独立复现中的撤回结果从 OK 变为 CONFLICT 且无 evidence。

本轮没有调用外部推理模型评测答案，也没有重跑新鲜 heldout。旧报告的 60 条本地模型评测和 156 条生产链路评测不能作为本次 before/after。要报告模型效果提升，下一步需要同一入口、同一预算、冻结数据与策略，逐层统计 Query 正确率、Candidate／Rerank-visible／Packed／ToolMessage 完整证据召回、引用支持率、最终答案正确率、延迟及 tokens；逐条记录救回和误伤。parent-child、层级检索及模型调参属于后续 D 对照实验。

## 支持范围

本批支持 public collection 的 UTF-8 text／Markdown／JSON 导入；PDF、OCR 和私有文档 ACL 不在该导入合同内。商品 product 是与来源标注一致的范围 ID，尚未做商品分类树推理。API 已增加 `/knowledge/withdraw` 管理入口；没有增加新的 RAG Agent、Flow、知识审批平台或通用事件系统。

部署仍需按项目已有流程应用数据库迁移并重启服务；本任务在隔离测试库验证迁移，没有操作生产数据库。工作区已有文档／评估产物与本批无关的改动不随本批提交。
