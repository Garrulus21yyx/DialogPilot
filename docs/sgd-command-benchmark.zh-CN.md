---
layout: default
title: SGD 到 DialogPilot Command IR 的公开数据适配
permalink: /sgd-command-benchmark.html
---

# SGD 到 DialogPilot Command IR 的公开数据适配

> 状态：v2 数据、评分器与生产同源 runner 已冻结；完整 Dev 已真实运行，Test 尚未消费
>
> 数据集：`dstc8-schema-guided-dialogue-command-v1-stratified`
>
> 上游：Google [Schema-Guided Dialogue](https://github.com/google-research-datasets/dstc8-schema-guided-dialogue)，commit `e852981ae34990f4358979625854259302feaa78`

## 1. 这套数据证明什么

该数据把 SGD 的公开对话、API schema、required slots、dialogue state 和
`service_call` 转换为 DialogPilot 的 conversation-level Command IR。它用于回答：

- 请求缺少已支持 Flow 的必填参数时，能否输出 `CLARIFY`；
- 第一次执行 API 时，能否选择正确的 `START_FLOW`；
- 同一 Flow 已执行后再次调用时，能否绑定实例并输出 `CONTINUE_FLOW`；
- 请求不属于冻结 Registry 时，能否输出 `NO_SUPPORTED_FLOW`；
- Command、Flow、参数与预期状态转换能否同时精确匹配。

它不代表真实生产流量，也不是官方 SGD leaderboard 分数。

## 2. 标签所有权

```text
官方 train/schema.json
  → 冻结 benchmark Registry（支持边界、必填参数、事务属性）

官方 user state + 紧随其后的 system frame
  → 确定性转换规则
  → Command Gold 或 typed exclusion
```

转换器不根据自然语言猜标签：

| 官方证据 | 适配标签 |
|---|---|
| 当前 intent 的首次 `service_call` | `RESOLVED / START_FLOW` |
| 同一 service/intent 已调用后的 `service_call` | `RESOLVED / CONTINUE_FLOW` |
| 缺少 required slot，且下一系统 action 明确 `REQUEST` 该槽 | `CLARIFY` |
| service/intent 不在 train Registry | `NO_SUPPORTED_FLOW` |

多 Frame、没有 active intent、调用参数不完整或无法由结构化标注明确决定的轮次，
进入 `exclusions.jsonl`，不能靠人工方便逻辑补标签。

## 3. 冻结数据

仓库内的 `data/eval/sgd-command-balanced-v2` 包含：

| Split | CLARIFY | START_FLOW | CONTINUE_FLOW | NO_SUPPORTED_FLOW | 合计 |
|---|---:|---:|---:|---:|---:|
| Dev | 300 | 300 | 300 | 300 | 1,200 |
| Test | 300 | 300 | 300 | 300 | 1,200 |

官方完整转换池的标签数量为：

| Split | CLARIFY | START_FLOW | CONTINUE_FLOW | NO_SUPPORTED_FLOW |
|---|---:|---:|---:|---:|
| Dev | 2,355 | 3,303 | 486 | 9,271 |
| Test | 1,407 | 2,367 | 306 | 27,540 |

因为官方 shard 与未见服务会造成 OOS 占比过高，冻结集使用固定 seed 对四类映射
分别做 stable-hash 抽样。Dev/Test 仍来自各自官方 split，没有跨 split 移动。

每个 case 都包含：

```json
{
  "input": {
    "message": "...",
    "history": {"dialogue_id": "...", "exclusive_end_turn": 4},
    "current_state": {"active_flows": []}
  },
  "expected": {
    "status": "RESOLVED",
    "commands": [{"kind": "START_FLOW", "flow": {}, "arguments": {}}],
    "missing_required_slots": [],
    "next_state": {}
  },
  "provenance": {
    "source_ref": "sgd://...",
    "mapping_rule": "SGD_SERVICE_CALL_FIRST",
    "source_evidence": {}
  }
}
```

对话文本单独存入同 split 的 `conversations.jsonl`，case 通过 history range 引用，
避免在每个轮次重复复制整段历史。

## 4. 可复现命令

从已下载的官方仓库生成完整池：

```bash
PYTHONPATH=. python scripts/adapt_sgd_command_dataset.py \
  --source /path/to/dstc8-schema-guided-dialogue \
  --source-commit e852981ae34990f4358979625854259302feaa78 \
  --output /tmp/dialogpilot-sgd-full-v2
```

也可用 `--download` 自动拉取固定 revision。然后冻结分层集：

```bash
PYTHONPATH=. python scripts/select_sgd_command_benchmark.py \
  --pool /tmp/dialogpilot-sgd-full-v2 \
  --output data/eval/sgd-command-balanced-v2 \
  --per-rule 300
```

校验数据：

```bash
PYTHONPATH=. python scripts/validate_sgd_command_benchmark.py \
  --dataset data/eval/sgd-command-balanced-v2
```

预测文件每行应为：

```json
{"case_id":"...","status":"RESOLVED","commands":[],"next_state":{}}
```

评分：

```bash
PYTHONPATH=. python scripts/score_sgd_command_predictions.py \
  --cases data/eval/sgd-command-balanced-v2/test/cases.jsonl \
  --predictions /path/to/predictions.jsonl \
  --output /path/to/report.json
```

评分器分别报告 Status、Command kind、Flow、Arguments、Next State、整体 Exact
Match、四类映射的 Exact Match 和 Macro Mapping-rule Exact。Command/Flow/Arguments
的分母只包含 `RESOLVED` case，终止 case 不会虚高这些指标。

## 5. 运行生产同源 Structured LLM

当前 Structured provider Command 协议已经显式携带 `arguments`。Registry 拥有
required/optional 参数集合，RoutePolicy 拒绝缺失或未知参数；生产 `order_id` binder
还会将模型参数和用户明确写出的 ID 交叉校验，避免模型生成标识符被直接信任。

运行 Dev 小样本：

```bash
ANTHROPIC_API_KEY=... \
PYTHONPATH=. python scripts/run_sgd_structured_command_eval.py \
  --dataset data/eval/sgd-command-balanced-v2 \
  --split dev \
  --limit 40 \
  --concurrency 4 \
  --output-dir artifacts/eval/sgd-command-dev-smoke
```

Runner 会执行：

```text
case + referenced history + current state
→ StructuredLLMCommandProducer
→ CommandArgument canonicalization
→ RoutePolicy
→ predicted state transition
→ deterministic scorer
```

它不会读取 case 的 `expected` 字段来生成预测。中断后只有显式传入 `--resume` 且
dataset/model/limit 配置完全一致才允许续跑。Test split 还必须显式传入
`--allow-heldout`，防止开发期反复消费冻结集。

v2 保留了官方 slot description、categorical possible values，并把官方说明中的
`today = 2019-03-01` 和日期/时间格式写入参数 schema。它也把 `CONTINUE_FLOW` 明确定义
为对 source-flow bindings 的增量：RoutePolicy 用“旧 bindings + 本轮 arguments”校验
必填参数，本轮值覆盖旧值，评测投影后再与完整 service-call 参数比较。

## 6. 完整 Dev 实测（2026-09-03）

模型为 `deepseek-v4-flash`，通过生产同源 Structured producer 和 RoutePolicy 运行全部
1,200 条 Dev；无缺失预测，无 provider transport error。严格结果如下：

| 指标 | 正确/总数 | 比例 |
|---|---:|---:|
| Status | 810/1,200 | 67.50% |
| Command kind | 347/600 | 57.83% |
| Flow | 340/600 | 56.67% |
| Arguments | 254/600 | 42.33% |
| Next state | 815/1,200 | 67.92% |
| E2E Exact | 690/1,200 | 57.50% |

分层 E2E Exact：`CLARIFY 279/300 (93%)`、`START_FLOW 57/300 (19%)`、
`CONTINUE_FLOW 189/300 (63%)`、`NO_SUPPORTED_FLOW 165/300 (55%)`。
运行身份、token 用量、prediction checksum 与逐条失败见
`artifacts/eval/sgd-command-dev-v2/`。这些是公开适配 Dev 成绩，不是生产流量准确率；
Test 仍保持未运行，待模型和 prompt 冻结后再做一次最终验证。

## 7. 许可

SGD 原始数据使用 CC BY-SA 4.0。仓库中的适配数据保留上游归属与相同许可说明；
DialogPilot 自有转换代码的许可不改变上游数据的许可。详细 commit、checksum、选择
seed 和规则见数据目录的 `manifest.json`。
