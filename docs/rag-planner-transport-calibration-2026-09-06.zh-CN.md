# 冻结规划输入的传输与编译校准

本轮沿用五条混合开发案例的真实规划 payload。目标是解释规划波动，避免不断重跑知识库与完整生成。没有执行业务工具、召回或答案发布，也没有更改生产规划提示词或默认模型。

## 已有失败归因

v4 的三个规划失败中，shipping/reference 在 2048 output tokens 处截断；delivered 完整返回 JSON，但订单目标含 `resolved_query: null`，违反现有非空字符串契约。不能把它们统一称为“不懂简单检索问题”，也不能通过放宽下游契约静默吞掉非法值。

## 冻结输入对照

新增 `scripts/run_conversation_plan_replay.py`，使用真实 provider 与 ConversationAgent 编译器。两个实验变体为原有文本 JSON 与显式工具字段 schema；工具 schema 仅属于评测，不进入生产。每组同一 Flash、2048 输出上限。v1 关闭 thinking；v2 使用 low thinking。模型输入保留捕获的消息、上下文、源引用和候选实体。只支持空活动任务状态与唯一实体绑定，并在隔离作用域重建编译上下文；不代表当前业务状态或权限的完整重放。

| 配置 | 变体 | 编译通过 | 同时保留订单＋知识工具 |
|---|---|---:|---:|
| Flash / none | 文本 JSON | 2/5 | 2/5 |
| Flash / none | 工具 schema | 3/5 | 1/5 |
| Flash / low | 文本 JSON | 4/5 | 4/5 |
| Flash / low | 强制工具 schema | 0/5 | 0/5（五次传输拒绝） |

“同时保留两工具”只检查编译出的两个工具，不代表查询语义、政策答案或最终行动正确。none 工具方案的 shipping/paid 丢了政策目标，不能按“编译成功”计为解决用户问题；missing 还出现了把询问规则选为改址目标的输出，编译未通过，没有执行任何改址。

调用按 text→structured 固定顺序、各一次，schema 也增加了输入约束；不是严格意义上只改变编码方式的统计实验。所有案例均为已消费开发案例。没有证据支持把实验规划 schema 或 none 配置推广到生产。

## 重放修正与传输兼容

独立审查发现最初重建只保留 recent messages，而真实上下文的 `recent_relevant_turns` 也包括 summary。五个捕获 payload 均有非空 summary，缺失会影响知识目标必须携带 resolved_query 的检查。已补齐，并离线重新编译二十份结果，计数未变；原始捕获保留，summary.json 是修正后编译检查。另有测试验证 summary 不能被丢弃，以及不能凭空重建活动任务。

低思考模式的五次失败均为 BadRequestError。额外一次最小诊断确认 400 原因：`Thinking mode does not support this tool_choice`。这是传输配置失败，不能算模型语义失败。

该限制也影响可配置的生产合成角色，因此在 Conversation provider 的合成边界修复：reasoning 开启用 `auto`，关闭仍指定输出工具；响应校验继续要求唯一完整的 submit_composed_response，不接受自由文本或其他工具。未更改默认 SYNTHESIS 配置。一例真实 Pro/low/2048 probe 返回正确工具输出、415 output tokens；只证明该配置可工作，不证明自动工具选择稳定，也未做该例答案语义或来源复验。

本轮共 22 次 API 尝试：两组各 10 次、一次失败原因诊断、一次合成兼容 probe。55 项相关测试通过，独立复核未发现上述修复的阻断。规划稳定性和更大规模全链路验收仍未完成。

## 复现

```bash
MODEL_INTENT_REASONING=none MODEL_INTENT_MIN_COMPLETION_TOKENS=0 PYTHONPATH=. \
.venv/bin/python scripts/run_conversation_plan_replay.py \
  --capture artifacts/eval/rag-mixed-business-2026-09-06-v4/mixed-cases.jsonl.gz \
  --output /path/to/new-none-output

MODEL_INTENT_REASONING=low MODEL_INTENT_MIN_COMPLETION_TOKENS=2048 PYTHONPATH=. \
.venv/bin/python scripts/run_conversation_plan_replay.py \
  --capture artifacts/eval/rag-mixed-business-2026-09-06-v4/mixed-cases.jsonl.gz \
  --output /path/to/new-low-output
```

证据位于 `artifacts/eval/rag-plan-replay-2026-09-06-{v1,v2}/` 与 `artifacts/eval/rag-compose-thinking-probe-2026-09-06/`，包括原始请求、输出、编译结果和修正后的统计。不得将传输校准结果混入 RAG 检索准确率。
