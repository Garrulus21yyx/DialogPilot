# 本地 RAG 首阶段召回可行性实测（Doc2Dial）

日期：2026-09-03

## 结论

使用公开 Doc2Dial 数据、固定的 512-token/64-token overlap 结构化切块、
`title + section_path + chunk_content` 检索文本和完全本地模型，方案具备明确可行性：

- 300 条公开 Dev 上，raw query 的 Dense+BM25 完整证据 Recall@20 为
  216/300（72.0%）；加入仅由对话确定性生成的 user-history query，并采用
  BGE-M3 Dense + learned sparse + BM25 固定权重 RRF 后达到 295/300（98.3%）。
- 同一 Dev 上，本地 `BAAI/bge-reranker-v2-m3` 将完整证据 Recall@5 从
  266/300（88.7%）提升到 283/300（94.3%）。
- 209 条长文档 Dev slice 上，三路融合 Recall@20 为 206/209（98.6%）；
  reranker 将 Recall@5 从 159/209（76.1%）提升到 185/209（88.5%），
  救回 29 条、误伤 3 条。
- 固定配置在一份全新、report-only 的官方 test heldout 上达到：完整证据
  Recall@20 56/60（93.3%，Wilson 95% CI 84.1%–97.4%），Recall@5
  51/60（85.0%，95% CI 73.9%–91.9%）。
- heldout 上 reranker 恰好救回 2 条、误伤 2 条，Recall@5 净增益为 0；
  因此不能把 Dev 上的 reranker 增益声称为已稳定泛化。

这是“公开数据上的工程可行性证据”，不是与论文排行榜同协议的 SOTA 声明。

## 数据与防泄漏协议

- 数据：Doc2Dial v1.0.1，公开许可 CC-BY-3.0。
- heldout：官方 test 中 60 个未使用 conversation，4 个领域 × 3 个文档长度层
  × 每层 5 条；语料为全部 488 篇官方文档，共生成 1,469 个 child chunks。
- 生成 heldout 时排除了此前使用过的 165 个 conversation group；排除集合、
  corpus、cases 和模型权重均有 SHA-256 指纹。
- heldout manifest 明确设置
  `configuration_selection_allowed=false`，结果只能报告，不能反向调参。
- 外部推理 API 调用数为 0；embedding、learned sparse、BM25 和 reranker
  均在本机 RTX 3080 上运行。

## 同协议消融

### 300 条 Dev

| 配置 | 完整证据 R@5 | 完整证据 R@20 | 完整证据 R@50 |
|---|---:|---:|---:|
| Raw query + Dense+BM25 | 61.0% | 72.0% | 81.3% |
| User history + Dense+BM25 | 84.7% | 97.0% | 100.0% |
| User history + Dense+Sparse+BM25 | 88.7% | 98.3% | 100.0% |
| 上述 Top-20 + local CrossEncoder | 94.3% | 98.3% | — |

这里最大的提升来自对话查询表达，而不是不断扩大候选：同一个 Top-20 候选池中，
reranker 只负责排序；Top-50 只用于测量候选上限，不会塞给生成模型。

### 60 条全新官方 test heldout

| 配置 | 完整证据 R@5 | 完整证据 R@20 | 完整证据 R@50 |
|---|---:|---:|---:|
| BM25 | 80.0% | 90.0% | 95.0% |
| Dense | 66.7% | 83.3% | 90.0% |
| Dense+BM25 | 83.3% | 91.7% | 96.7% |
| Dense+Sparse+BM25 | 85.0% | 93.3% | 95.0% |
| 三路 Top-20 + local CrossEncoder | 85.0% | 93.3% | — |

三路融合相对 Dense+BM25 在 R@20 上增加 1/60，在 R@5 上增加 1/60；
learned sparse 提供了小而真实的互补，不能单独解释全部提升。

## 4 条 heldout Candidate miss 的根因

切块投影包含全部 109 个 gold spans，containment rate 100%，boundary
fragmentation 0%。所以这 4 条不是 overlap 不足或 gold 被切断。

- 1 条属于 query 上下文缺失：当前 query 只拼接历史 user turns，最终问题是
  “No”，其含义依赖紧邻的 agent 问句；正确文档在 BM25/Dense/Sparse 中分别排
  178/459/909。这里应由确定性的 standalone/context projection 保留最近问答
  关系，而不是用无约束 HyDE 扩写。
- 3 条在三路融合中正确文档均排第 1，但正确 evidence child 分别排第 38、
  第 50 之外、第 50 之外。这里的 owner 是层级检索：先识别 parent/document，
  再在命中的 parent 内做 child search 或受限 window expansion。继续增大全局 K
  只能观察上限，不能作为最终修复。

## 下一轮的正向契约

1. Query projection：raw query 始终保留；对于 “yes/no/that/it/how do I apply”
   等上下文依赖表达，确定性携带最近 agent 问句和相关 user turns；无外部 API。
2. Candidate retrieval：Dense、BGE-M3 learned sparse、BM25 独立召回并固定 RRF；
   不使用 heldout 调权。
3. Hierarchical routing：由 top parent/document 命中触发同文档 child 局部检索，
   有明确 parent 数和 child pool 上限；不能把整库或 Top-100 交给生成模型。
4. Rerank：本地 CrossEncoder 只重排有界 child pool。由于新 heldout 净增益为 0，
   上线前需要新的 conversation-disjoint 验证，或用公开训练 split 做领域微调。
5. Packing：只接收最终 Top-5 和按需 parent/window，不改当前 token budget，除非
   新的 packing attribution 显示丢证据。

## 可复现产物

- 数据 manifest：
  `artifacts/eval/doc2dial-rag-en-heldout-fresh-local-v1/manifest.json`
- 固定 heldout 报告：
  `artifacts/eval/doc2dial-rag-en-heldout-fresh-local-v1/local-bge-m3-fixed-heldout-v2/report.json`
- 报告 SHA-256：
  `b5236ec314351a1daaf24a5fdc8812128bea5aefbd62b99ad9a2bcb085e3285c`
- 评估代码：`evaluation/local_bge_m3_retrieval_eval.py`
- 运行入口：`scripts/run_local_bge_m3_retrieval_ablation.py`
- 单元测试：`tests/test_local_bge_m3_retrieval_eval.py`

本机该 heldout 的语料与 query 编码耗时约 10.6 秒；1,200 个 query-child pair
的 rerank 耗时约 9.1 秒，即约 151 ms/query（不含一次性语料建索引成本）。
