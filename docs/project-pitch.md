---
layout: default
title: 项目讲述与技术取舍
permalink: /project-pitch.html
---

# DialogPilot 项目讲述

> 以下表述以重构后的当前实现为准。面试中把机器报告的 commit、环境和 `scope_limit` 一起展示，不把某次测试数量当作永久项目属性。

## 30 秒版本

DialogPilot 是一个可恢复的多 Agent 客服后端。我把原本容易混在一次模型调用里的路由、任务规划、工具副作用、知识证据、回答校验和送达状态拆成明确 Owner。请求先在 PostgreSQL 持久准入，再运行 TaskGraph 和有界 ReAct；只有 Coverage 与 Verifier 通过的回答才能发布。PostgreSQL 是事实库，Redis 只做当前会话投影。本地 Docker Compose 可一键启动，并有真实鉴权 E2E、全量测试与恢复报告。

## 简历表述

可使用：

> 设计并实现 FastAPI 多 Agent 客服后端，以 PostgreSQL 持久化请求准入、会话事件、Knowledge/ServiceEpisode、Commitment/Handoff、附件和回答发布，以 Redis 提供可重建当前会话投影；通过 TaskGraph、受控 ReAct、工具幂等 receipt、Evidence/Coverage 和 fail-closed Verifier 实现可恢复、可追溯的客服服务链；实现 Agent 按需选择 Tesseract OCR 与 DeepSeek Vision 的 L0/L1/L2 多模态路径，并用状态机/集成测试、真实 JWT Docker E2E 与本地 dump/restore 报告验证。

不要使用：

- “已支持生产 Shadow/Canary 和自动回滚”——相关模拟已删除。
- “视觉观察可直接决定退款/故障根因”——VLM 只产带 provenance 的派生观察，业务事实仍由对应 Owner 决定。
- “接入完整生产可观测平台”——当前有脱敏 PostgreSQL span、正确的 Agent/Generation/Tool 层级和可选 Langfuse v4 exporter，但没有 Collector fan-out/tail sampling。
- “准确率达到生产标准”——项目数据仍含 provisional 标签，没有 human-reviewed Gold。
- “所有数据都在 PostgreSQL”——核心 Ticket/Commitment 已在 PostgreSQL，BadCase、ReAct/Bundle metadata 仍有本地 store。

## 最值得讲的三个问题

### 1. 为什么回答发布会 503？

切换为 PostgreSQL ResponseDelivery 后，最终 publication 必须引用已经存在的 Invocation，但在线 `/chat` 仍默认绕过 durable admission。症状是 LLM 正常完成，发布阶段报 `invocation not found`。

修复不是在 Delivery 中临时补一行 Invocation，而是让运行服务固定走 `admission → execution → publication` 单主链。这样请求身份、固定版本、outbox、执行和最终回答共享同一权威生命周期。

### 2. 为什么 Knowledge 正常检索却降级？

Evidence 的 `source_type` 属于 `SourceReference`，校验器却从 `EvidenceItem` 顶层读取。直接检索测试覆盖了候选生成，但缓存 Evidence Pack 的再校验路径没有覆盖。

修复发生在 provenance 转换边界，并新增测试证明 validator 从 `source_ref` 读取。真实 E2E 随后得到 `knowledge_used=true / grounded=true / verified=true`。

### 3. 为什么 Redis Worker 会出现 closed transport？

PostgreSQL projection dispatcher 在工作线程中调用 `asyncio.run`，驱动同一个 lifespan-owned Redis async client。客户端被跨事件循环使用，连接 transport 随临时 loop 关闭。

修复是增加异步 projection dispatcher：PostgreSQL claim/ack 放在线程，Redis effect 始终在应用主事件循环执行。这关闭了共享资源的事件循环所有权不变量。

## 核心取舍

### 直接 Python TaskGraph，而不是先引入框架

当前任务状态和失败代数已经闭合，直接实现更容易展示依赖、预算、Coverage 和恢复语义。只有当需要跨进程长图、动态节点和框架级持久调度时，才值得引入 LangGraph 一类依赖。

### PostgreSQL + Redis，而不是两个事实库

PostgreSQL 保存可审计事实；Redis 优化当前对话读取。投影可以重建，因此缓存丢失不会产生第二权威答案。

### 轻量本地 embedding，而不是默认下载大模型

Docker 本地展示优先确定性、体积和启动速度。384 维 feature hashing 保证 Knowledge/ServiceEpisode 路径完整跑通；它不冒充高质量语义模型，后续可以在同一 embedding port 替换。

### 失败关闭，而不是流畅优先

Coverage 不完整、Verifier `REJECT/UNKNOWN`、工具副作用未知都不能当成成功。系统返回安全结果或 Handoff，并保留 typed reason。

## Demo 顺序

```bash
docker compose up -d --build --remove-orphans
curl http://localhost:18000/health
PYTHONPATH=. .venv/bin/python scripts/run_local_e2e.py \
  --output evaluation/reports/local-e2e-v1.json
VLM_ENABLED=true docker compose up -d --build dialogpilot nginx
PYTHONPATH=. .venv/bin/python scripts/run_local_vlm_e2e.py \
  --output evaluation/reports/local-vlm-e2e-v1.json
```

展示报告时重点指出：

- Knowledge engine 是 `postgresql+pgvector+pg_fts`。
- 请求经过真实 JWT，而不是测试内直接调用函数。
- L1 E2E 识别截图 `E42` 且不调用 VLM；L2 E2E 调用 DeepSeek Vision 并由 Technical Worker 消费观察。
- 报告不保存 JWT、API Key 或完整用户回答。

## 当前边界

这是本地作品集系统，不声称有生产流量、生产 RPO/RTO 或组织级发布治理。Commitment/Handoff 与基础持久可观测均已完成：默认 PostgreSQL Trace 可查询，配置凭据可发送到 OTel-native Langfuse v4；不模拟生产 Collector 治理。

## 证据索引

- 主链与 Owner：[完整架构教程]({{ '/' | relative_url }})、[架构边界]({{ '/architecture.html' | relative_url }})。
- 评测：[500 条分层评测]({{ '/evaluation-500/' | relative_url }})、[RAG 全链路评测]({{ '/rag-pipeline-evaluation/' | relative_url }})。
- 生产责任缺口：[RAG 生产化审计]({{ '/customer-service-rag-production-audit/' | relative_url }})。
- 机器报告：[`local-e2e-v1.json`](https://github.com/garrulus21yyx/DialogPilot/blob/main/evaluation/reports/local-e2e-v1.json)、[`local-vlm-e2e-v1.json`](https://github.com/garrulus21yyx/DialogPilot/blob/main/evaluation/reports/local-vlm-e2e-v1.json)、[`local-postgres-restore-v1.json`](https://github.com/garrulus21yyx/DialogPilot/blob/main/evaluation/reports/local-postgres-restore-v1.json)。

---

## 技术答辩底稿

这一部分不是简历摘要，而是为了在追问时能从 FastAPI 入口一路讲到 RAG、Memory、多模态、并发、Reducer、发布与 LangGraph 取舍。

{% include_relative _includes/current-runtime-deep-dive.md %}
