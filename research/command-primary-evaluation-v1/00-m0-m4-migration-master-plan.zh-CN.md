# DialogPilot M0–M4 总迁移计划

状态：`DRAFT_FOR_IMPLEMENTATION`
目标：在不重写现有执行、工具、Publication 和 Delivery 基础设施的前提下，将入口从 intent-primary 迁移为 state-first、command-primary，并建立可归因的分层评测。

## 1. 要修复的共同根因

当前 `intent` 同时参与：

- 当前轮语义理解；
- continuation 与 route；
- domain/owner 选择；
- risk 与 required authority；
- Knowledge/Tool 调用；
- 兼容输出与统计。

因此一次 intent 错误会沿整条链传播。目标不是删除一个分类器，而是让每种事实只有一个 Owner：

| 事实或决定 | 目标 Owner |
|---|---|
| active flow、pending signal、状态版本 | Flow State Store |
| 用户表达的候选命令 | Deterministic / Encoder / LLM Understanding Producer |
| 命令是否受支持、是否与当前状态兼容 | RoutePolicy |
| 风险、requirements、approval、allowed tools | Versioned Flow/Action Registry |
| 状态 mutation 的合法提交 | Flow State Store + CAS |
| 知识、历史、媒体、业务状态 | 对应 Evidence/Tool Owner |
| 最终可见回复 | Publication Policy |

## 2. 目标主链

```text
Admission + inbound event/outbox
  ↓
TurnStateSnapshot
  ↓
TurnObservations
  ↓
Deterministic Command Compiler
  ├─ resolved ──────────────────────────────┐
  └─ unresolved                            │
       ↓                                   │
最多一次 Understanding Context Enrichment  │
       ↓                                   │
Encoder ACCEPT/DEFER                       │
       ↓                                   │
必要时 LLM Command Router                  │
       └──────────── CommandProposal(s) ───┘
                           ↓
                    RoutePolicy.validate
                           ↓
                   ValidatedCommandPlan
                           ↓
                    TurnPlanCompiler
          ┌────────────────┼────────────────┐
          ↓                ↓                ↓
 RouteDecisionV2  FlowTransitionPlan      WorkPlan
                                             ↓
                               Per-WorkItem Execution Contract
                                             ↓
                         Evidence / Agent / Tool / Verification
                                             ↓
                              Publication + delivery outbox
                                             ↓
                                  asynchronous projections
```

理解阶段最多允许一次历史指代或路由媒体补充。执行阶段按 WorkItem 设预算和最大循环数，不受“全局一次请求”限制。`NEEDS_INPUT`、`NEEDS_APPROVAL`、`NEEDS_MEDIA` 与 `OUTCOME_UNKNOWN` 都可形成 durable checkpoint，并在后续 invocation 恢复。

## 3. 是否必须完成 M0–M3 才能并行评测

不是。必须区分四种工作：

| 工作 | 最低前置条件 | 能否在 M2/M3 前开始 |
|---|---|---:|
| 整理数据、转换官方格式、复跑历史 baseline | 固定数据版本和历史配置 | 可以 |
| Knowledge/Memory/Media 的 Artifact 组件开发评测 | M1 共享 artifact/trace schema + 各自生产能力前置条件 | 可以 |
| 新 TurnUnderstanding 直接端口评测 | M0 Flow/OOS 合同 + M1 Understanding schema | 可以，但不代表主链已迁移 |
| Trigger、Consumption、Flow Transition 集成评测 | M0–M3 | 不可以 |
| command-primary Shadow、80 条真实主链、τ³ 最终结果 | M0–M3 完成，M4 冻结 Bundle | 不可以 |

因此，正确依赖是：

```text
M0 → M1 → ┬→ Intent 组件开发
          ├→ Knowledge 生产能力与组件评测
          ├→ Memory 生产能力与组件评测
          └→ Media 生产能力与组件评测
M1 → M2 → M3 ─────────────────────┐
四条组件线冻结 ───────────────────┼→ M4 集成、Shadow、最终评测
```

