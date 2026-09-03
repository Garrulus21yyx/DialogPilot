# E2E、故障注入与成绩汇总

状态：`SUPPORTED_SLICES_RUNNING — FULL_80_AND_TAU3_NOT_RUN`
目标：在唯一真实 `ChatApplication.handle()` 主链上同时验证用户结果、状态转换、组件调用、证据消费、副作用、Publication/Delivery 与成本，并用不可被平均分掩盖的方式汇总。

## 1. 统一评测坐标

所有按需能力——Understanding Router、Knowledge、Memory、Media、Agent/Tool——统一测五个维度：

1. **Trigger**：该调用时是否调用，不该调用时是否跳过；
2. **Artifact**：产生的 Command/Evidence/Receipt 是否正确；
3. **Consumption**：下游是否正确使用，是否遗漏、误读或越权；
4. **Outcome**：用户结果和状态转换是否正确；
5. **Cost**：调用数、Token、检索次数、轮数和延迟。

一次失败必须能落入明确链路：

```text
REQUIRED
→ actually invoked/reused
→ artifact correct
→ consumed correctly
→ outcome succeeded
```

不能只看最终答案，因为模型可能猜对答案掩盖检索失败，也可能回答正确但已经发生禁止调用或错误副作用。

## 2. 预期调用与实际观测

### 2.1 预期/运行时决策

```text
expected invocation:
  REQUIRED
  ALLOWED
  FORBIDDEN
  NOT_APPLICABLE
```

Route/WorkItem contract 决定生产计划；测试 case 另有 expected invocation，用于比较。二者是不同 artifact，Evaluator 不能把测试期望注入生产计划。

### 2.2 实际观测

```text
actual observation:
  INVOKED
  REUSED
  SKIPPED
  DEGRADED
  FAILED
```

实际观测优先复用已有 Stage、Evidence、tool audit、state version、Publication 和 provider result，并至少汇总：

```text
request/invocation/span/attempt
capability
reason_code + outcome_code
input/output fingerprint
artifact/evidence refs
producer/policy/model/index versions
latency_ms + token_usage
```

`REUSED` 用于旧媒体 observation 或缓存 artifact；`DEGRADED` 用于有明确受控 fallback 的结果。`FAILED` 与 `NO_EVIDENCE` 不得混同。Evaluator 不得根据最终回答反推调用；也不要求生产代码预先实现一个汇总所有能力的 Trace 超类型。

## 3. E2E case 必须断言什么

每个 case 至少同时断言：

1. User-visible outcome；
2. Understanding/Validated commands；
3. RouteDecision；
4. FlowTransitionPlan 与 state after；
5. expected invocation vs actual observation；
6. Evidence artifact 与 Consumption；
7. Tool/action side effects；
8. Publication、Delivery 与 authoritative events。

示例：

```yaml
case_id: refund_continuation_001
initial_state:
  active_flow: {id: refund_status, version: 4}
  active_case_ids: [R-123]
  pending_signal: null
input:
  message: "还是没到账"
  request_id: req-001
expected:
  commands:
    - continue_flow: refund_status
  flow_mutations:
    - kind: ADVANCE
      expected_version: 4
  route:
    mode: AGENT_TASK
    owners: [billing]
  capabilities:
    deterministic_resolution: REQUIRED
    encoder: FORBIDDEN
    command_router: FORBIDDEN
    knowledge_rag: FORBIDDEN
    memory_rag: FORBIDDEN
    multimodal: FORBIDDEN
    refund_status_tool: REQUIRED
  forbidden_tools: [refund_request_create]
  must_claim: [current_refund_status]
  must_not_claim: [refund_executed_this_turn]
```

## 4. Runner 组织

共享一个评测核心，不共享被测组件实现：

```text
evaluation/
  schemas/
    eval_case
    expected_capability_decisions
    capability_trace
    report_status
  adapters/
    understanding
    knowledge
    memory
    media
    chat_application
    tau3
  runners/
    run_understanding_eval
    run_knowledge_eval
    run_memory_eval
    run_media_eval
    run_e2e_eval
```

现有计划中的 CLI 名称可以保留：

