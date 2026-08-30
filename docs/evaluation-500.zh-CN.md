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
还包含结构化 `setup`、`action` 和布尔断言，可执行短会话归档、画像单调合并、
跨用户隔离、混合召回、审批令牌、零副作用、trace、超时和发布脱敏等协议。

## 为什么固定 400 / 100

400 条 dev 用来调 Prompt、阈值、召回策略和模型分层；100 条 heldout 在配置
冻结后只运行一次。每个语义 family 只能整体位于一侧，避免“同一句话换个说法”
同时出现在调试集和考试集，造成指标虚高。

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

# 4. RAG producer 产出 predictions.jsonl 后确定性评分
.venv/bin/python -m evaluation.benchmark \
  data/eval/dialogpilot-500-v1 artifacts/eval/dev-predictions.jsonl \
  --split dev --include-non-gold --layer retrieval --retrieval-k 5
```

当前 180 条外部样本是 `auto_mapped`，320 条项目样本是 `provisional`。
因此当前结果只能叫“候选集回归结果”；完成人工复核并留下 reviewer、时间和
notes 后，才可以叫 gold heldout 结果。

目前服务端可以直接运行 Intent 与 Routing。Stateful 的 100 条已经全部绑定
真实 fixture：Dev 80/80、Heldout 20/20；执行器拒绝未注册 action 和无探针
断言，不能把 expected 复制成 actual。Retrieval 仍缺隔离 collection loader，
所以当前不能虚报“500 条已经全部实跑”。

第一次 Stateful heldout 运行还发现了真实缺陷：不可信记忆中的 `<system>` 经
HTML 转义后字符膨胀，旧预算算法会把整个高优先级 section 丢弃。修复后改为
按最终渲染文本二分裁剪，再次运行 heldout 达到 20/20。

## 面试追问

**为什么不让 LLM Judge 判断所有层？**  
Owner、task_id、证据 ID、跨用户隔离和副作用都有确定性真值。让另一个模型
判断会引入方差，也会掩盖安全错误。LLM Judge 只适合回答质量等主观维度。

**为什么 500 条不直接跑三个模型？**  
数据覆盖与模型选型是两个正交维度。先用小型消融确认候选配置，再在 400 条
dev 上分层比较；配置冻结后只跑一次 100 条 heldout，才能避免反复看考试答案。

**最重要的门禁是什么？**  
不是平均分，而是 OOS、跨用户隔离、未审批工具零副作用、必需任务覆盖等关键
切片必须单独过线。平均数不能抵消一次越权调用。
