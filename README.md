# DialogPilot

面向客服场景的可恢复多 Agent 后端。它不是把多个 Prompt 串起来，而是把路由、任务图、工具副作用、知识证据、上下文、发布资格和送达状态分别交给明确的 Owner。

当前仓库以“本地完整跑通、可复现、适合简历展示”为目标：在线请求只走一条 PostgreSQL 主链，不包含旧数据迁移、双写、Shadow/Canary 或模拟企业审批发布流程。

## 已实现能力

- FastAPI + JWT 身份边界；请求体不能冒充其他用户。
- `EXECUTE / CLARIFY / OUT_OF_SCOPE` 闭合路由，以及依赖感知的 TaskGraph。
- General、Technical、Billing、Account Security 等领域 Worker；任务内执行有界 ReAct。
- 工具白名单、写操作审批、持久 checkpoint、幂等调用和 typed receipt。
- PostgreSQL 准入、Conversation/Invocation、回答发布、Knowledge、ServiceEpisode 与用户事实。
- Redis 当前会话窗口及可重建投影；PostgreSQL 事件保持权威。
- PostgreSQL pgvector + 中文 FTS + weighted RRF，Evidence Pack 保留来源、版本与 chunk 坐标。
- 认证附件上传、安全扫描与本轮绑定；Agent 默认 L0，按需选择 Tesseract L1 或 DeepSeek Vision L2。
- ParseResult/EvidenceNode 保留 asset checksum、page/bbox、producer/model/version；媒体观察只作为非权威数据进入 TaskGraph。
- Coverage + AnswerVerifier 发布门禁；只有可发布结果直接返回，其余安全升级到 Handoff。
- `response_id + response_seq + selected/delivered/read` 送达状态。
- TraceId、Prometheus 指标、本地评测、空库初始化、恢复演练和机器可读 E2E 报告。

当前文本知识与多模态客服链均可本地复现；持久 OTel/Langfuse 属于后续节点，README 不把它描述成已实现能力。

## 快速开始

要求：Docker Compose，以及一个支持 Anthropic Messages 协议的模型 API Key。默认配置使用 DeepSeek 兼容端点。

```bash
cp .env.example .env
```

至少修改以下两项：

```env
ANTHROPIC_API_KEY=your_api_key
AUTH_JWT_SECRET=replace_with_at_least_32_random_bytes
```

启动完整本地栈：

```bash
docker compose up -d --build --remove-orphans
curl http://localhost:18000/health
```

运行一次真实鉴权对话并生成脱敏报告：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_local_e2e.py \
  --output evaluation/reports/local-e2e-v1.json
```

可选启用 DeepSeek 视觉模型（官方 Anthropic Messages 图片协议）：

```env
VLM_ENABLED=true
MODEL_VISION=deepseek-v4-flash-vision-exp
VLM_BASE_URL=https://api.deepseek.com/anthropic
```

```bash
VLM_ENABLED=true docker compose up -d --build dialogpilot nginx
PYTHONPATH=. .venv/bin/python scripts/run_local_vlm_e2e.py \
  --output evaluation/reports/local-vlm-e2e-v1.json
