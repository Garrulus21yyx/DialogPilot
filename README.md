<div align="center">

# DialogPilot

<p align="center">
  <img src="assets/readme/hero.svg" width="100%" alt="DialogPilot 智能电商客服：汇合政策证据与订单事实，确认动作后受控执行并保存回执。架构示意，非运行结果截图。">
</p>

**智能电商客服 · 多 Agent 协作、知识问答与售后处理**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-146C43)
![LangChain](https://img.shields.io/badge/Agent-LangChain-1C3C3C)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)

[核心亮点](#核心亮点) · [快速开始](#快速开始) · [整体架构](#整体架构) · [评测与优化闭环](#评测与优化闭环)

</div>

---

DialogPilot 是一个智能电商客服系统，支持政策咨询、产品指引、订单查询与售后处理，通过多 Agent 协作理解多轮需求、检索适用知识并调用业务工具。项目以 **LangGraph 编排任务、LangChain 执行领域 Agent、PostgreSQL 保存权威事实**，串联需求理解、知识问答与业务办理。

例如，用户提出“查一下这笔订单是否符合退款政策，符合的话帮我申请”，系统需要同时获得订单事实和适用条款，补齐必要信息，展示待确认动作，并在授权后执行、记录回执。

可查看[真实鉴权示例及历史运行报告](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/evaluation/reports/local-e2e-v1.json)，或阅读[三套公开数据检索对照](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/docs/rag-retrieval-selection-three-2026-09-08.zh-CN.md)。上图是源码机制示意；报告的结论限定于各自运行版本与数据范围。

> 支持 Docker Compose 本地部署。下文的实验结果限定于所标注的数据、配置与运行版本。

## 核心亮点

**多 Agent 协作，有明确的任务边界。** 会话状态优先续接，ConversationAgent 生成计划，LangGraph 按依赖调度领域 Worker；独立只读任务并行，补充输入与审批沿原任务继续。

**上下文可以压缩，原始证据可以回查。** 联合计算提示、工具与历史预算，归档工具原文，清理旧观察并压缩历史；需要细节时分页回读，减少重复业务调用。

**政策与订单分别取证，回答保留来源。** pgvector 与中文 BM25 混合召回，经过查询改写、RRF、精排与预算装配；知识证据保留版本和原文位置，实时业务事实保留工具回执。

**合成与核验按需介入。** ResultBoard 汇总事实，ResponseAssembler 选择模板、直通或合成；需要核验的回答绑定正文与证据，避免额外模型调用成为每轮必经步骤。

**优化有证据，也保留失败。** 通过 Trace 定位查询、召回、精排、打包、生成与执行问题，固定预算比较救回与误伤。公开检索数据和中文业务模拟集分别报告。

## 快速开始

需要 Docker Compose 和支持 Anthropic Messages 协议的模型服务。容器使用 Python 3.12；模型端点与角色配置见 [`.env.example`](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/.env.example)。

本文架构与运行配置对应 [`feat/customer-service-target-architecture`](https://github.com/Garrulus21yyx/DialogPilot/tree/feat/customer-service-target-architecture) 分支。已克隆仓库时，先切换到该分支：

```bash
git switch feat/customer-service-target-architecture
```

**1. 配置环境**

```bash
cp .env.example .env
```

修改 `.env` 中的凭据，并按所用服务核对模型名、端点：

```dotenv
ANTHROPIC_API_KEY=your_api_key
AUTH_JWT_SECRET=replace_with_at_least_32_random_bytes
```

**2. 启动本地栈**

```bash
docker compose up -d --build --remove-orphans
curl http://localhost:18000/health
```

访问 [Swagger UI](http://localhost:18000/docs) 查看接口和鉴权要求。Compose 包含应用、PostgreSQL、Redis、Nginx 与 Prometheus。

**3. 运行真实鉴权示例**

在主机建立 Python 3.12 环境，安装示例所需的运行依赖：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

PYTHONPATH=. python scripts/run_local_e2e.py \
  --output evaluation/reports/local-e2e-v1.json
```

脚本读取本地配置，执行鉴权、附件上传和客服对话，输出脱敏报告；此步骤会调用配置的模型服务。

## 整体架构

```mermaid
flowchart TB
    Client[客户端 / 多轮消息 / 附件] --> API[FastAPI · JWT 身份与输入边界]
    API --> Admission[持久准入 · request_id / Invocation]
    Admission --> Conversation[会话状态续接与 ConversationAgent 规划]
    Conversation --> Plan[WorkPlan · 依赖与工具权限]

    subgraph Runtime[LangGraph 任务编排]
        Plan --> Dispatch[调度就绪任务]
        Dispatch --> Workers[领域 Worker · LangChain create_agent]
        Workers --> Read[只读业务工具 / Knowledge RAG]
        Workers --> Action[动作准备 / 用户审批 / 受控写入]
        Workers --> Media[按需媒体感知 · L0 / OCR / VLM]
        Read --> Board[ResultBoard · 结果 / 证据 / 进展]
        Action --> Board
        Media --> Board
    end

    Board --> Assembly[ResponseAssembler · 模板 / 直通 / 按需合成]
    Assembly --> Gate[对应响应路径的完整性与证据检查]
    Gate --> Publication[Publication · 持久回答与 response_seq]
    Publication --> Delivery[客户端接收 / ACK / 断线重放]
    Board --> Followup[补充输入 / 审批 / 后续任务]
    Followup --> Conversation

    PG[(PostgreSQL · 权威事实与持久执行状态)] -.-> Admission
    PG -.-> Runtime
    PG -.-> Publication
    Publication --> Redis[(Redis · 可重建会话投影)]
    Runtime -.-> Trace[脱敏 Trace / Prometheus / 可选 Langfuse]
```

### 1. 分层决策与多 Agent 协作

- **会话层决定做什么：** 先处理已有任务、待补信息和审批状态，再由 ConversationAgent 形成结构化提案，经策略与编译边界转为 WorkPlan。
- **编排层决定何时做：** LangGraph 根据依赖派发任务。独立只读任务可并行；当前单会话审批槽约束下，具备动作能力的 Worker 串行调度。
- **领域层决定如何取证：** 六个领域共用 `create_agent` 工具循环，按任务授权暴露工具；Skill 提供可选复合能力。任务结果进入 ResultBoard，保留成功、部分完成、等待与失败等结构化状态。
- **回答层决定如何表达：** 简单结果使用模板或适用的直通路径；复杂结果按需调用合成器。核验检查证据支持与需求覆盖，发布结果绑定具体正文。普通补充问题有独立完整性路径，不需要每轮都增加一次模型评审。

可选 Encoder 路由通过显式开关与模型制品状态管理，示例配置默认关闭。核心入口见 [`turn_runtime.py`](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/application/turn_runtime.py)、[`orchestration_runtime.py`](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/application/orchestration_runtime.py) 和 [`response_assembly.py`](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/application/response_assembly.py)。

### 2. 上下文预算与可追溯证据

上下文管理覆盖一次实际模型请求中的系统提示、工具 Schema、历史消息与输出预留。长工具结果保存原文，模型使用摘要或引用按需分页回读；旧工具观察与历史消息逐步清理、压缩，并保留当前目标及必要工具交互。

归档内容带作用域与校验信息。业务回执、知识来源和媒体观察保留各自身份；OCR/VLM 的识别内容作为观察使用，不能独立证明订单状态或执行成功。实现见[预算管理](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/application/context_budget.py)、[上下文压缩](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/infrastructure/target_context_compaction.py)和[结果归档](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/infrastructure/target_result_archive.py)。

### 3. 持久执行与数据所有权

| 数据与状态 | 持久化位置 | 责任 |
|---|---|---|
| Conversation / Invocation / 请求准入 | PostgreSQL | 会话事实、任务状态和请求幂等身份 |
| LangGraph checkpoint / Store | PostgreSQL | 保存执行位置与恢复所需上下文 |
| Operation ledger / receipt | PostgreSQL | 记录副作用状态、执行回执与对账依据 |
| Knowledge / source revision / generation | PostgreSQL + pgvector | 来源版本、适用范围、词法与向量检索 |
| Publication / response seq / ACK | PostgreSQL | 最终回答、送达状态和断线重放 |
| 附件 / 服务经历 / 用户事实 / 工单 / 承诺 | PostgreSQL | 原始对象、来源绑定与领域状态转换 |
| 当前会话窗口与缓存投影 | Redis | 加速读取；可由持久事实重建 |

恢复需要同时回答两个问题：checkpoint 决定**执行从哪里继续**，操作账本与回执决定**业务动作是否已经发生**。已提交操作复用回执；结果未知的操作需要对账，不能把异常直接解释为“没有执行”。回答同样区分持久发布、客户端送达与已读，通过序号与 ACK 续接。

## Knowledge RAG：从资料到可引用证据

```mermaid
flowchart LR
    subgraph Offline[离线知识构建]
        Source[原文与适用信息] --> Revision[SourceRevision · 版本与位置]
        Revision --> Chunk[结构感知切块]
        Chunk --> Index[词法索引 + pgvector 向量索引]
    end
    subgraph Online[在线检索与打包]
        Context[会话上下文 / 完整问题] --> Query[按查询模式改写与保留原问题]
        Query --> Scope[身份与知识适用范围]
        Scope --> Recall[Dense + 中文 BM25]
        Index --> Recall
        Recall --> Fusion[Weighted RRF · 去重与候选截取]
        Fusion --> Rerank[可配置精排]
        Rerank --> Pack[预算装配 · Evidence Pack]
    end
    Pack --> Answer[领域回答 / 结果合成 / 对应核验]
```

**设计重点**

- **完整问题进入检索：** 区分需要结合历史改写的查询和已经解析完整的查询；原始问题与独立问题按配置融合，避免只拿多轮追问中的短句检索。
- **适用范围先于相关性：** 由系统绑定身份与适用信息。知识来源保留版本、时间和范围，避免把不同商品或渠道的相似条款混为有效证据。
- **候选排序与证据打包分别评估：** 高相关片段不一定覆盖所有必要条件；同时检查候选池、精排 Top-K 和模型实际可见正文。
- **来源可回查：** Evidence Pack 保留 source revision、checksum、原文范围与检索信息，支持引用核对和失败重放。实时订单、退款或账户状态仍由业务工具提供。

**按 `.env.example` 启动的基线配置**

| 配置项 | 示例值 / 支持方式 |
|---|---|
| 切块 | `structure_aware`，512 tokens / 64 overlap；支持 `markdown_headers` 可选策略 |
| 向量与词法融合 | Dense `0.50` + Lexical `0.50`，RRF `k=10` |
| 原始与独立查询 | `0.25 / 0.75`；默认不启用额外扩展查询 |
| 预算 | 候选 20，最终最多 5 段，正文预算 2600 tokens |
| Embedding | 默认 `hash_baseline`，用于轻量可复现启动；可显式配置本地固定版本 `bge_m3` |
| 精排 | 默认 `listwise`，使用配置的 RERANK 角色模型；可选本地 `local_bge` |

运行时以加载的 Bundle 策略为准，定义见 [`core/rag_policy.py`](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/core/rag_policy.py)。Compose 的部分无环境变量回退值仍与示例不同，因此启动前应复制 `.env.example`；不要把历史报告中的模型与参数视为当前默认。BGE 模式需要单独配置本地模型制品。

## 评测与优化闭环

```mermaid
flowchart LR
    Cases[真实失败 / 固定评测样本] --> Diagnose[定位查询 / 召回 / 精排 / 打包 / 生成 / 执行]
    Diagnose --> Hypothesis[明确假设与采用门槛]
    Hypothesis --> Pair[固定数据与预算对照 / 模块消融]
    Pair --> Audit[救回与误伤 / 质量 / 成本 / 延迟]
    Audit --> Decision[采用或保留失败结论]
    Decision --> Regression[回归与新留存验证]
    Regression --> Cases
```

| 评测层 | 数据与场景 | 关注的指标与需求 |
|---|---|---|
| 条款取证 | Doc2Dial | span 级证据与整题必要证据覆盖，检查政策条件和例外是否找全 |
| 多轮检索 | MTRAG | passage 级 Recall / MRR / nDCG，检查历史指代下的片段召回 |
| 产品指引 | WixQA | article 级定位与排名，检查操作指引文档是否正确 |
| 中文电商知识验收 | 政策条件、多轮追问、产品指引及适用范围干扰的模拟语料 | 模型实际可见的完整必要证据、错误范围来源、答案需求覆盖与支持性 |
| Agent 与业务链路 | 真实 Agent 入口、工具交互、审批续接及外部任务适配 | 任务执行、协议行为、业务结果、Token 与延迟，和检索分数分别报告 |

不同数据集的文章、片段与 span 标注不混算。模型核验通过、合法引用、完整证据和最终答案正确是不同指标。模拟数据、已消费的开发/回归样本、独立留存和真实业务流量也分别标明。

现有工作包括融合权重对照、Metadata 适用范围贯通、Markdown 标题切块实验，以及 BM25 访问路径与性能诊断。**局部指标改善不代表整体质量验收完成**；当前仍有多证据排序、查询语义、答案覆盖和词法检索性能缺口，reranker 微调保持暂停。

- [三套公开数据的检索选型与对照](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/docs/rag-retrieval-selection-three-2026-09-08.zh-CN.md)
- [中文电商范围过滤对照](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/docs/ecommerce-scoped-rag-2026-09-08.zh-CN.md)
- [答案质量与失败归因](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/docs/ecommerce-answer-quality-2026-09-09.zh-CN.md)

## 技术栈与代码导航

| 层级 | 技术 | 用途 |
|---|---|---|
| 接入 | FastAPI · Pydantic · JWT | 类型化 HTTP 接口、身份和输入校验 |
| 编排与执行 | LangGraph · LangChain | 父图调度、领域工具循环、中断与 checkpoint |
| 模型接入 | Anthropic SDK / 兼容端点 | 按规划、Worker、改写、精排、合成与核验角色配置模型 |
| 结构化生成 | PydanticAI | 独立 RAG grounded structured-output 边界 |
| 数据与检索 | PostgreSQL · pgvector · 中文 BM25 · Redis | 权威事实、知识检索、投影与缓存 |
| 多模态 | Tesseract OCR · 可选 VLM | 经鉴权绑定的附件按需感知 |
| 观测与交付 | Prometheus · 可选 Langfuse · Docker Compose · Alembic | 指标、脱敏追踪、部署与数据库迁移 |

```text
api/              HTTP 合同、鉴权入口与应用装配
application/      会话决策、WorkPlan、任务编排、上下文、检索与回答发布
core/             共享合同、模型角色、预算与检索策略
infrastructure/   LangChain Agent、PostgreSQL/Redis 适配、checkpoint 与索引
agents/           媒体感知需求等辅助能力
mcp/              内部工具运行时、证据打包与结构化生成
memory/           会话记忆、上下文与事实提取
services/         答案核验、工单、Bad Case 等服务
skills/           可选的复合任务能力
evaluation/      数据合同、评分器与评测报告
scripts/          本地演示、数据库迁移、恢复与实验入口
tests/            合同、状态转换、集成与回归测试
docs/             技术文档与实验说明
plans/            持续工作状态、实验预注册与证据索引
```

## 开发与可选能力

<details>
<summary><strong>测试、数据库迁移与恢复演练</strong></summary>

在已建立的虚拟环境中安装开发依赖。测试应连接可用于测试的本地数据库：

```bash
pip install -r requirements-dev.txt

TEST_DATABASE_URL=postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot \
PYTHONPATH=. pytest -q
```

数据库迁移与隔离恢复演练：

```bash
PYTHONPATH=. python scripts/run_postgres_migrations.py \
  --database-url postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot

PYTHONPATH=. python scripts/rehearse_x_t01_restore.py \
  --database-url postgresql://dialogpilot:dialogpilot-local@localhost:15432/postgres \
  --postgres-container dialogpilot-postgres \
  --output evaluation/reports/local-postgres-restore-v1.json
```

[文本与 OCR 示例报告](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/evaluation/reports/local-e2e-v1.json)、[视觉示例报告](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/evaluation/reports/local-vlm-e2e-v1.json)、[承诺生命周期报告](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/evaluation/reports/local-commitment-e2e-v1.json)、[持久 Trace 报告](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/evaluation/reports/local-trace-e2e-v1.json)和[恢复报告](https://github.com/Garrulus21yyx/DialogPilot/blob/14ce43e/evaluation/reports/local-postgres-restore-v1.json)是各自运行时的历史证据；当前版本验证应重新运行对应入口。

</details>

<details>
<summary><strong>按需视觉与可观测性</strong></summary>

媒体感知分为 L0、Tesseract OCR L1 和可选 VLM L2。附件先经过认证上传、安全扫描与本轮绑定；识别结果保留 checksum、位置和生产者信息。

启用 VLM 时，在 `.env` 设置 `VLM_ENABLED=true`，并核对 `MODEL_VISION` 与 `VLM_BASE_URL` 是否支持图片输入，然后重建应用并运行示例：

```bash
docker compose up -d --build dialogpilot nginx
PYTHONPATH=. python scripts/run_local_vlm_e2e.py \
  --output evaluation/reports/local-vlm-e2e-v1.json
```

Langfuse exporter 默认关闭。本地 Trace 使用脱敏 PostgreSQL span；需要外部导出时显式配置：

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

</details>

<details>
<summary><strong>主要接口</strong></summary>

| 接口 | 用途 |
|---|---|
| `POST /chat` | 提交客服对话与任务 |
| `POST /assets/upload` | 认证附件上传与本轮绑定 |
| `GET /conversations/{conv_id}/responses` | 按序号读取已发布回答 |
| `POST /responses/{response_id}/ack` | 更新送达 / 已读状态 |
| `POST /knowledge/add`、`/knowledge/upload`、`/knowledge/withdraw` | 知识导入与撤回 |
| `GET /knowledge/stats` | 知识后端与索引信息 |
| `/tickets`、`/commitments` | 人工工单与显式服务承诺 |
| `GET /traces/{trace_id}`、`GET /metrics` | 持久 Trace 与运行指标 |
| `POST /eval/run` | 评测入口 |

业务接口使用 Bearer JWT，具体方法、参数、异步任务查询与 scope 以运行实例的 [OpenAPI](http://localhost:18000/docs) 为准。

</details>

## 使用边界与许可

当前没有生产流量；公开数据映射与模型生成的模拟合同不等于人工审定 Gold 或生产准确率。VLM、本地 BGE 与 Encoder 需要各自的显式配置。`MCPToolManager` 是内部工具运行时，不能据此认为仓库提供了远程 MCP Server。

仓库当前没有独立开源许可证文件。第三方数据与依赖说明见 [NOTICE.md](NOTICE.md)；再分发前需确认适用许可。
