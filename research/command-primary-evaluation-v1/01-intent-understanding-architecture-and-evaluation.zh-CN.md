# Intent / TurnUnderstanding 架构、迁移与评测

状态：`LIVE_L0_DEV_13_OF_20 — NOT_PROMOTED`
目标：将旧的“首句 → 单 intent → 下游执行”改造成 state-first、selective、command-primary 的入口理解链；保留旧 Intent 作为后置兼容投影。

## 1. 最终结论

生产系统每轮都要理解当前输入，但不应每轮重新运行完整 Intent。目标结构是：

```text
Message + TurnStateSnapshot
  ↓
TurnObservations
  ↓
Deterministic Command Compiler
  ├─ RESOLVED → CommandProposal
  └─ UNRESOLVED
       ↓
可选一次历史指代/路由媒体补充
       ↓
Encoder：Flow candidates + ACCEPT/DEFER
       ├─ ACCEPT → registry-backed CommandProposal
       └─ DEFER  → LLM Command Router
                         ↓
                   CommandProposal(s)
                         ↓
                    RoutePolicy
                         ↓
                 ValidatedCommandPlan
                         ↓
                  TurnPlanCompiler
```

Encoder 和 LLM 都没有执行、授权、风险降级或工具选择权。

## 2. 为什么旧 Intent 不能继续做生产主键

当前标签混合了领域、业务对象、对话行为、处置方式和风险，例如 billing、refund、request、handoff、account_security。相同一句话可以同时包含多个维度；相反，多个细标签又可能映射到同一个 Agent。

旧实现还让 intent 参与 Authority requirement 和执行路径，使一个分类错误同时改变：

- owner；
- evidence source；
- risk/approval；
- allowed tool；
- flow transition；
- 最终回复。

因此本迁移的成功定义不是删除 `IntentRecognizer`，而是：

> 修改、清空或随机化 `legacy_intent_projection`，生产 Route、Authority、WorkPlan、工具、副作用与 Publication 全部不变。

## 3. 输入：先有状态，再做理解

每次 invocation 首先读取有限、版本化的 `TurnStateSnapshot`：

```text
recent turns / summary
active flow(s) + versions
pending slot
pending signal / approval
active case candidates
recent tool receipt
reusable media refs
source watermarks + availability
```

`TurnStateSnapshot` 是状态投影，不是 Memory RAG。最近历史不可用时，独立只读查询可以按 policy 降级；pending approval、active flow 或关键状态不可用时，写操作必须暂停，不能猜测继续。

## 4. TurnObservations：事实不能混在一起

入口产生的观察必须保留 epistemic status：

| 类型 | 示例 | 可以推出什么 |
|---|---|---|
| `VERIFIED_STATE` | active flow 是 refund_status | 可以尝试 continuation |
| `PROTOCOL_ASSERTED` | request 绑定 pending signal | 可以做版本验证与消费 |
| `USER_ASSERTED` | “这笔交易不是我操作的” | 提升安全风险，不能证明欺诈成立 |
| `MEDIA_OBSERVED` | 截图显示“退款成功” | 证明截图内容，不能覆盖业务当前状态 |
| `DERIVED` | 文本中识别到订单号候选 | 必须在业务边界验证 |

每条观察至少包含 polarity、source ref、producer/version 和原始 span/locator。Pattern 可以产生带极性的观察或风险升级信号，不能直接降低风险。

## 5. Deterministic Command Compiler

这一层不从自由文本做业务语义分类，只消费已经存在的协议或状态绑定：

- 唯一 pending slot 的 flow-owned 值解析；
- 带 signal ID/version 的 approval/resume 协议请求；
- 客户端明确发送的 cancel/handoff/control command；
- admission/idempotency 已绑定的 resume target。

“还是没到账”、“不要转人工”、“取消刚才那个”即使对人来说很明确，也仍然是自然语言理解问题。active flow 用来缩小候选和提供粘性，不能单凭“只有一个 active flow”就机械推出 continuation。这些输入进入 Encoder/LLM，而不是通过中英文关键词表扩充“确定性”。

当前代码的机械正向路径是 `PendingSlotResolver`；其余输入统一 `DEFER`。

确定性命令的依据表示为：

```text
decision_source = PINNED_WORKFLOW_STATE
resolution_basis = PENDING_SLOT_BINDING | EXPLICIT_RESUME_BINDING | CLIENT_CONTROL_BINDING
semantic_confidence = null
```

