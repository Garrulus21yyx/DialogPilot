# Memory RAG 迁移与评测

状态：`DRAFT_FOR_IMPLEMENTATION`
目标：将每轮状态恢复与长期记忆检索彻底分开，并分别验证 ServiceEpisode、Commitment 和可选 Preference 的事实语义、检索目的与安全边界。

## 1. 最重要的概念分离

```text
Turn State                           Memory RAG
每轮必读                             按需调用
active flow/pending/case/recent turn 历史 episode/承诺来源/过去材料
状态投影                              语义/词法检索
用于恢复当前执行                      用于解析旧指代或补历史证据
```

目标 API：

```text
load_turn_state()                 # 不接受语义 query，不做 Top-K
retrieve_service_episodes()       # 明确请求时才检索
resolve_commitment_state()        # 读取 Commitment Authority
```

现有对话 history/summary 可以构成 TurnStateSnapshot，但不能因为每轮有消息就默认运行 ServiceEpisode RAG。

## 2. 长期信息的三个 Owner

### 2.1 ServiceEpisode

表达已经关闭或解决的客服经历：

- 症状和目标；
- 执行过的动作；
- 经过权威 Owner 接受的结果；
- 解决方案/根因；
- 业务实体与来源事件；
- evidence refs。

未经验证的助手文本不能直接升级为 ServiceEpisode 权威事实。

### 2.2 Commitment

旧对话只能证明“曾经有人承诺过”。回答当前是否逾期或是否已经履行需要：

```text
Conversation/Memory evidence
→ 找到 commitment_id/source
→ Commitment Authority
→ 当前状态、期限、履行/取消/替代
```

### 2.3 Preference

永久偏好应使用 typed store，并包含 scope、来源、确认状态、生效/失效时间和 supersedes。一次临时表达不得自动升级为永久偏好。

Preference 不是 command-primary 首发阻塞项；只有产品范围明确需要时再实现和评测。

Raw Conversation Event 是这三类投影的来源，不属于 Memory RAG 索引中的权威结论。

## 3. Memory RAG 的两个调用位置

### 3.1 路由前：Reference Resolution

只处理当前 snapshot 不能唯一解析的历史指代：

- “之前那个”；
- “上次的问题”；
- “跟前一次一样”；
- “你们答应过的”。

先用结构化约束缩小范围：

```text
tenant/user
active case candidates
entity IDs
explicit time
product/version
flow family
```

再做 lexical/dense 检索。输出是候选 episode/case/reference、来源和是否足以唯一绑定，不直接输出公共答案或执行命令。

Understanding enrichment 总轮数仍为 1。无唯一绑定时必须 Clarify。

### 3.2 路由后：Historical Evidence

Route/WorkItem 已确定后，按 requirement 检索：

- 历史解决方案；
- 此前执行过的动作；
- 承诺来源；
- 用户曾提交的材料；
- 相似但已验证的 ServiceEpisode。

Memory evidence 不得覆盖业务当前状态；需要当前事实时仍调用 Business Tool/Commitment Authority。

## 4. 评测前必须完成的生产迁移

1. 拆分 TurnState loader 与 ServiceEpisode Retriever；
2. 将 active flow/case/pending state 放在 Understanding 前；
3. ServiceEpisode canonical text 生成真实、版本化 Dense embedding；
4. 固定 model/digest/dimension/preprocessing/index generation；
5. tenant/user/filter 在召回前生效，不能依赖后过滤；
6. locator 能回到 episode revision、source event 和 provenance hash；
7. `NO_EVIDENCE`、`BACKEND_UNAVAILABLE`、`INVALID_CONTRACT`、`CONFLICT` 分开；
8. 建立 reference-resolution 与 historical-evidence 两套 policy；
9. Commitment 当前状态由独立 Authority 提供；
10. CapabilityDecision/Trace 区分未调用、复用、降级和失败。

## 5. 为什么不能照抄 Knowledge RAG 权重

Knowledge 查询通常寻找公共说明；Memory 查询同时受到主体、实体、时间、版本、替代关系和隐私范围约束。即使两者共享 PG、embedding、lexical 与 RRF 基础设施，也不能共享：

- corpus；
- weights；
- relevance threshold；
- freshness；
- top-k；
- authorization filter；
- Gold 与错误代价。

当前 `.30/.60/.10,k=60` 只作 baseline。Recency 只能排序已经相关的候选，不能替代相关性；用户明确指定旧日期或实体时，旧记录不能被统一 freshness cutoff 错误删除。

