# DialogPilot 500 条分层评测集

这不是“500 条都测意图”的数据，也不是把同一批问题分别换模型跑。
它把项目的四个责任边界拆开测量：

| 层 | Dev | Heldout | 合计 | 确定性真值 |
|---|---:|---:|---:|---|
| Intent / OOS | 144 | 36 | 180 | 意图标签 |
| TaskPlan Routing | 96 | 24 | 120 | Owner 集合、task_id 集合 |
| RAG Retrieval | 80 | 20 | 100 | relevant document IDs |
| Memory + ReAct/Tool | 80 | 20 | 100 | 状态与副作用断言 |
| **合计** | **400** | **100** | **500** | |

## 数据状态

- 180 条意图样本来自 BANKING77 和 CLINC150 OOS 的许可子集，状态为
  `auto_mapped`。它们用于外部压力测试，不是人工审核的项目 gold。
- 320 条项目合同样本状态为 `provisional`。人工逐条复核后，才能通过
  `scripts/review_eval_dataset.py` 晋升为 `human_reviewed`。
- 同一语义 family 的变体共享 `group_id`，只能整体进入 dev 或 heldout。
- `manifest.json.expected_distribution` 由加载器强制校验；少一条、分错层或
  跨 split 都会失败。

## 生成与校验

```bash
.venv/bin/python scripts/build_project_eval_500.py
.venv/bin/python -m evaluation.dataset data/eval/dialogpilot-500-v1
.venv/bin/pytest tests/test_layered_eval_dataset.py -q
```

若 `data/eval/generated` 中没有外部候选池，构建脚本会从官方来源下载并生成。
最终选中的 180 条已经提交到本目录，因此普通评分不需要联网。

## 怎么跑

Intent 与 Routing 可以通过服务端数据集注册表直接运行。第一次只跑 dev，
确认模型、延迟和费用后再解封 heldout：

```bash
curl -sS http://localhost:8000/eval/datasets

curl -sS -X POST http://localhost:8000/eval/run \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id": "dialogpilot-500-v1",
    "split": "dev",
    "layers": ["intent", "routing"],
    "include_non_gold": true
  }'
```

`include_non_gold=true` 是因为当前数据尚未完成人工审核。报告必须注明
`auto_mapped/provisional`，不能写成“项目 gold 准确率”。

Retrieval 使用统一 prediction scorer；Stateful 已有独立真实 fixture 执行器。
预测结构如下：

```json
{"case_id":"retrieval-memory-recall-1","actual":{"retrieved_ids":["kb-memory-recall"]}}
{"case_id":"stateful-memory-short-close-1","actual":{"assertions":{"episodic_archived":true,"working_memory_cleared":true}}}
```

```bash
.venv/bin/python -m evaluation.benchmark \
  data/eval/dialogpilot-500-v1 \
  artifacts/eval/dev-predictions.jsonl \
  --split dev --include-non-gold --retrieval-k 5
```

Stateful 的真实运行命令：

```bash
.venv/bin/python -m evaluation.stateful_runner \
  data/eval/dialogpilot-500-v1 --split dev \
  --predictions artifacts/eval/stateful-dev-predictions.jsonl \
  --report artifacts/eval/stateful-dev-report.json
```

执行器根据 `input.scenario.action` 查找注册 fixture，调用真实的 MemoryManager、
HybridMemoryRetriever、ContextAssembler、MCPToolManager、ReAct、CoverageGate、
Verifier 和 TicketService。fixture 不读取 expected 值；未注册 action、fixture
异常或断言缺少实际探针都会直接失败。当前版本实跑 Dev 80/80、Heldout 20/20。

预测缺失不会缩小分母，而会直接失败。Retrieval 的自动运行器仍需把本目录
`corpus.jsonl` 装入隔离的评测 collection；因此不能声称 RAG 100 条已经实跑。

## 评测与模型选型的关系

500 条是“项目覆盖面”；Flash/off、Flash/reasoning、Pro/reasoning 是“模型配置”。
正确顺序是先用 dev 对候选配置做逐层比较，再锁定配置只跑一次 heldout。
不能把 500 条乘三个模型后，声称得到了三套不同项目数据。