- `run_synthetic_contract_eval.py`：E2E runner 的 80 条合同 wrapper；
- `run_postgres_rag_eval.py`：Knowledge component wrapper；
- `run_memory_rag_eval.py`：Memory component wrapper；
- `tau3_adapter.py`：τ³ 协议 bridge。

command-primary 后还需要一个 `run_understanding_eval.py`；项目媒体 trigger/tier 测量需要 `run_media_eval.py`。它们只是薄入口，不是新 Dataset 平台。

## 5. 组件评测与 E2E 的边界

| 评测 | 是否经过完整 Intent/Route | 用途 |
|---|---:|---|
| Understanding component | 否，直接调用 UnderstandingPort | 测候选命令与状态理解 |
| Knowledge component | 否，直接调用 KnowledgeRetriever | 定位 query/chunk/retrieval/rerank/packing |
| Memory component | 否，直接调用 ServiceEpisodeRetriever | 定位 reference/evidence retrieval |
| Media component | 否，直接调用 PerceptionPort | 定位 tier/artifact/page/region |
| Trigger/Consumption integration | 是，可用受控 mock artifact | 隔离调用策略和下游消费 |
| 项目合同/τ³ E2E | 是，真实生产装配 | 验证整条状态机与副作用 |

Mock artifact 结果不能报告为完整 E2E。最终 E2E 的所有 actual state 只能从生产 Owner、数据库、receipt 和 Trace 读取，不能从 expected 反向制造。

## 6. 80 条合成合同

定位：`SYNTHETIC_CONTRACT_LOCKED`，负责 DialogPilot 特有架构行为，不承担自然分布能力分数。

每条 case 通过真实 `ChatApplication.handle()`，检查：

- state-first 与粘性 flow；
- pending/resume/CAS；
- Knowledge/Memory/Media trigger；
- Evidence/Authority 边界；
- TaskFormation/WorkPlan；
- 工具与副作用；
- Handoff 单一写入；
- Publication/Delivery；
- 故障和 typed outcome。

报告真实 `x/80`，closure gate 是全部 hard assertions 通过。默认一次确定性运行，不产生 `pass^4`。

当前不是 `x/80`。20 条无附件 L0 澄清只作为
`DEV_CONTRACT_DIAGNOSTIC` 使用。初版真实 Structured LLM 为 `2/20`；通用状态
语义合同修订后，v2/v3 依次为 `11/20`、`13/20`，当前 v3 仍有 7 条把信息不足
判成 `NO_SUPPORTED_FLOW`。v3 调用/错误为 `20/0`，Knowledge、Media 和 Tool
仍全部禁止。另有一条独立真实主链正例证明 `NO_SUPPORTED_FLOW` 会由
command-primary 发布 OOS 规则终态，不会回落旧 Intent。

因此当前结论仍是“主链运输可运行，Understanding 质量未过”，不能把同一
Dev slice 的改进外推成合同成绩。其余 60 条以及完整 100 turns 仍为
`NOT_RUN`；新的质量验收必须使用独立冻结的 conversation-heldout 数据。

当前可运行覆盖需要按装配层次分开：

| 层次 | cases / turns | 真实状态 |
|---|---:|---|
| structured eval composition | `20/80` / `20/100` | L0 CLARIFY 已运行，viewed Dev `13/20` |
| owner/execution diagnostic | `2/80` / `2/100` | Policy/Tool `01-a/b` 已接真实 PostgreSQL FlowState 与业务 Owner；固定 completion transport，不计合同分 |
| transport only | `18/80` / `18/100` | 其余 Policy/Tool、Continuity、Memory、Handoff fixture 尚未 hydrate |
| attachment path unsupported by locked adapter | `40/80` / `60/100` | Media+Knowledge/L2/reuse Work 与 fixture 尚未闭环 |

显式生产 `structured_knowledge_primary` 现在也接管现有 CLARIFY terminal；
Product-a 10 条经真实 provider 为 `10/10`，所有禁止能力仍未调用。默认 mode
仍为 `off`。这把一个正向 slice 从评测专用装配移到实际 composition，但完整
合同 manifest 仍是 `NOT_RUN`，不得写成 `10/80` 或 `13/80`。

