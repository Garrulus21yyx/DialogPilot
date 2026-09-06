# RAG D：本地开发评测与有界并行（进行中）

本轮外部推理调用 0。A/B/C 架构版本锚点为 `5b60455`。下面基线是该版本切块、融合、打包和知识工具序列化组件的**本地无精排回放**，不是完整线上 A/B/C 系统：本地 BGE-M3、Python BM25、精确向量排序替代线上 embedding/数据库检索；没有运行 Conversation Agent、生产 listwise reranker 或最终生成。不能据此声称 A/B/C 或答案准确率提升。

## 已执行的数据与预算

- 首轮校准 20 条，与后续开发集重叠，不合并计数。
- 扩展开发集实际 56 条：36 个公共 Doc2Dial conversation + 20 条中文电商模拟问题，共 55 个 group。公共输入有 209 个 turn，但只有 36 个独立 group，选择每组历史最长的 turn。
- 119 篇文档，512/64 结构切块得到 378 个 child，77 个 gold spans。原文完整包含率 100%，边界碎裂 0%。384/48 对照为 482 个 child，同样完整包含率 100%。这些不能证明 PDF/OCR 等格式解析质量。
- 每路候选 20，融合候选 20，最终最多 5 个片段；2,600 tokens 是证据正文预算。完整 ToolMessage 另计 tokens，基线记录中最大 3,305，不能声称整个消息限制在 2,600。
- 本地 embedding 与 reranker 均校验模型文件 SHA-256；检索缓存按模型配置和完整文本标识。离线环境配置加 socket connect 阻断，禁止推理网络连接。
- Query `history` 是确定性保留最近四条历史消息，并非 Agent 生成的 resolved query。`raw` 为当前消息对照。
- 电商样本为虚构商店政策与手写问题，比较容易；成绩与公共集分开报告。地区、版本、撤回和业务工具路由尚不是这份本地回放的评测能力。

## 同一批 56 条的初步结果

完整证据表示该问题的所有 gold spans 均可见；不是相关性 Hit，也不是答案正确率。

| 本地方案 | 候选完整证据 /56 | ToolMessage 完整证据 /56 | 相对无精排 0.25 基线救回/误伤 |
|---|---:|---:|---:|
| history，512/64，Dense 权重 0.25，无精排 | 50 | 42 | — |
| 相同候选 + 本地完整输入 CrossEncoder | 50 | 44 | 2 / 0 |
| history，512/64，Dense 权重 0.5 + CrossEncoder | 54 | 45 | 3 / 0 |
| raw，512/64，Dense 权重 0.25 + CrossEncoder | 46 | 44 | 10 / 8 |
| history，384/48，Dense 权重 0.25 + CrossEncoder | 49 | 45 | 4 / 1 |
| history，512/64，权重 0.25，有界 parent 内重检索 + CrossEncoder | 52 | 44 | 2 / 0 |

因此较小 chunk 或更多 history 并非单调提升；要同时看救回、误伤和候选上限。512/64 同权重本地精排净增 2/56，即 3.57 个百分点；这是开发诊断结果。当前捕获中 selected→packed→ToolMessage 没有再丢完整证据，剩余失败主要在候选与排序。

固定五组权重 0/0.25/0.5/0.75/1 使用同一份分路排名重放。五折按 group 隔离，固定权重、按语料类型选权重、按查询类别选权重均在训练折选参，测试折不用标签选权重；小于 10 条的类别退回较粗配置。三种方案均 44/56，动态方案未显示收益，生产权重保持不变。这只是轻量分桶策略，不是训练了通用神经路由模型。语料类别来自 benchmark 元数据，不能直接视为可部署的知识库路由器。交叉验证只检验已选定 chunk/query 配置下的权重选择，没有对整个管线调参进行嵌套验证。

## Parent-child 对照

根据分路 child 的首次出现位置聚合 parent 排名，最多选 3 个 parent，在这些文档的所有 child 中重新计算局部两路排名；不是新增 parent embedding。保留全局前 10 个候选，再由局部结果补满总计 20 个，使用相同 CrossEncoder 和 5 个输出片段。