M0–M3 是“集成和最终成绩”的前置条件，不是所有离线实验的统一开工门槛。

## 4. M0：冻结业务语义与历史基线

### 4.1 必须冻结

- Flow Registry：`flow_id/version`、支持租户、required slots、允许 command；
- Action Registry：effect、base risk、requirements、allowed tools、approval policy；
- 产品支持范围与 OOS Owner；
- `INSUFFICIENT_CONTEXT`、`NO_SUPPORTED_FLOW`、Provider failure 的 typed 区分；
- 数据 split、checksum、已消费数据清单；
- 当前 V1、Typed V2、Encoder cascade 和现有 RAG artifacts 的历史身份。

### 4.2 Approval 语义

不能把 `WRITE` 等同于“一律再次确认”。Action Registry 至少表达：

```text
USER_COMMAND_SUFFICIENT
EXPLICIT_CONFIRMATION_REQUIRED
STRONG_AUTH_REQUIRED
```

用户是否已经明确要求执行由 Understanding 表达；这句话是否满足执行授权由 Action/Authority Policy 判断。

### 4.3 M0 出口

- Prompt、Pattern、Encoder mapping、LLM schema 与 scorer 使用同一 Flow/OOS 版本；
- 公共数据集 mapping 不得反向定义产品范围；
- 历史结果标为 `HISTORICAL_BASELINE`，不得冒充新架构成绩。

## 5. M1：建立共享合同、Trace 与直接评测端口

### 5.1 最小合同

```text
TurnStateSnapshot
UnderstandingResult
ContextResolutionRequest
CommandProposal
ValidatedCommandPlan
RouteDecisionV2
FlowTransitionPlan
WorkPlan
```

生产合同只保留当前支持路径真正消费的状态、命令与计划。媒体、知识和历史证据继续使用各自已有的 Evidence artifact；在没有真实运行时 emitter 前，不另建一个汇总一切的 Observation/Trace 超类型。

### 5.2 评测中的计划与实际使用分开

```text
expected invocation
  与
actual direct/runtime result
```

直接评测 adapter 记录 Trigger、Artifact、Consumption、Outcome 与 Cost；E2E adapter 读取现有 Stage、tool audit、state version、Evidence 和 Publication。Evaluator 只比较，不反向制造生产状态。以后只有真实 runtime owner 需要跨能力 trace 时才增加窄事件，不先把所有能力塞入共享 DTO。

### 5.3 M1 出口

- legacy 与 command-primary 对同一 case 产出可比较的评测记录；
- Understanding、Knowledge、Memory、Media 有直接 Port adapter；
- 每次运行统一写 manifest、predictions 和 report；
- 所有版本和数据 provenance 可重放。

## 6. M2：入口改为 state-first、command-primary

### 6.1 调整顺序

当前 `ChatApplication` 在 Memory context 后无条件调用旧 Intent，媒体和 active case 更晚进入。目标顺序必须改为：

```text
Admission
→ load TurnStateSnapshot
→ resolve pending/resume/continuation
→ unresolved delta 才进入 Understanding
→ 必要时一次 route-time historical/media enrichment
```

### 6.2 必须拆开的 API

```text
load_turn_state()                 # 每轮必读，不是 RAG
retrieve_service_episodes()       # 按需 Memory RAG
routing_media_probe()             # 路由所需最小媒体观察
task_media_plan()                 # route 后的任务级媒体证据
```

### 6.3 Flow Transition

一轮可有多个 mutation，因此使用 `FlowTransitionPlan`，而不是单个 kind。每个 mutation 必须绑定 source/target flow、expected version、slot updates、consumed signal 和 reason code。提交使用 CAS；过期、重复或并发消费必须产生 typed outcome。

### 6.4 M2 出口

- 明确 continuation/pending reply 实际不调用 Encoder/LLM；
- active case 和 pending state 参与前置理解；
- Understanding 统一产出 CommandProposal，而不是直接执行；
- 路由前 enrichment 最大轮数为 1，且禁止业务写操作。

