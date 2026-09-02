---
layout: default
title: DialogPilot 面经校准与追问手册
permalink: /interview-guide.html
---

# DialogPilot 面试追问

> 本页按重构后的当前实现校准。回答原则：先说 Owner 和正向合同，再说数据流、失败语义、验证证据与当前边界；测试数与运行结果引用对应 commit 的机器报告，不背诵会漂移的总数。

## 项目与架构

### Q：用一分钟介绍项目。

DialogPilot 是 FastAPI 多 Agent 客服后端。请求通过 JWT 后先持久化到 PostgreSQL，再由 Router/Planner 产生闭合 RouteDecision。`EXECUTE` 构建 TaskGraph，领域 Worker 在任务内执行有界 ReAct 和白名单工具；附件由 Agent 按需选择 L0/L1/L2，Tesseract 与 DeepSeek Vision 只产可追溯派生观察。Evidence、Coverage 和 AnswerVerifier 决定回答能否发布。最终回答先写 PostgreSQL，再返回客户端并投影到 Redis 当前窗口。项目用 pytest、Docker Compose、L1/L2、Commitment、Trace 真实 HTTP E2E和本地恢复报告验证，数量与状态以当次 commit/report 为准。

### Q：为什么不是一个大 Prompt？

大 Prompt 会把业务范围、工具授权、知识来源、副作用成功和答案质量混成模型的概率判断。本项目分别由 RouteDecision、ToolManager、EvidenceReceipt、CoverageGate 和 Verifier 拥有这些事实，所以每个失败都能落到具体边界。

### Q：为什么不用 LangChain/LangGraph？

当前状态代数和任务依赖有限，直接 Python 能清楚表达 task identity、预算、依赖阻塞和恢复语义，也减少本地展示依赖。需要跨进程长图或动态节点时再比较框架的 checkpointer、升级兼容和可观测性成本。

## 路由与多 Agent

### Q：意图和路由有什么区别？

意图描述用户主要需求；RouteDecision 决定需要哪些 authority、route mode 和 verification profile；TaskGraph 再把它转换为带 Owner、依赖、上下文和成功条件的任务。一个复合问题可以有一个主意图，但包含 Billing 与 Technical 两个任务。

### Q：低置信度为什么不同时调用所有 Agent？

模糊不是多领域证据。低置信度进入 `CLARIFY`，明确越域进入 `OUT_OF_SCOPE`；两者都没有 TaskGraph。只有存在可分离领域证据时才并行，避免多次模型调用共同猜测。

### Q：怎样证明多 Agent 不是简单拼接？

TaskGraph 在执行前校验重复 ID、悬空依赖和环；Worker 返回闭合 typed outcome；Coverage 对照必做任务，ResultSynthesizer 处理冲突和顺序。缺失或阻塞任务仍留在结果代数中，不能被流畅文本隐藏。

## ReAct、工具与副作用

### Q：模型能否自行批准写工具？

不能。模型只提出调用意图，ToolManager 根据 Agent allowlist、工具风险和宿主审批决定。写调用等待审批时保存 checkpoint；resume 再验证 user、task、tool、arguments 和版本绑定。

### Q：网络超时后怎样避免重复退款？

同一个 tool call 先 claim 幂等身份；已完成调用重放相同 receipt。若超时后不能证明业务是否提交，返回 `outcome_unknown`，不能靠盲目 retry 猜测。当前业务工具是本地沙箱，不声称已连接真实支付系统。

### Q：为什么 receipt 很重要？

自然语言“已完成退款”不是业务事实。Receipt 才能证明哪个 authority、哪次操作、什么状态和幂等键产生了结果。Coverage 和 Verifier消费 receipt，而不是只看 Agent 文本。

## Knowledge 与证据

### Q：RAG 链路是什么？

PostgreSQL 保存 SourceRevision 和 chunks；pgvector 与中文 FTS 各自产生候选，weighted RRF 融合，ContextPacker 控制预算，EvidencePack 保存 revision、checksum、字符范围、rank 和 manifest，再进入 grounded generation 与 Verifier。

### Q：为什么 Dense 权重只有 0.25？

这是当前本地检索配置，不应包装为全局最优。客服政策含稳定业务词，中文 FTS 在现有 Dev 实验中更可靠；Dense 补充语义召回。正式调权重需要新鲜、group-safe 的 heldout 和分层指标。

### Q：本地 embedding 是什么？

当前默认是确定性 384 维 feature hashing，包含 ASCII token 与中文 unigram/bigram。它避免 Docker 下载多 GB 模型和 CUDA 依赖，保证本地链路可复现；不是训练过的语义模型。替换点由 embedding port 和 generation manifest 隔离。

### Q：公共知识能回答“我的退款到账了吗”？

