---
layout: page
title: 500 条分层评测：设计与运行
permalink: /evaluation-500/
---

# 500 条不是一个指标，而是四个可定位的质量边界

如果把 500 条全部做成意图分类，只能回答“入口标签是否识别正确”，无法回答
主 Agent 是否拆对任务、RAG 是否找到证据、短会话是否归档、伪造审批是否产生
副作用。DialogPilot 因此固定采用四层、总计 500 条的评测矩阵。

| 评测层 | Dev | Heldout | 总数 | 主要指标 |
|---|---:|---:|---:|---|
| Intent / OOS | 144 | 36 | 180 | Accuracy、Macro-F1、OOS Recall |
| TaskPlan Routing | 96 | 24 | 120 | Owner Exact、Task Exact、Jaccard、Fan-out |
| RAG Retrieval | 80 | 20 | 100 | Recall@K、MRR、nDCG |
| Memory + ReAct/Tool | 80 | 20 | 100 | Assertion Pass、All Assertions Pass |
| **总计** | **400** | **100** | **500** | 分层报告，不压成一个虚假的总准确率 |

## 数据是怎么来的

意图层从 BANKING77 选出 7 个项目重叠标签、每类 20 条，再从 CLINC150
选 40 条 OOS，共 180 条。它们保留原始标签、许可证和来源，只是自动映射，
所以状态是 `auto_mapped`。

路由层由 30 个语义 family、每组 4 个改写组成。覆盖单 Owner、技术+账务、
安全+账务、通用+技术、否定表达和人工接管。每条都明确期望 Owner 与
`task_id`，CI 会用当前确定性 Planner 逐条核对 120 条，标签漂移会直接失败。

检索层包含 25 篇隔离评测文档，每篇对应词面、语义改写、精确实体/错误码、
抗干扰四类 query。这样能分别暴露 BM25、向量召回、融合排序和否定干扰问题。

Stateful 层严格一半测记忆、一半测 ReAct/工具安全。每条不只是自然语言，
还包含结构化 `setup`、`action` 和布尔断言。当前真实 fixture 覆盖显式会话归档、
画像合并与 ID 区分、混合召回、宿主布尔审批、零副作用、trace、超时、Coverage
Gate 和 verifier fail-closed。它尚未证明真实空闲检测、签名 `approval_token`、
HTTP 公共响应投影或跨用户检索，因此不再用这些更强的名字包装现有结果。

## 为什么固定 400 / 100

最初设计是 400 条 dev 用来调 Prompt、阈值、召回策略和模型分层，100 条
heldout 在配置冻结后只运行一次。当前 Stateful heldout 已经参与两次缺陷定位，
所以它的 20 条只能作为 `consumed_regression_after_repair`，不能再证明泛化。
恢复 verified closure 前必须由未看过修复的人另写新鲜用例并独立复核。

## 真正运行

```bash
# 1. 验证数据合同，不调用模型
.venv/bin/python -m evaluation.dataset data/eval/dialogpilot-500-v1

# 2. 启动服务后，先运行 dev 的意图与路由
curl -sS -X POST http://localhost:8000/eval/run \
  -H 'Content-Type: application/json' \
  -d '{"dataset_id":"dialogpilot-500-v1","split":"dev",\
       "layers":["intent","routing"],"include_non_gold":true}'

# 3. Stateful fixture 调用真实组件并自动评分
.venv/bin/python -m evaluation.stateful_runner \
  data/eval/dialogpilot-500-v1 --split dev \
  --predictions artifacts/eval/stateful-dev-predictions.jsonl \
  --report artifacts/eval/stateful-dev-report.json

# 4. 在临时 embedded Chroma 中装载版本化 corpus，执行生产 KnowledgeBase
.venv/bin/python -m evaluation.retrieval_runner \
  data/eval/dialogpilot-500-v1 --split dev --top-k 5 \
  --predictions artifacts/eval/retrieval-dev.predictions.jsonl \
  --report artifacts/eval/retrieval-dev.report.json
```

当前 180 条外部样本是 `auto_mapped`，320 条项目样本是 `provisional`。
因此当前结果只能叫“候选集回归结果”；完成人工复核并留下 reviewer、时间和
notes 后，才可以叫 gold heldout 结果。

目前服务端可以直接运行 Intent 与 Routing。Stateful 的 100 条已经全部绑定
真实 fixture，当前机械回归为 Dev 80/80、已消费 Heldout 20/20。针对审查指出的
7 条假阳性，Owner 变异测试会在 `search_long_term`、`_fallback_summary`、
`finalize_conversation` 或 `ContextAssembler.assemble` 被破坏时强制失败。

Retrieval producer 现已接线：它把 25 篇 corpus 装入临时 embedded Chroma，调用
生产 `KnowledgeBase` 的向量 + BM25 + RRF 路径并输出证据 ID。当前 Dev 80 条的
真实基线是 Recall@5 0.9125、MRR 0.7504、nDCG@5 0.7914；这是 provisional
开发集结果，不是生产准确率，Retrieval heldout 尚未运行。

Reviewer B 随后用多 chunk 文档发现父 `document_id` 被过早当成候选 ID，可能
组合不同 chunk 的内容与 metadata。现已改为 360 Token 上限、48 Token overlap
的结构感知切分；唯一 `chunk_id` 贯穿向量、BM25、RRF 和投影，最终才按父文档
去重。显式两 chunk 反例与 300 组生成文档通过，原 Retrieval Dev 指标保持不变。

两次复核暴露的是同一个验收缺口。第一次发现 HTML 转义会扩大 section；第二次
发现 section 分隔符未计费，且二次预算返还重复计算容量。现在预算唯一事实是
最终拼接文本；历史确定后 section 上限严格等于剩余容量，强制当前轮次本身放不下
时返回 `ContextBudgetExceededError`。CI 固定种子 3000 组组合测试覆盖描述属性、
多 section、转义和历史；同种生成合同本地扩大到 20000 组，19405 组成功装配、
595 组得到预期有类型拒绝，预算违规为 0。

这套做法与 Anthropic 对 agent eval 中 task、trial、grader、transcript、outcome
和 harness 的区分一致：确定性 grader 要检查权威 outcome 与实际 trace，不能只
检查一个叫“通过”的字段。重复查看过的 heldout 也应降级为回归证据，而不是继续
声称未见泛化（[Anthropic agent eval 指南](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)、
[holdout 污染研究](https://arxiv.org/abs/2407.01502)）。

## 面试追问

**为什么不让 LLM Judge 判断所有层？**  
Owner、task_id、证据 ID 和副作用都有确定性真值。让另一个模型
判断会引入方差，也会掩盖安全错误。LLM Judge 只适合回答质量等主观维度。

**为什么 500 条不直接跑三个模型？**  
数据覆盖与模型选型是两个正交维度。先用小型消融确认候选配置，再在 400 条
dev 上分层比较；配置冻结后只跑一次新鲜 heldout，才能避免反复看考试答案。

**最重要的门禁是什么？**  
不是平均分，而是 OOS、用户过滤、未审批工具零副作用、必需任务覆盖等关键
切片必须单独过线。平均数不能抵消一次越权调用。
