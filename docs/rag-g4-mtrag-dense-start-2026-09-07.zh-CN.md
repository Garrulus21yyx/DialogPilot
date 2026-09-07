# G4：MTRAG Dense 编码预算与启动记录

2026-09-07，起点 `d6dcd54`。本轮为固定32题补Dense分路，查询沿用上一轮官方rewrite，四域完整非空语料366,438片段，API0，未微调。**全量编码仍在运行，尚无Dense Recall或融合提升结论。**

## 成本及截断探针

每域按source ID固定hash选64片段，共256，与qrels无关：

| 项目 | 实测 |
|---|---:|
| 512-token上限会截断 | 20/256 |
| 1024/8192上限会截断 | 0/256（仅样本） |
| Token p50 / p95 / max | 302 / 522 / 573 |
| batch4、8192上限、FP16编码 | 1.45秒 / 256片段 |
| 峰值allocated显存 | 约1.2GB |
| 全量朴素外推 | 约35分钟，非承诺 |

warmup另编码4片段，探针共编码260片段。外推不含全部I/O、长尾长度、缓存和评分，不能当线上性能。探针速度与真实全量可能不同。

全量使用8192上限；每分片先检查真实token长度，超限会失败而非悄悄截断。未因为样本最大573就假设整个语料均短于1024。FP16为推理精度，保存归一化float32向量；只编码原正文，保持与词法对照的检索表示一致。

## 全量执行与恢复合同

`scripts/run_mtrag_dense_shards.py` 每4096片段写一个向量文件，然后原子提交对应metadata。身份绑定模型文件SHA、语料manifest、冻结query结果、最大长度、batch、表示、分片大小及库版本。已有metadata的缓存必须通过输入hash和向量文件SHA；未提交metadata的中断文件可以在恢复时重写。目录进程锁阻止两个写入者同时使用缓存。

逐分片精确点积，稳定合并全域Top100；正式指标只用Top20，后80仅诊断。分片没有缩小搜索语料，也不是ANN。完整域计数与manifest一致后才输出结果。查询向量每次恢复会重算，文档完整缓存不重复编码。

当前执行：

- 统一exec会话：`2481`；实际进程PID：`1508735`。
- 缓存与进度：`/tmp/dialogpilot-mtrag-dense-full-20260907/`。
- 日志：`/tmp/dialogpilot-mtrag-dense-full-20260907.log`。
- 快照时已编码12,288片段，仍在第一域ClapNQ；实时状态以进程/会话输出为准，不能凭旧progress的RUNNING判定进程仍活着。

下轮先轮询同一个会话或核查该PID；只有明确进程终止才考虑按相同命令恢复，不能另开重复任务。向量大文件留本地，Git保存身份和阶段证据。

```bash
PYTHONPATH=. HF_HUB_OFFLINE=1 .venv/bin/python -u scripts/run_mtrag_dense_shards.py \
  --corpora /tmp/dialogpilot-mtrag-corpora-20260907 \
  --model /home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181 \
  --manifest artifacts/eval/rag-g4-mtrag-adapter-2026-09-07/manifest.json \
  --lexical artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07 \
  --output /tmp/dialogpilot-mtrag-dense-full-20260907
```

检查：2项性质测试通过（分片TopK与全局排序在同分/负分条件下一致；hash取样不依赖输入顺序）。首个4096×1024缓存SHA、有限值、单位范数检查通过。此证据不证明所有分片已完成或所有异常恢复已覆盖。

产物：[探针](../artifacts/eval/rag-g4-mtrag-dense-start-2026-09-07/probe.json)、[缓存身份](../artifacts/eval/rag-g4-mtrag-dense-start-2026-09-07/identity.json)、[首分片检查](../artifacts/eval/rag-g4-mtrag-dense-start-2026-09-07/first-shard-audit.json)。

下一步等完整编码完成，审计四域计数/实际token上限/排名，再以同32题比较Dense与BM25、统计漏召回互补性；随后才重放融合。不改变当前生产权重、精排或Agent架构。
