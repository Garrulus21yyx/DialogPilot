# Cloud父级与局部片段检索：开发候选收益，尚未采用

固定既有8条Cloud开发query，8578个官方完整父文档，BM25 k1=1.2/b=.75与现有tokenizer。父ID由前轮官方正文映射验证；不使用gold选择父级或查询词。API0、embedding0。

| 父级方法 | Recall@3 | Recall@20 | MRR@20 |
|---|---:|---:|---:|
| Dense child Top20按首次父ID去重 | 50.00% | 70.83% | .6652 |
| BM25 child Top20按首次父ID去重 | 43.75% | 62.50% | .3950 |
| 完整父文档BM25 | 54.17% | 68.75% | .6042 |

父Top3相对Dense改善2题、降低1题，整体Top20和MRR没有改善。此处父相关性由passage gold映射而来，不是片段Recall。索引统计及评分约4.03秒，是离线8query批量耗时，不是生产请求延迟。

按预注册继续唯一局部策略：取父BM25 Top3，每父在其全部官方passage内用同query局部BM25取2条，最多6条；先保留这些条目，再用原Dense Top20补齐最终20。最终候选数相同，但新增父级/局部检索计算，不声称计算预算相同。

片段候选Recall@20 **55.21%→61.46%**，1题改善/0题下降。救回1be662…3的一条gold（父内第1）；不代表全部证据或答案已经正确。候选顺序尚未经CE，不报告此顺序的MRR为最终精排成绩。

两个主要见证仍失败：语言支持问题的父文档已到第1，但gold局部BM25第23；web chat父到第3，gold局部第14。固定每父2条没有救回它们。Enterprise原query目标错位，父gold仍74/85；不能靠这个局部策略解决。

结论：有候选收益，下一步仅对固定这两池做本地CE与打包验证，判断唯一救回是否保留、是否引入新的排序误伤。仍未采用、不改生产、不用8个已消费开发例证明泛化，微调暂停。

复现：`PYTHONPATH=. .venv/bin/python scripts/run_mtrag_cloud_parent_bm25.py`，随后`PYTHONPATH=. .venv/bin/python scripts/replay_mtrag_cloud_parent_children.py`。两个输出目录需不存在。产物分别位于`artifacts/eval/rag-g4-cloud-parent8-2026-09-08/`与`artifacts/eval/rag-g4-cloud-parent-child8-2026-09-08/`，包含每题父排名、局部池大小、gold局部排名、候选及输入身份。主体输入沿用已锁定本地ZIP，不重下载、不重算文档向量。