当前权重下，候选完整证据由 50/56 提到 52/56，但精排后的 ToolMessage 仍为 44/56。救回的候选尚未转化为最终可见证据收益，因此只保留评测策略，没有迁移为生产默认。内部多一次文档定位与局部排序；相同最终候选预算不代表相同计算成本。复现增加 `--candidate-policy parent_child`。

## 并行实现与限度

`RAG_RETRIEVAL_PARALLEL=true` 可选择 BM25 与 embedding→dense 并发；默认 false。独立连接执行两路检索，保留相同 generation、scope、授权和适用条件，合并前检查 watermark；任一路故障不返回可当作完整成功的部分候选。来源投影和复验沿用现有 owner。

每个 source 实例最多两个同时执行的检索批次。超时的调用直到工作真正结束才释放容量。`RAG_RETRIEVAL_DEADLINE_SECONDS` 默认 3 秒，约束候选分支阶段（embedding、各变体/过滤分支查询）；不包含前面的 registry 读取或后面的原文投影，不是整条回答链 SLA。运行中的 Python embedding 不能强杀；数据库查询受 statement_timeout 约束。应用关闭时先等待检索执行器，再关闭连接池。

真实隔离 PostgreSQL 的单来源、常量 embedding 微基准，预热后每模式 20 次，交替执行并核对结果相同：serial p50 1.87ms / p95 3.11ms；parallel p50 2.52ms / p95 3.26ms。此规模下未加速，不能外推生产吞吐或 p95。事件/屏障测试另外证明两路实际可以同时执行，超时不会提前释放容量。

## 复现

已冻结公共输入至 `data/eval/rag-d-public-dev-v1`，保留原始 manifest 与 Doc2Dial CC-BY-3.0 来源信息；模拟输入在 `evaluation/rag_ecommerce_dev.py`。

```bash
.venv/bin/python scripts/run_rag_provider_free.py \
  --dataset data/eval/rag-d-public-dev-v1 \
  --model /path/to/local/bge-m3 \
  --reranker /path/to/local/bge-reranker-v2-m3 \
  --output /path/to/new-output \
  --cache /path/to/local-cache \
  --public-cases 50 --synthetic-cases 20

.venv/bin/python scripts/run_rag_fusion_selection.py \
  --capture /path/to/new-output/reranked-cases.jsonl \
  --output /path/to/new-cv.json
```

切块对照加 `--chunk-tokens 384 --overlap 48`；查询对照加 `--query-mode raw`。脚本拒绝覆盖输出，报告记录实际样本数。运行需本地模型、FlagEmbedding、PyTorch 和 CUDA。输出 scores、逐例 trace、原文位置、模型输入及 checksum；归档 JSONL 使用 gzip，解压后与原 manifest 的 SHA-256 对照。

## 验证与尚未闭合

本轮相关纯本地测试 38 项通过；真实 PG 候选/并行、后端和 lifespan 分批通过，重叠项不累计成总数。独立 fresh-context 审查复核了 280 行无精排与 280 行精排统计，发现的过期 deadline、单路绕开执行器、reranker query 截断预算问题已修复。后续新增策略仍需独立审查。

有界 child 重检索的开发对照已完成，并由独立审查复核 560 条捕获记录的预算、去重、全局前缀保留与分层指标。逐例失败和救回/误伤见 `artifacts/eval/rag-d-provider-free-2026-09-06/failure-attribution.json`，可用 `scripts/summarize_rag_provider_free.py` 重建。下一步需要扩大代表性样本，验证候选收益怎样传到最终证据，而不是直接启用更多策略。独立封存验收尚未执行；当前数据均已用于开发，不能改称 fresh heldout。生产 Agent 的 query、最终生成、引用语义支持及业务路由没有用 mock 或引用 ID 合法率代替。全链路目标仍进行中，最终答案收益尚未测量。