不能。公共 Knowledge 只提供政策；用户订单、退款和账户当前状态必须来自业务工具 receipt。AuthorityPolicy 和 Coverage 防止知识文本冒充实时私有事实。

## 存储、记忆与恢复

### Q：为什么同时使用 PostgreSQL 和 Redis？

PostgreSQL 是事件和业务事实 Owner，Redis 是当前会话快速投影。projection outbox 异步更新 Redis，失败可重试；Redis 丢失可以从 PostgreSQL 重建，所以不是双写双真相。

### Q：如何保证请求重试不产生两个回答？

JWT subject、tenant、conversation 与 request_id 形成 Invocation identity。Admission 先落库，publication 对 invocation 建唯一约束；相同请求重放已完成 publication，不重新生成第二个权威回答。

### Q：你遇到过什么并发或生命周期 bug？

Projection worker 曾在线程里用 `asyncio.run` 调共享 async Redis client，临时事件循环关闭 transport，导致事实 Worker 报 closed connection。修复为异步 dispatcher：PostgreSQL claim/ack 在线程执行，Redis effect 始终留在 lifespan 所属事件循环。

### Q：为何没有旧数据迁移？

项目没有真实旧数据。保留 backfill、双写和 cutover 只会制造第二权威路径，所以新安装从空 PostgreSQL 直接跑 Alembic；验证重点是 empty-to-head、真实 E2E 和本地 dump/restore。

## 发布、送达与 Handoff

### Q：Verifier 的三种状态是什么？

`PASS / REJECT / UNKNOWN`。只有 PASS 正常发布；REJECT 与 UNKNOWN 都安全升级。解析异常不能隐式变成 PASS。

### Q：HTTP 200 是否等于用户已经看到回答？

不等于。PostgreSQL publication 产生 `response_id + response_seq + selected`；客户端渲染后 ACK `delivered/read`。重连按 seq 拉取，业务 ACK 才能表达用户侧送达，而不是用消息队列 ACK 替代。

### Q：为什么还保留 TicketService？

Handoff 是客服闭环的一部分。当前 PostgreSQL TicketService 负责工单 identity、幂等、合法状态迁移、event 与 outbox；它不是回答末尾的 `escalated=true`，也不是已经删除的 SQLite ResponseDelivery 双路径。

## 测试与证据

### Q：怎样验证项目完整跑通？

三层证据：Owner 合同、状态机与集成 pytest；Docker Compose health；L1/L2 脚本通过真实 JWT 请求附件与 `/chat`，Commitment 与 Trace 脚本通过管理 API 验证持久状态。L1 报告证明 OCR 足够时 VLM 调用为零；L2 报告证明 DeepSeek Vision producer/model/version、视觉 artifact 和回答消费均可追踪。完整性必须由相同 commit 的测试输出和机器报告共同证明。

### Q：恢复报告证明什么？

它证明本地空库 schema 可以升级、dump、restore，并对迁移 ledger/记录哈希做一致性检查。它不证明生产 RTO、RPO 或真实业务数据恢复。

### Q：为什么不宣称准确率？

仓库评测数据包含 provisional 和公开数据映射，没有完整 human-reviewed Gold；部分 heldout 已被开发过程消费。可以展示分层 scorer 和回归结果，但不能包装成生产准确率。

## 当前限制与后续节点

### Q：项目还缺什么？

当前已有附件、Tesseract OCR、可配置 DeepSeek Vision、PostgreSQL Ticket/Handoff/Commitment，以及脱敏 PostgreSQL Trace 与可选 Langfuse v4 exporter；仍没有生产 Collector/tail sampling，BadCase、ReAct/Bundle metadata 仍是本地 store，也没有未消费的人工 Gold heldout。

### Q：为什么删除 Shadow/Canary？

它们解决线上流量发布风险，但项目没有线上流量。为本地作品集重复执行 Agent、维护 5%/25% cohort、promotion/rollback pointer 和签署文件不会提高核心 Agent 证明力，反而增加成本与双路径。当前验收改为测试、真实本地 E2E、机器报告和可复现 Demo。

### Q：下一步优先做什么？

先完成 fresh human-reviewed Gold 与 Service-chain v2 的真实 runner/独立复核，再按证据决定是否引入更强中文 embedding、复杂文档 ingest 或 LangGraph 薄 runtime。生产 Collector、容量与恢复目标只有在存在真实部署约束时才立项，不能用模拟流程冒充成熟度。

---

## 深挖题底稿：从 FastAPI 到 LangGraph 取舍

以下材料补齐每个追问的代码链、触发条件、状态代数、存储和失败路径。面试时按问题截取，不要整段背诵。

{% include_relative _includes/current-runtime-deep-dive.md %}