另一个显式模式 `structured_read_only_primary` 只在 Registry 已接受低风险只读
Action 时增加 `AGENT_TASK`。`dp-policy-01-a/b` 已证明：同一显式 `order_id`
依次绑定 `order_lookup` 与 `refund_eligibility_check`，两份 ToolReceipt 和两份
EvidenceReceipt 使 Coverage 完整后，FlowState 以 CAS 从 version 0 提交到 1，
随后才进行 Verification 与 Publication/Delivery。该切片使用固定 completion
transport，故它是 owner/execution diagnostic，不是 Understanding 成绩，也不
改变完整 80 条的 `NOT_RUN` 状态。

## 7. τ³

`tau3_adapter.py` 只做：

```text
τ³ message/state/tool environment
→ ChatCommand + environment ports
→ ChatApplication.handle()
→ τ³ outcome
```

它不能直接调用 AgentOrchestrator，否则会绕过 Admission、状态恢复、Route/Execution contract、Publication 和 Delivery。官方 reward 由 τ³ evaluator 拥有；项目侧 `safe_pass` 另命名，定义为官方成功且没有 DialogPilot hard violation。

发布等级：

- `RC_PASS1`：197 个任务各一次，只报告 `pass^1`；
- `FINAL_STABILITY`：同一冻结配置每题四次，共 788 次，才报告 `pass^4/safe_pass^4`。

四次运行只为计算 `pass^4`，不是所有组件网格的默认要求。

## 8. 故障注入

至少覆盖：

- Turn State/Memory projection unavailable；
- Knowledge no evidence/backend unavailable；
- Memory no evidence/backend unavailable/conflict；
- OCR partial/VLM timeout；
- tool timeout；
- write outcome unknown；
- duplicate request ID；
- same request ID different input；
- expired approval；
- concurrent consumption of one signal；
- stale entity version；
- bundle/index generation switch；
- delivery disconnect/receipt unknown；
- projection write failure。

每个故障检查：typed outcome、是否出现未经授权 fallback、是否重复副作用、flow 是否错误变化、Publication 是否错误选择。

## 9. 数据切分与统计

按 conversation 分组，条件允许时另做 user-heldout 和 temporal-heldout。禁止把同一会话 turn 当完全独立样本扩大分母。

报告切片：

```text
zh / en / code-switch
read-only / write / security
OOS / insufficient-context
continuation / multi-flow
media / historical-reference
normal / fault-injected
```

质量比较使用相同 case/trial 的 paired 统计。Bounded quality 指标使用预声明非劣 margin 和 conversation-level paired bootstrap。安全、权限和副作用采用 all-pass hard gate，不能由平均分抵消。

Encoder accepted precision 使用适当的二项置信区间下界；样本不足为 `INCONCLUSIVE`。有限测试中的 `0/N` 写成 observed zero，并同时给出上置信界。

## 10. 汇总方式：禁止一个加权总分

最终报告采用四层 scorecard。

### Layer A：Hard Gates

新增任一项即拒绝发布：

- cross-user/cross-tenant leakage；
- unauthorized tool；
- duplicate side effect；
- stale resume；
- pending signal 重复消费；
- wrong/stale media publication；
- unsupported business claim；
- FORBIDDEN capability 被调用。

### Layer B：状态与 E2E 质量

- Task Success；
- Flow Transition Macro-F1；
- Wrong-stick/Wrong-switch；
- Clarification Utility；
- Tool correctness；
- 80 条合同 `x/80`；
- τ³ `pass^1/pass^4`（按实际 trial）。

### Layer C：组件质量

- Understanding accepted precision/coverage、OOS、Command F1；
- Knowledge All-evidence/Packed Recall、grounding；
- Memory reference/Episode Recall、temporal/supersession；
- Media tier、page/region Recall、grounding。

不同 benchmark 不能拼成一个平均 Accuracy。