## 6. 两套检索 policy

### 6.1 `memory-reference-resolution-v1`

目标是唯一绑定历史对象，优先考虑：

- tenant/user hard filter；
- entity/time/flow constraints；
- lexical exact match；
- semantic similarity；
- ambiguity margin；
- Top-K candidate coverage。

输出必须表达 `UNIQUE_BINDING | AMBIGUOUS | NO_EVIDENCE | FAILED`。

### 6.2 `memory-historical-evidence-v1`

目标是满足 WorkItem 的历史 fact requirements，优先考虑：

- Evidence Recall；
- entity/product/version 一致性；
- supersession；
- temporal validity；
- provenance；
- 是否足以支持当前 claim。

这两套 policy 可以共享一次 lexical/vector candidate capture，但不能强制共享最终融合权重和 gate。

## 7. 测试集必须覆盖的情况

- 两个非常相似的历史 case；
- 唯一 active case 已足够，因此 Memory 必须跳过；
- 没有 active state，且历史有一个唯一候选；
- 历史有多个可能候选，需要澄清；
- 旧方案被新方案替代；
- 同一问题跨产品版本；
- 用户明确指向较旧日期；
- episode 存在但证据不足以支持当前结论；
- Commitment source 存在但当前状态已改变；
- 另一个用户/租户有高度相似 episode；
- backend unavailable；
- projection watermark 落后或 conflict。

## 8. 分层评测指标

### Trigger

- Memory Trigger Precision/Recall；
- Active-state-sufficient skip accuracy；
- Forbidden Invocation Count；
- Reference vs Historical purpose classification。

### Artifact

- Reference Resolution Accuracy；
- Episode Recall@K / All-evidence Recall；
- Entity-bound Recall@K；
- MRR/nDCG；
- Temporal Validity Accuracy；
- Supersession Accuracy；
- Conflict Detection；
- Provenance Completeness。

### Consumption

- 正确 evidence 是否进入对应 requirement；
- 旧 episode 是否被当作当前状态；
- Commitment source 与 current state 是否正确组合；
- Unsupported Memory Claim；
- False Memory Use Rate。

### Outcome/Safety

- historical-reference task success；
- clarification utility；
- wrong-case continuation；
- Cross-user/Cross-tenant Leakage = observed `0/N`；
- 未授权历史材料使用 = observed `0/N`。

### Cost

- Memory invocation rate；
- candidate/retrieval count；
- context token reduction；
- P50/P95；
- 额外澄清轮数。

## 9. 数据角色与调参

- LoCoMo：按完整 conversation/group 切 Dev，选择 fusion、top-k、threshold 与时间规则；
- LongMemEval：配置冻结后的外部测试，不用于回调参数；
- 80 条合成合同：Memory 是否应调用、连续服务、Commitment 与跨用户边界；
- 项目状态反事实：active state sufficient、多个历史引用、过期/替代记录；
- 历史 ServiceEpisode replay：生命周期和 provenance regression。

确定性 candidate capture 和离线重放默认一次；涉及 LLM answer/clarification 时，在关键切片重复。

## 10. Runner 与产物

组件入口：`run_memory_rag_eval.py`。

真实路径：

```text
canonical ServiceEpisode projection
→ PG lexical/vector generation
→ purpose-specific policy
→ MemoryEvidencePack
```

输出 manifest、candidate capture、case results、EvidencePack、CapabilityTrace 与 report。组件评测不得经过旧 Intent；Trigger/Consumption 和 E2E 才进入完整 `ChatApplication.handle()`。

## 11. 通过条件

- 每轮状态恢复不再隐式触发 ServiceEpisode RAG；
- 两套 purpose policy 独立冻结；
- heldout retrieval/temporal/supersession 指标通过；
- backend unavailable 与 no evidence 行为可区分；
- 跨用户/租户泄漏 observed `0/N`；
- Trigger 和 Consumption 合同通过；
- E2E task success 非劣后才进入生产 Bundle。

## 12. 相关文档

- [M0–M4 总迁移计划](./00-m0-m4-migration-master-plan.zh-CN.md)
- [Intent / TurnUnderstanding](./01-intent-understanding-architecture-and-evaluation.zh-CN.md)
- [E2E 与成绩汇总](./05-e2e-evaluation-and-scorecard.zh-CN.md)