不能伪装成分类器 `confidence=1.0`。

## 6. Understanding Context Enrichment

只有两种允许的路由前请求：

```text
HISTORICAL_REFERENCE_RESOLUTION
ROUTING_MEDIA_RESOLUTION
```

例子：

- “之前那个还是不行”且 snapshot 没有唯一引用：取 Top-3 同用户 ServiceEpisode/case candidates；
- “截图里的第二个错误”且文本无法确定 flow：请求最小 OCR/region observation。

固定约束：

```text
max_understanding_enrichment_rounds = 1
```

补充期间禁止业务写操作和无限 Query Rewrite。仍无法解决时进入 Clarify、受控降级或 Handoff。

## 7. Encoder selective fast path

Encoder 第一版只负责：

1. 从版本化 Flow Registry 检索 Top-K candidates；
2. 输出校准后的接受概率/不确定性；
3. 按 flow/risk bucket 做 `ACCEPT` 或 `DEFER`。

它不预测 Authority、最终 risk、allowed tools 或 approval。接受单一 flow 后，由 Registry 模板确定性生成 CommandProposal；模型不得自由构造工具参数。

初始 fast-path allowlist：

- 高精度只读 FAQ；
- 明确低风险单流程查询；
- 仍需轻量语义确认的低风险 continuation。

写操作、安全、多 flow 和 underpowered 类别一律 `DEFER`。

### 7.1 现有 Encoder artifact 的处置

以前的 `classifier.joblib` 可以作历史 baseline 和训练方法参考，但不能接入新 fast path：

- 它预测 9 类旧 Intent，不是 `command_kind + flow_id@version`；
- `refund` 没有分开政策、状态和执行，`other` 没有分开 OOS 和信息不足；
- `0.522578` 是 raw softmax 的全局 cutoff，不是校准后的正确概率；
- 该阈值的 accepted precision 在 heldout / verification / 中文诊断上分别为 `97.07% / 96.15% / 95.71%`；
- 即使改用历史严格阈值 `0.892519`，verification 的 `180/180` 对应单侧 95% 置信下界仍约为 `98.35%`，不足以开放 99% gate。

新 artifact 必须直接预测 Registry candidate，并固定：BGE revision/digest、input renderer version、Registry fingerprint、candidate-to-command mapping、候选级 calibrator/threshold 和数据 checksum。`__DEFER__` 只表示不走 fast path，不是 OOS 类别。

## 8. LLM Command Router

LLM 读取当前消息、Turn State、未解决增量、Flow candidates、允许的命令 schema 和一次补充证据。它只能输出 `CommandProposal`，例如：

```text
ContinueFlow(ref)
StartFlow(flow_id, extracted_inputs)
PauseFlow(ref)
CancelFlow(ref)
AnswerKnowledge(topic)
Clarify(candidate_differences)
RequestHandoff(reason)
NoSupportedFlow(candidate_summary)
```

Provider adapter 拥有 `PROVIDER_FAILURE`；schema/JSON 校验失败是 `INVALID_PROVIDER_OUTPUT`；LLM 自己不得把这些情况输出成 OOS 或信息不足。

当前已有的最小实现将这个边界拆为 prompt renderer、strict output parser
和 Anthropic transport 三个小模块。LLM 目录只包含当前 Registry 中有
ActionDefinition、能编译成 Work 的 command；输出还必须通过
`registry.action_for()` 才会成为 proposal。当前尚未将它设为默认生产
producer，因为新 Encoder artifact 和 conversation-heldout 门禁还未完成。

为了先验证正确性而不让未校准 Encoder 获得决策权，生产组装已提供
显式 `COMMAND_PRIMARY_MODE=structured_knowledge_primary` 切片：

```text
state/deterministic
→ AlwaysDeferCommandEncoder
→ StructuredLLMCommandProducer
→ RoutePolicy / Registry
→ Knowledge Work
→ ChatApplication publication lifecycle
```

该模式的 Encoder calibration 明确是 `not-applicable`；它只建立 LLM-only
correctness baseline。真实 `ChatApplication.handle()` 正向测试已证明旧
`recognize_intent()` 调用为 0。默认模式仍是 `off`，并未将该切片扩大到
写操作、安全或全部 Flow。