## 7. M3：切断旧 Intent 的生产权限并补生产能力

### 7.1 下游迁移

- Authority requirement 从 Flow/Action Registry 编译，不再读取 `route.intent`；
- risk 由 flow/action 基线、升级证据和租户下限确定；
- WorkPlan 为每个 WorkItem 编译 required/conditional/forbidden 组件；
- Knowledge/Memory/Media 请求来自 fact requirements，不来自 intent 关键词；
- Handoff 固定为 Draft → CommitPolicy → 唯一幂等写入 Owner；
- Legacy Intent 只在 ValidatedCommandPlan 之后生成兼容投影。

### 7.2 四条可并行生产能力线

- Intent：Encoder artifact、calibrator、LLM command schema 与 RoutePolicy；
- Knowledge：真实 BGE-M3、原始文本 Dense、中文 FTS、immutable PG generation；
- Memory：ServiceEpisode Dense projection、reference/historical 两套 policy；
- Media：routing probe、task plan，以及公开测试需要的 parser/index/retriever。

### 7.3 M3 出口

- 在生产路径修改 `legacy_intent_projection` 不改变 route、authority、tool、effect 或 publication；
- `rg` 等静态检查只允许 compatibility/analytics/eval 读取旧 intent；
- RouteExecutionContract 与真实工具/Handoff 路径一致；
- 每个能力能分别完成 Artifact 组件评测；
- 故障、无证据与不可用状态均为 typed outcome。

## 8. M4：分组件选择、冻结 Bundle、集成验收与切换

### 8.1 顺序

1. 各组件只在 Dev 上选配置；
2. 冻结模型、Prompt、Registry、阈值、索引 generation、工具 schema 和 evaluator；
3. 运行 Trigger/Consumption 集成测试；
4. 运行 80 条真实 `ChatApplication.handle()` 合同；
5. 运行公开 frozen test；
6. command-primary Shadow；
7. 低风险只读切换；
8. 在硬门禁满足后逐步开放写操作、多 flow 和高风险路径；
9. 最后运行 τ³ RC 或稳定性成绩。

### 8.2 Shadow 禁止事项

Shadow 不得调用业务工具、写 Memory、消费 pending signal、改变 flow、创建 ticket 或创建 Publication，只允许产生 dry-run plan 和 trace。

### 8.3 选择规则

采用字典序，而不是单一加权总分：

1. 跨租户泄漏、越权、重复副作用、stale resume 等 hard failure 新增数为 0；
2. E2E task success 和关键安全/状态指标满足预声明非劣门禁；
3. 组件 Artifact 指标通过；
4. 才比较调用率、Token、延迟和复杂度。

### 8.4 M4 出口

- 一个不可变、可复现的生产 Bundle；
- 组件报告、Trigger/Consumption 报告和 E2E 报告相互可追踪；
- 结果按 `PASS | FAIL | INCONCLUSIVE | NOT_APPLICABLE | NOT_RUN` 发布；
- 只有每题至少四次的 τ³ 运行才报告 `pass^4`。

## 9. 明确非目标

- 不为评测新造第二套生产检索链；
- 不把 Preference Store 作为 command-primary 首发阻塞项；
- 不恢复旧 Chroma 主链；
- 不把父子 Chunk 当成多 requirement 召回缺失的通用修复；
- 不让公共 benchmark 的标签定义产品支持范围；
- 不用一个综合分抵消任何安全、权限或副作用失败。

## 10. 相关文档

- [Intent / TurnUnderstanding](./01-intent-understanding-architecture-and-evaluation.zh-CN.md)
- [Knowledge RAG](./02-knowledge-rag-evaluation.zh-CN.md)
- [Memory RAG](./03-memory-rag-evaluation.zh-CN.md)
- [多模态](./04-multimodal-evaluation.zh-CN.md)
- [E2E 与成绩汇总](./05-e2e-evaluation-and-scorecard.zh-CN.md)