```

模型名与图片 content block 以 [DeepSeek Vision 官方文档](https://api-docs.deepseek.com/guides/vision/) 为准。`VLM_ENABLED=false` 时 L2 返回 typed unavailable；L0/L1 不会调用 VLM。

最近一次仓库验证结果：

- 全量测试：`881 passed`
- Docker Compose：应用、PostgreSQL、Redis、Nginx、Prometheus 均 healthy
- L1 E2E：真实 PNG 经 Tesseract 识别 `E42`，Technical Agent 回答且 `verified=true`，VLM 调用为零
- L2 E2E：红框 UI 图经 OCR + `deepseek-v4-flash-vision-exp`，回答使用视觉观察且 `verified=true`
- Commitment E2E：显式承诺自动进入违约，带业务 receipt 后转为迟到履约并保留 breach 历史
- PostgreSQL：空库升级到 Alembic head，并完成隔离 dump/restore 对账

对应机器报告：

- [`evaluation/reports/local-e2e-v1.json`](evaluation/reports/local-e2e-v1.json)
- [`evaluation/reports/local-vlm-e2e-v1.json`](evaluation/reports/local-vlm-e2e-v1.json)
- [`evaluation/reports/local-commitment-e2e-v1.json`](evaluation/reports/local-commitment-e2e-v1.json)
- [`evaluation/reports/local-postgres-restore-v1.json`](evaluation/reports/local-postgres-restore-v1.json)

Swagger UI：<http://localhost:18000/docs>

## 核心链路

```mermaid
flowchart LR
    A[FastAPI + JWT] --> B[Durable Admission]
    B --> C[Router / Planner]
    C -->|EXECUTE| N[Agent Media Requirement L0/L1/L2]
    N --> O[Tesseract OCR / DeepSeek Vision]
    O --> D
    C -->|CLARIFY / OOS| K[Policy Terminal]
    D --> E[Worker + bounded ReAct]
    E --> F[Knowledge / Business Tools]
    F --> G[Evidence + Coverage]
    G --> H[Answer Verifier]
    H -->|PASS| I[PostgreSQL Publication]
    H -->|REJECT / UNKNOWN| J[Handoff]
    I --> L[Redis Current Window Projection]
    I --> M[ACK / Replay]
```

一次 `/chat` 的关键顺序是：

1. JWT 生成可信 Principal，输入安全边界先于模型与记忆访问。
2. PostgreSQL 原子写入请求、Invocation 和 start outbox；相同 `request_id` 可安全重试。
3. Router 产生唯一 RouteDecision；策略终态不启动 Worker。
4. `EXECUTE` 路径构建 TaskGraph，Worker 只看到声明的上下文和允许工具。
5. 已绑定附件由 Agent 决定 L0/L1/L2；OCR/VLM 观察带坐标与 producer provenance，并以不可信数据进入声明它的 Worker scope。
6. Knowledge 与业务工具返回带来源或 receipt 的证据，Coverage 检查必做任务是否完整。
7. Verifier 输出 `PASS / REJECT / UNKNOWN`；只有 `PASS` 进入正常发布。
8. PostgreSQL 先持久化最终回答与 seq，再返回 HTTP；Redis 只投影当前会话窗口。
9. 客户端 ACK 单调推进 `selected → delivered → read`，断线后可按 seq 续取。

## 数据所有权

| 事实 | 唯一 Owner | 说明 |
|---|---|---|
| 请求与 Invocation | PostgreSQL | 准入、固定版本、执行状态和幂等身份 |
| 最终回答与送达 | PostgreSQL | publication、response seq、ACK、重放 |
| Knowledge | PostgreSQL + pgvector/FTS | 原文 revision、chunk、active generation、混合检索 |
| ServiceEpisode / 用户事实 | PostgreSQL | 跨会话服务经历与带来源事实 |
| Attachment | PostgreSQL | 原始字节、checksum、安全状态和 turn binding；OCR/VLM 是派生观察 |
| Commitment | PostgreSQL | 显式来源、due_at、版本 CAS、违约/履约事件和 receipt |
| 当前会话窗口 | Redis | PostgreSQL Conversation 事件的快速投影，可重建 |
| ReAct checkpoint / receipt | 本地持久 Store | 本地 Demo 的审批恢复与副作用防重 |
| Ticket / Handoff | PostgreSQL | 幂等工单、状态转换、事件和 outbox |

仓库已经删除 Chroma、SQLite ResponseDelivery、legacy Knowledge、raw episodic 双路径以及所有 backfill/cutover 协调器。不存在旧数据，因此新安装直接从空 PostgreSQL 初始化。

## Knowledge RAG

```mermaid
flowchart LR
    S[SourceRevision] --> C[Fixed-token Chunk]
    C --> V[pgvector]
    C --> T[PostgreSQL FTS]
    Q[Raw + Standalone Query] --> V
    Q --> T
    V --> R[Weighted RRF]
    T --> R
    R --> P[Context Packer]
    P --> E[Evidence Pack]
    E --> G[Grounded Generation]
    G --> A[Verifier]