2026-09-03 又在锁定合同的 20 条无附件 L0 澄清 slice 上执行了一个真实
`DEV_CONTRACT_DIAGNOSTIC`。每条都由 `AlwaysDeferCommandEncoder` 进入
Structured LLM，再经过 RoutePolicy/Registry 与真实 `ChatApplication.handle()`：

```text
provider calls/errors = 20/0
CLARIFY = 2
NO_SUPPORTED_FLOW = 16
其他错误命令/编译结果 = 2
最终通过 = 2/20 = .10
input/output tokens = 8426/331
mean/P95 = 987.0ms/1361.3ms
```

Knowledge、OCR、VLM 与 Tool 的禁止调用均为 `20/20`；问题集中在 LLM 把
“信息不足，需要澄清”误判成“没有支持的 Flow”。这证明接口和调用边界能
运行，但同时明确否决初版 prompt/model 作为 Intent 替代方案。

随后只修订了一个通用语义合同，没有加入商品、安装或逐 case 关键词：先判断
是否缺少必要对象/指代/资产，再判断请求是否真的超出 Registry 支持范围。两次
Dev 结果从 `2/20 → 11/20 → 13/20`；当前 v3 为：

```text
provider calls/errors = 20/0
CLARIFY 且全部合同通过 = 13
NO_SUPPORTED_FLOW（预期应澄清） = 7
input/output tokens = 6823/261
mean/P95 = 913.8ms/1109.7ms
```

因此这里停止继续追同一 Dev slice。该 20 条已被查看，只能保留为开发回归；
当前 `13/20` 仍然阻止晋级。下一次质量结论必须来自独立、冻结的
conversation-heldout 集，同时包含信息不足、明确可路由和明确 OOS。另已单独
证明 `NO_SUPPORTED_FLOW → OUT_OF_SCOPE` 可以完整留在 command-primary
规则终态中，旧 Intent、Agent 和 Knowledge 均跳过；这只修复主链所有权，
不把 7 个语义误判算成通过。

## 9. RoutePolicy、TurnPlanCompiler 与 Approval

`RoutePolicy` 只验证：

- 命令 schema 和 flow version；
- 当前状态是否允许；
- 多命令是否冲突；
- 租户是否支持目标 flow；
- 风险升级信号是否完整。

通过后得到 `ValidatedCommandPlan`。`TurnPlanCompiler` 再确定性产生：

```text
RouteDecisionV2       # 本轮由谁处理、如何形成回复
FlowTransitionPlan    # 持久 flow 的一个或多个 mutation
WorkPlan              # 具体任务及依赖
```

是否需要再次确认由 Action Registry 决定，而不是 Understanding：

| policy | 含义 |
|---|---|
| `USER_COMMAND_SUFFICIENT` | 当前明确指令、身份和参数已经满足授权 |
| `EXPLICIT_CONFIRMATION_REQUIRED` | 必须展示绑定后的具体操作再次确认 |
| `STRONG_AUTH_REQUIRED` | 必须重新认证或满足更强 authority |

Approval 必须绑定 tool/version、canonical arguments hash、target entity/version、effect、authority 和 expiry；参数或实体版本改变时重新审批。

## 10. 粘性路由与多命令

每条消息创建新的 invocation，但不会重新开始 flow。当前轮 Route 与持久 flow 变化是两个事实：

| 用户输入 | RouteDecision | FlowTransitionPlan |
|---|---|---|
| 退款流程中“谢谢” | DIRECT | KEEP refund |
| “另外发票怎么开” | KNOWLEDGE/MULTI | KEEP refund + START invoice help |
| “退款先停，我的卡丢了” | AGENT/MULTI | PAUSE/CANCEL refund + INTERRUPT security |
| “还是没到账”且唯一 refund case | AGENT_TASK | ADVANCE/KEEP refund |
| 同一句但有两个 active case | CLARIFY | 无 mutation |

`FlowTransitionPlan` 中每个 mutation 独立绑定 expected state version；应用时使用 CAS。

## 11. 评测数据单位

生产 Understanding 的最小 case 不是裸句，而是：

```yaml
case_id: string
initial_state: {}
message: string
expected:
  understanding_status: string
  command_proposals: []
  route_decision: {}
  flow_transition_plan: []
  required_capabilities: []
  forbidden_capabilities: []
  forbidden_side_effects: []
```

