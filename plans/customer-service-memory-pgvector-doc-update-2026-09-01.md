# Memory + pgvector 文档收敛计划

目标：在不引入 Memory sidecar、不中断现有 Agent/TaskGraph 编排、也不建立中央业务状态机的前提下，将 DialogPilot 的目标架构与实施计划收敛为可执行方案：PostgreSQL 持有会话、案件、承诺和 Memory 权威事实；pgvector 与经评测的中文词法层承载 Knowledge、ServiceEpisode 和按需视觉的可重建检索投影；现有 Chroma/SQLite BM25 通过 shadow、canary、回滚窗口后退出。

约束：

- 两份现有文档是用户工作成果；只做本请求相关的局部、相互一致的修改。
- Raw transcript 是 MEM_L0 权威事件，不设计 Raw→L0→L1→L2→L3 串行摘要状态机。
- Knowledge、ServiceEpisode、ActiveCase、Profile 和 Visual 分 Owner、分表、分策略、分评测。
- 活跃服务债务从 PostgreSQL 权威表确定性读取，不依赖向量召回。
- pgvector 是当前目标候选；中文词法质量、过滤召回、OLTP 干扰和恢复演练不过 Gate 时不切换，并进入 Elasticsearch/OpenSearch 对比评测。

## 步骤

1. **completed** — 审计两份文档的 Memory、数据拓扑、检索、迁移、Gate 和 PR 任务，标出需要替换或补充的段落。
   - 文件：`docs/customer-service-agent-target-architecture.zh-CN.md`
   - 文件：`docs/customer-service-agent-implementation-plan.zh-CN.md`
2. **completed** — 更新目标架构：写清 MEM_L0/L1/L2/L3、Thread Summary、Owner/存储/触发/水位、ServiceContinuity、统一检索内核、pgvector/FTS 边界与 Agent 读取规则。
   - 修改：`docs/customer-service-agent-target-architecture.zh-CN.md`
3. **completed** — 更新实施计划：增加后端无关接口、PostgreSQL schema、中文词法 shadow、Knowledge/Memory 分阶段切换、回滚、删除防复活、ES 评测触发条件与验收 Gate。
   - 修改：`docs/customer-service-agent-implementation-plan.zh-CN.md`
4. **completed** — 跨文档一致性与机器检查：标题/术语/任务依赖/PR 映射/旧 Chroma 与模糊三选一负向搜索。
   - 验证产物：命令输出与行号证据
5. **completed** — Fresh-context reader test：让独立审阅者回答“谁写/存/更新/读取每层 Memory、如何切换、失败如何回滚、为何不直接上 ES”，修复 P0/P1 歧义。
   - 修改（如需）：上述两份文档

## 连续对话与按需多模态增量

新增目标：在上述数据面不变的前提下，让普通追问复用仍有效的路由、Requirement、Evidence、只读 Tool receipt 与媒体派生产物；只有开放的 typed PendingSignal 才恢复原 LangGraph run。服务债务继续是 ActiveCase、Commitment、Handoff、PendingSignal 与未知 Tool effect 的统一只读组合，不新增 ServiceDebt 表、Agent 或全局状态机。

6. **completed** — 更新目标架构：加入 `TaskContinuationFrame`、Agent-owned `ContinuationGate`、硬失效规则、immutable delta TaskPlan、分层 Retriever cache，以及逐 requirement×asset/region 的 `MediaReusePolicy`。
   - 修改：`docs/customer-service-agent-target-architecture.zh-CN.md`
7. **completed** — 更新实施计划：增加 M4-T03E、缓存/多模态复用任务、DAG/Gate/PR/评测与可观测性要求。
   - 修改：`docs/customer-service-agent-implementation-plan.zh-CN.md`
8. **completed** — 最终验收：机器检查任务 DAG、Task→PR 映射、Markdown 结构；媒体部分只保留 Agent decision、确定性 artifact 复用和 LangGraph native terminal signal，没有新增 Vision Agent 或中央媒体状态机。
   - Target SHA-256：`4dc37252b6f4779757485cb543ea0459bcbbaf8fdc08ebb30263258aaf41ef12`
   - Plan SHA-256：`3f43db434fee13b59f5c60c949435c8f942eddbd4750508eab339acdb1a033d1`
   - 机器检查：78 个任务、194 条显式 Build 边、DAG 无环；78/78 Task 有 PR 映射；两份 Markdown code fence 完整；旧 `MEDIA_READY/all-required-bindings-ready` 表述已清除。
