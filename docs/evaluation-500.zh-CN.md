---
layout: page
title: 500 条分层评测：设计与运行
permalink: /evaluation-500/
---

# 500 条分层评测：覆盖四个责任边界，不制造一个总分

> `dialogpilot-500-v1` 是当前实现保留的 provisional 合同数据集。它用于定位 Intent、TaskPlan、Retrieval 与 Stateful 四层问题；其中部分 heldout 已参与修复，不能被重新命名为 fresh Gold。当前 PostgreSQL RAG 的选型证据另见 [Doc2Dial 全链路评测]({{ '/rag-pipeline-evaluation/' | relative_url }})。

## 1. 数据矩阵

| 评测层 | Dev | Heldout | 总数 | 权威真值 |
|---|---:|---:|---:|---|
| Intent / OOS | 144 | 36 | 180 | 意图标签与 OOS |
| TaskPlan Routing | 96 | 24 | 120 | route、Owner、task id |
| RAG Retrieval | 80 | 20 | 100 | relevant document ids |
| Memory + ReAct/Tool | 80 | 20 | 100 | 状态与副作用断言 |
| **总计** | **400** | **100** | **500** | 分层报告 |

如果把 500 条都做成意图分类，只能回答入口标签问题，无法发现错误 Owner、缺失任务、越权工具、无证据发布或记忆污染。这里没有跨层“总准确率”；每层按自己的 Owner 和失败代数评分。

## 2. 数据来源与审阅状态

- Intent 的 180 条来自 BANKING77 与 CLINC150 OOS 许可子集，状态为 `auto_mapped`。
- 其余 320 条是项目合同场景，状态为 `provisional`。
- 相同语义 family 共享 `group_id`，必须整体进入同一个 split，避免改写泄漏。
- manifest 固定 case/corpus checksum、分布、来源、版本与 review policy。
- Stateful 20 条 heldout 已用于缺陷定位，状态是 `consumed_regression_after_repair`。
- 恢复 verified closure 仍需要未见的 fresh cases 与独立 reviewer。

因此本页的数字只能叫开发基线或机械回归，不能写成“500 条人工 Gold 的生产准确率”。

## 3. 当前分支如何生成与校验

```bash
PYTHONPATH=. .venv/bin/python scripts/build_project_eval_500.py
PYTHONPATH=. .venv/bin/python -m evaluation.dataset \
  data/eval/dialogpilot-500-v1
```

普通评分不需要下载第三方大语料；选中的样本、corpus 和 checksum 已提交。重新构建外部候选池时才需要联网，并必须保留数据许可证与来源。

## 4. Intent 与 Routing

服务启动后，通过同一个数据集注册表运行 Dev：

```bash
curl -sS -X POST http://localhost:18000/eval/run \
  -H "Authorization: Bearer $DIALOGPILOT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id":"dialogpilot-500-v1",
    "split":"dev",
    "layers":["intent","routing"],
    "include_non_gold":true
  }'
```

`include_non_gold=true` 是对当前 review 状态的显式承认，不是放宽通过标准。

Routing 的确定性真值是 route mode、Owner 和 task id。它不执行 Worker、工具、Coverage 或 Verifier，因此 `routing passed` 不能推导为完整服务链通过。

## 5. Stateful fixture

```bash
TEST_DATABASE_URL=postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot \
PYTHONPATH=. .venv/bin/python -m evaluation.stateful_runner \
  data/eval/dialogpilot-500-v1 --split dev \
  --predictions artifacts/eval/stateful-dev-predictions.jsonl \
  --report artifacts/eval/stateful-dev-report.json
```

执行器只把冻结的 `FixtureRequest(case_id, scenario, message)` 交给 actual producer，类型上不暴露 `expected`。fixture 调用真实 Memory、Context、ToolManager、ReAct、Coverage、Verifier 和 PostgreSQL Ticket Owner；未注册 action、异常、缺少探针或非布尔观测都会显式失败。

这关闭了“fixture 读取 expected 后制造通过”的 harness 缺口，但仍不把固定 fixture 等同于真实服务链或未见泛化。

## 6. Retrieval 层的当前定位

`dialogpilot-500-v1` 的 Retrieval 是 25 篇隔离 corpus、100 条 query 的早期开发基线。它记录过 vector/BM25/RRF 的历史比较，但当前分支已删除 Chroma `KnowledgeBase`、`evaluation.retrieval_runner` 和旧 retrieval ablation 运行时，在线唯一 Owner 已迁移到 PostgreSQL SourceRevision + pgvector + 中文 FTS。