同一 message 必须配状态反事实：唯一 flow、多个 flow、无 flow、pending slot、pending approval、过期 signal 等。数据按 conversation 分组，另报告 user/temporal heldout（条件允许时）。

## 12. 分层评测

### 12.1 Deterministic 层

- protocol/state binding precision；
- pending slot/signal consumption accuracy；
- ambiguous fail-closed；
- duplicate/stale consumption；
- Router avoidance；
- property/state-machine tests。

### 12.2 Encoder 层

- Candidate Recall@K；
- Accepted Precision 与 Coverage；
- calibration error；
- OOS boundary；
- LLM fallback rate；
- P50/P95。

若门禁为 Accepted Precision 99%，使用单侧 95% 置信区间下界。对独立发布的策略桶样本不足时标 `INCONCLUSIVE` 并保持 DEFER，不能降低门槛换取上线。

### 12.3 LLM Command Router

- UnderstandingStatus accuracy；
- Command schema validity；
- Command set Precision/Recall/F1；
- flow candidate selection；
- unsupported proposal rate；
- OOS/insufficient/provider-failure 区分；
- clarification utility；
- token、latency、稳定性。

### 12.4 RoutePolicy 与 Flow

- 合法命令接受、非法命令拒绝；
- Flow Transition Macro-F1；
- Wrong-stick、Wrong-switch、Unnecessary reroute；
- state mutation accuracy；
- CAS/concurrency；
- legacy-intent invariance。

### 12.5 级联 E2E

- task success；
- wrong route；
- clarification turns；
- harmful/duplicate side effects；
- Encoder/LLM 调用率；
- token 与 P95。

## 13. 对照方案与数据角色

比较：

1. 当前 V1 LLM/ngram/Pattern；
2. Typed Fusion V2；
3. Encoder → LLM cascade；
4. state-first command-primary。

MASSIVE zh-CN、BANKING77、CLINC150 只证明闭集/OOS 分类能力；它们不能证明状态续接、多命令、Approval 或副作用正确。生产切换依赖新的 conversation-heldout 状态集、80 条项目合同和真实主链 E2E。

历史 V1/V2/Encoder artifacts 可作 baseline、回归和错误 taxonomy；出现过的样本不得重新称为 fresh test。

## 14. 评测前必须完成的 Intent 迁移

1. 冻结 Flow/Action Registry 与产品支持范围；
2. 定义 Understanding/Command/Transition V2 合同；
3. 将 active flow/case/pending state 提到语义路由前；
4. 实现 deterministic compiler 和一次 enrichment；
5. Encoder/LLM 统一输出 CommandProposal；
6. RoutePolicy 与 TurnPlanCompiler 分离；
7. Authority、risk、tool、Knowledge trigger 脱离旧 intent；
8. 实际调用、跳过和消费记录从真实运行 Owner 发出；
9. 新链 Shadow 且零副作用；
10. 通过 legacy-intent 静态依赖与动态不变量门禁。

在第 1–2 项完成后，可以直接开发和评测 UnderstandingPort；只有第 1–8 项完成后，Trigger/Consumption 与真实 command-primary E2E 才有效。

## 15. 产物与通过条件

当前组件入口是 `evaluation/command_primary_eval/understanding.py` 和 `selective_adapter.py`，复用同一 `DirectRunner`。真实 L0 入口是 `scripts/run_locked_l0_clarification_eval.py`；它只保存模型输入/输出摘要、版本和聚合 Token/延迟，不把密钥、prompt 或模型原文写入产物。每次固定输出 `manifest.json`、`predictions.jsonl` 和 `report.json`，并分开记录 Trigger、Artifact、Consumption、Outcome 与 Cost。在生产运行时尚未有统一 trace owner 之前，不为了报表再造一个无消费者的 trace 模块。

发布选择采用字典序：

1. safety/authority/effect 新增失败为 0；
2. 状态转换和任务成功满足非劣；
3. 合格候选中最大化生产 Understanding 质量；
4. 统计不可区分时最小化 LLM 调用率；
5. 再比较 P95、模型大小和维护复杂度。

## 16. 相关文档

- [M0–M4 总迁移计划](./00-m0-m4-migration-master-plan.zh-CN.md)
- [E2E 与成绩汇总](./05-e2e-evaluation-and-scorecard.zh-CN.md)
