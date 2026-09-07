# ContextCompaction 真实模型开发评测

## 结论

2026-09-07，基于生产提交 `2b3a3c5`，8 个新建开发场景各执行两次。
结构性检查 16/16 通过；DeepSeek v4 pro 自动 judge 判为 16/16 通过。
主执行助手逐条复查后提出 2 项语义异议，因此不能将自动评分写成语义准确率 100%。
未修改生产 Prompt 或压缩实现；没有业务工具写入，没有运行 τ³ 完整业务任务。

## 方法与范围

- 压缩：生产 ContextCompaction / StrictSummarization，模型使用 WORKER 配置
  DeepSeek v4 flash、thinking none，最多输出 1024 tokens；与生产领域 Agent 使用同一模型角色。
- 触发线：默认 .70/.85；为控制开发评测开销，可用输入预算缩到 4200、overhead=100，
  不声称覆盖生产 32768-token 上限附近的长序列质量。
- 场景：否定授权、条件范围、金额方向、历史更正、目标更正、部分成功、写入结果未知、跨目标取消。
- 负载：原始语义消息后填入重复的只读工作备注，直到预算约 90%；
  两次分别保护 1 / 3 个工具返回，因此是两种批次配置，不是相同输入的严格重复稳定性实验。
- 独立于摘要的确定性断言：最新完整工具批次、固定目标原样保留；输入未修改；
  历史按引用可回读且与原始 messages 完全一致；输出不超预算；确实调用了摘要。
- 本次原文 Store 为 InMemoryStore，便于隔离语义测试；PostgreSQL 生命周期另跑组件集成验证。
- 自动 judge 通过现有 structured_call 调用 DeepSeek v4 pro，输出上限 2048 tokens；
  只看原始语义消息、预先冻结的要求及实际摘要，不用压缩后保留下来的原文替摘要补分。
- 使用现有 FrameworkCapture 记录实际调用、IO、用量与延迟，没有新建传输或事件总线。
  本轮本地 capture 不等于已上传并回读 Langfuse trace。

## 结果

| 指标 | 结果 |
|---|---:|
| 真实压缩调用 / 自动评判调用 | 16 / 16 |
| Provider/解析错误 | 0 |
| 结构性检查通过 | 16/16 |
| 自动 judge 全维度通过 | 16/16 |
| 主执行助手复查异议 | 2 项，未独立裁决 |
| 估算工作视图总 tokens（前 → 后） | 60606 → 7082，下降 88.3% |
| 单次压缩后视图估算 tokens | 361–567 |
| 压缩实际 input / output tokens | 44033 / 1783 |
| judge 实际 input / output tokens | 12119 / 1184 |
| 压缩中位延迟 / 样本 P95 | 1.670s / 2.589s |
| judge 中位延迟 / 样本 P95 | 1.600s / 2.334s |

P95 采用 nearest-rank，16 条时等于样本最大值，不代表生产尾延迟。
压缩输入中 cache_read=21504，judge 输入中 cache_read=7296；延迟包含缓存命中影响。
估算视图 tokens 与供应商计费 tokens 不同。未核对实时单价，未报告美元成本；
压缩比也不是整体业务 Token 节约率，需加上摘要调用成本并结合后续复用轮数。

## 自动评分遗漏与复查异议

1. `temporal_authority / 0`：原文只有“退款申请审核中、没有到账凭证”，摘要出现
   “Actual refund has not been received or confirmed”。“未到账”比“未确认到账”更强；
   同段虽保留审核中和未确认，仍存在把未知变成否定事实的歧义。judge 未指出。
2. `target_correction / 1`：用户已要求改查 OD-222，摘要增加
   “Pending decision: Whether to proceed with a fresh query for OD-222”。
   它可能把已经请求的查询变成新的待决事项；其他段落仍正确保留目标和待查询状态。
   尚未做下游恢复实验，不能断言一定导致重复追问，也不能直接忽略这个风险。

这两项不是金额丢失或工具记录丢失：它们指向同一语义风险——摘要添加了原文没有的
确定性或决策条件。当前样本能证明保留机制运行，不能证明语义边界完全正确。
不在本轮按编号修改生产 Prompt；后续应通过独立评判和下游续接实验裁决，
同时加入正反例检验 judge 是否能检出条件强化与未知状态误写。

## 可复现材料与后续

- 样本：[context_compaction_cases.json](../evaluation/context_compaction_cases.json)
- 执行器：[run_context_compaction_eval.py](../scripts/run_context_compaction_eval.py)
- 原始输入、摘要、judge 与回调：[cases.jsonl](../artifacts/eval/context-compaction-dev8-2026-09-07-v1/cases.jsonl)
- 配置与样本 hash：[manifest.json](../artifacts/eval/context-compaction-dev8-2026-09-07-v1/manifest.json)
- 自动统计：[report.json](../artifacts/eval/context-compaction-dev8-2026-09-07-v1/report.json)

运行命令（输出目录必须是新的，避免覆盖既有证据）：

```bash
.venv/bin/python -m scripts.run_context_compaction_eval --output artifacts/eval/context-compaction-dev8-new
```

另外运行压缩与原文生命周期测试：20 passed、0 skipped，使用独立 PostgreSQL 测试库。
此前一次未配置 TEST_DATABASE_URL 的运行是 14 passed、1 skipped，不作为数据库验收证据。

样本由主执行助手创建和复查，自动 judge 与被评模型同供应商；不是独立评审或 heldout。
重复备注远比真实异构历史容易压缩。未测多轮递归摘要、压缩后真实业务决策、生产规模长上下文，
也未覆盖最新超长工具输出外置的真实模型理解质量。这些限制不能用 16/16 自动评分抵消。