因此：

- 不再发布已删除模块的“当前运行命令”；
- 旧 Retrieval case 仍可用于数据合同、离线 scorer 和历史对照；
- 当前在线 RAG 参数与发布资格必须由 Doc2Dial 分层实验、PostgreSQL Owner 测试和真实 `/chat` E2E 共同证明；
- 需要重新激活这 100 条时，应为 `HybridRetrievalBackend` 实现新的 producer，而不是复活 Chroma。

## 7. 从 500 条到 Service-chain v2

四层 fixture 暴露了覆盖缺口，但还不能表达完整客服生命周期。当前分支新增 Service-chain v2，将一次请求拆成 11 个可审计层：

```text
perception → route_mode → context_memory → retrieval
→ tool_authority → tool_effect → generation_claims
→ publication → handoff → delivery_feedback → service_outcome
```

每条 case 固定 required/forbidden layer、Owner、工具参数子集、receipt 字段、task owner/dependency、parallel wave、claim-evidence、状态迁移、publication/effect 上限和零容忍安全标志。

`ChatApplicationRunner` 调用与服务相同的 `ChatApplication`，并采集 typed stages 与 Owner state。grader 在执行后比较 rubric；可选 semantic scorer 不能覆盖确定性失败。

当前合同测试入口：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_service_chain_eval_v2.py \
  tests/test_chat_application_runner.py \
  tests/test_behavior_baseline.py
```

## 8. 选择、回归与 fresh evidence

正确流程：

1. 在 Dev 上定位层与 slice；
2. 建立根因、Owner 和正向合同；
3. 用状态机/属性/集成测试修复整个因果面；
4. 固定候选、数据、代码和环境 fingerprint；
5. 在未参与开发的 fresh cases 上反证；
6. 由独立 reviewer 核对报告、实现和文档；
7. 已读 heldout 自动降级为 regression，不重复声称泛化。

一个回归示例只证明该示例。身份隔离、未审批副作用、唯一 publication、证据覆盖、ACK 单调等应使用 invariant 或状态机测试。

## 9. 已记录的历史结果如何解读

历史收敛运行记录包括：Intent 170/180、Fast Routing 120/120、Stateful Dev 80/80 和 consumed regression 20/20。它们属于固定版本和数据状态的证据快照，不是当前提交自动继承的承诺。

Intent 的剩余分歧包含标签边界问题，例如“陌生扣款”可落在 account security 或 payment issue。正确处理是人工仲裁标签与支持范围，而不是用 case id 或更多关键词制造 100%。

每次重新引用数字时必须同时写出：commit、dataset checksum、split/review 状态、配置 fingerprint、环境与生成报告。若缺少这些字段，只能称为历史描述。

## 10. 零容忍门禁

以下失败不能被平均分抵消：

- JWT subject / tenant 越权；
- OOS 被当作业务执行；
- 模型伪造审批字段触发写工具；
- 同一 operation 产生重复副作用；
- 必需任务或 requirement 缺失却正常发布；
- 无合法 authority 的 claim 获得 citation；
- 同一 Invocation 出现多个权威 publication；
- `REJECT / UNKNOWN` 被改写为普通成功；
- projection 或 cache 反向覆盖 PostgreSQL 事实。

## 11. 面试追问

### Q1：为什么 500 条不直接乘三个模型？

数据覆盖和模型配置是两个维度。先在 Dev 比较候选并定位分层差异，再冻结配置只使用真正 fresh 的 heldout，才能避免反复看答案。

### Q2：为什么不全部交给 LLM Judge？

Owner、task id、receipt、publication count 和状态迁移都有确定性真值。LLM Judge 适合人工校准后的语义质量，不应决定权限或副作用是否安全。

### Q3：为什么删除 Retrieval 的旧运行命令？

当前分支已删除其 Chroma producer。保留不可执行命令会把历史实现冒充当前链路；新的 producer 应接 PostgreSQL `HybridRetrievalBackend`。

### Q4：500 条与 Service-chain v2 是替代关系吗？

不是。500 条提供四层覆盖与历史回归，v2 扩展到真实应用边界和服务生命周期。两者的 case identity、rubric 和可证明结论不同。

### Q5：最重要的评测纪律是什么？

先固定事实 Owner 和 supported algebra，再让 runner 观察真实 outcome；报告必须保留失败，不能让 harness、projection 或 semantic scorer替系统完成任务。

---

{% include_relative _includes/rag-evaluation-deep-dive.md %}