当前已完成的独立组件基线只有 Knowledge 英文 heldout：官方 Doc2Dial test
中机械冻结 120 个互不重复的 conversation，固定配置下 Candidate@20
All-evidence 为 `78/120=.6500`，Packed Top-5/2600 为 `65/120=.5417`，
120/120 retrieval 为 `OK`。这是一项 `HELDOUT_FIXED_BASELINE`，没有与
legacy 做同集 paired comparison，也尚未覆盖中文/code-switch、生成 grounding
或 E2E Trigger/Consumption，因此状态是“已测基线、未晋级”，不能与 Intent、
Memory、Media 或完整 80 条合同合成一个总分。

### Layer D：成本

只有 A–C 合格后才用于选择：

- Encoder/LLM/RAG/VLM/Tool invocation rate；
- tokens；
- model/retrieval calls；
- conversation turns；
- P50/P95；
- 人工转接率。

选择顺序为安全硬门禁 → E2E 非劣 → 组件门禁 → 成本，不使用单一权重求和。

当前已经实现一个只读 Scorecard 索引器，而不是新的评分 Owner：

```text
explicit run catalog
→ verify manifest/report run_id + SHA-256
→ read canonical status
→ project declared report JSON Pointers into Layer A/B/C/D
→ scorecard.json
```

同一 run 可以分别贡献 Layer C 质量和 Layer D 成本。`COMPLETED` 仅表示运行
结束，若 report 没有 canonical judgment，索引结果必须是 `INCONCLUSIVE`；
缺文件、run ID 或摘要不一致是 `BLOCKED`。索引器不读取 predictions、不重算
指标、不生成 `overall_score` 或 `weighted_score`。

## 11. 标准产物

每次 run 的共同索引统一产生：

```text
manifest.json
predictions.jsonl
report.json
```

`predictions.jsonl` 中直接引用本次运行实际产生的 Stage、evidence、tool audit、state transition 与 Publication；当数据量较大时可另附 query/candidate/evidence capture，但不复制一套生产事实。Manifest 固定 dataset/split/checksum、配置/模型/Prompt/Registry、index generation、tool/evaluator version、trial count、随机种子和环境。

每个指标必须携带 numerator/denominator、slice 和状态：

```text
PASS
FAIL
INCONCLUSIVE
NOT_APPLICABLE
NOT_RUN
BLOCKED_MISSING_SYSTEM_CAPABILITY
```

汇总入口为：

```bash
python scripts/build_command_primary_scorecard.py \
  --catalog path/to/run-catalog.json \
  --output path/to/scorecard.json
```

Catalog 只声明 run artifact、固定摘要、所属 layer 和要展示的 report JSON
Pointer；它不能声明或覆盖评测结果。

失败或基础设施错误不能静默删除后重跑到成功。

## 12. 最终对外成绩单

只保留以下 headline，详细归因留在附表：

1. Understanding：accepted precision/coverage、OOS、LLM 路由调用降幅；
2. Knowledge：All-evidence/Packed Recall 与 MRR/nDCG；
3. Memory：Reference/Episode Recall、temporal accuracy、context token 降幅；
4. Multimodal：L2/Page/Region Recall、VLM 调用降幅；
5. E2E：80 条合同 `x/80`、τ³ official/safe pass、工具正确率、P95。

任何“降低/提升”均使用相同 case/trial 的 paired run，并同时报告质量门禁是否满足。

## 13. 最终发布门禁

只有以下条件同时成立才可把 command-primary 标为生产 Owner：

- M0–M3 迁移完成；
- 一个冻结 Bundle 通过组件 heldout；
- Trigger/Consumption integration 通过；
- 80 条真实主链 hard assertions 通过；
- Shadow 零副作用且 legacy intent 无生产影响；
- 低风险切换满足 E2E 非劣；
- 对应范围的安全、权限、幂等与故障门禁通过。

## 14. 相关文档

- [M0–M4 总迁移计划](./00-m0-m4-migration-master-plan.zh-CN.md)
- [Intent / TurnUnderstanding](./01-intent-understanding-architecture-and-evaluation.zh-CN.md)
- [Knowledge RAG](./02-knowledge-rag-evaluation.zh-CN.md)
- [Memory RAG](./03-memory-rag-evaluation.zh-CN.md)
- [多模态](./04-multimodal-evaluation.zh-CN.md)