```

- 默认 chunk：512 tokens，64 tokens overlap。
- 默认召回：Dense `0.25` + Lexical `0.75`，RRF `k=10`，候选 20，最终 5。
- 本地向量使用确定性 384 维 feature hashing，不下载大型模型或 CUDA 依赖。
- Evidence Pack 保存 source revision、checksum、字符范围、scope、rank 和 manifest fingerprint。
- 订单、退款和账户的实时状态必须来自业务工具，公共知识不能冒充用户私有事实。

## 主要接口

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/health` | 服务和存储状态 |
| `POST` | `/chat` | 完整客服 Agent 主链 |
| `POST` | `/assets/upload` | 本轮附件准入与安全扫描，不自动触发 OCR/VLM |
| `GET` | `/conversations/{conv_id}/turns` | 会话事实视图 |
| `GET` | `/invocations/{invocation_key}` | Invocation 组合视图 |
| `POST` | `/responses/{response_id}/ack` | delivered/read ACK |
| `GET` | `/conversations/{conv_id}/responses` | 按 seq 断线续取 |
| `POST` | `/agent-runs/{run_id}/resume` | 审批后恢复原 ReAct 调用 |
| `POST` | `/knowledge/add` | 添加结构化文本知识 |
| `POST` | `/knowledge/upload` | 上传 UTF-8 txt/md/JSON |
| `GET` | `/knowledge/stats` | Knowledge 后端与 manifest |
| `GET/POST` | `/tickets` | Handoff 工单读写 |
| `POST/GET/PATCH` | `/commitments` | 显式创建、查询和迁移服务承诺；模型不能静默创建 |
| `POST` | `/feedback` | 绑定真实预测的反馈 |
| `GET` | `/metrics` | Prometheus 指标 |
| `POST` | `/eval/run` | 本地分层评测 |

除 `/health`、`/metrics` 外，业务接口需要 Bearer JWT；具体 scope 以 OpenAPI 为准。

## 本地开发与验证

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

TEST_DATABASE_URL=postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot \
PYTHONPATH=. pytest -q
```

空库迁移和恢复演练：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_postgres_migrations.py \
  --database-url postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot

PYTHONPATH=. .venv/bin/python scripts/rehearse_x_t01_restore.py \
  --database-url postgresql://dialogpilot:dialogpilot-local@localhost:15432/postgres \
  --postgres-container dialogpilot-postgres \
  --output evaluation/reports/local-postgres-restore-v1.json
```

## 仓库导航

```text
api/              HTTP 合同与应用装配
application/      Chat、路由、准入、投影、Coverage 等用例层
agents/           Planner、TaskGraph、Worker、ReAct
mcp/              ToolManager、Evidence Pack、RAG 打包与 grounded generation
infrastructure/   PostgreSQL/Redis 仓储、检索、outbox、projection
memory/           当前窗口、上下文与事实提取
services/         Verifier、Ticket、Bad Case、Bundle 等领域服务
evaluation/       数据合同、评分器和机器报告
scripts/          本地迁移、恢复、E2E 与离线实验入口
docs/             已完成的目标架构、实施计划和展示文档
```

重点文档：

- [目标架构](docs/customer-service-agent-target-architecture.zh-CN.md)
- [实施计划](docs/customer-service-agent-implementation-plan.zh-CN.md)
- [架构边界](docs/architecture.md)
- [项目讲述](docs/project-pitch.md)
- [面试追问](docs/interview-guide.md)

## 边界与非目标

- DeepSeek Vision 是显式启用的实验模型；关闭或不可用时 L2 fail-closed，不影响 L0/L1。
- 当前 TraceRecorder 是进程内实现，尚未接入持久 OTel Collector/Langfuse。
- Ticket/Event/Outbox 已由 PostgreSQL 单路径持久化；Bad Case、ReAct checkpoint 和 Bundle metadata 仍是本地 SQLite store。
- 评测集包含 provisional/公开数据映射，不能宣称生产准确率或 human-reviewed Gold。
- 没有生产流量，因此不模拟 Shadow、Canary、promotion、回滚指针、双盲签署或生产 RPO/RTO。
- `MCPToolManager` 是项目内部工具运行时，不是远程 MCP Server。

## License

仓库当前没有独立的开源许可证文件。第三方数据与依赖说明见 [NOTICE.md](NOTICE.md)；在添加明确许可证前，不应默认视为可自由再分发。
