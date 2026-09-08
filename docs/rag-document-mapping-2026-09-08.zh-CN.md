# 官方父文档映射与偏移核查

锁定IBM MTRAG revision 2c618bb98db3c8526433e22d8a2f7320f10a7470，下载四域document_level并逐一与已锁定passage_level比较，原始archive SHA保存在report。官方说明区分两种偏移，并建议基准使用passage版本：[官方说明](https://github.com/IBM/mt-rag-benchmark/blob/2c618bb98db3c8526433e22d8a2f7320f10a7470/corpora/README.md)。本轮继续用passage qrels，不切换评分单位。

父级候选ID必须出现在官方document_id/_id中；仅此前缀关系不算验证。正文要求严格相等，分开记录：原切片、官方标题+换行+切片、父正文连续ASCII空格压为单空格后再取偏移并加标题。最后一种转换是通过源数据复验得到的推断，不声称取得官方预处理实现；其偏移不属于未规范化原文，不能直接用于原文引用。

| 域 | 官方document记录 | 验证通过passage | 未解释正文差异 |
|---|---:|---:|---:|
| ClapNQ | 178890 | 183183 | 225 |
| Cloud | 8578 | 72439 | 0 |
| FiQA | 57638 | 60984 | 0 |
| Govt | 7661 | 48717 | 890 |

合计365323/366438个非空片段逐字验证。41个空片段沿用之前排除口径。1115个未解释片段不进入verified mapping，不用模糊匹配补过。ClapNQ的document记录本身大量是Wikipedia段落，不能把178890称整篇文章数，也不能再按数字前缀合并成文章而不核对原始资料。

首次只比较原始切片全部不匹配；加标题后仍有差异；连续空格规范化解释了主要差异。中间报告保留为转换诊断，不是来源丢失结论。一次脚本遇null标题退出，按空标题语义修正；一次反复规范化同父全文过慢，主动中断并改每父缓存后完成，不涉及模型调用或结果阈值变化。

使用已验证映射重算开发32题：两路遗漏的28个gold均有可验证父级，但只有4个父级进入任一路Top20，Top3不同父级仍为0。与有效URL诊断一致。**当前证据支持先改善父级定位，而不是直接加Top3父内搜索。** 本轮没有测独立父检索收益。

API0/新模型0。复现脚本`PYTHONPATH=. .venv/bin/python scripts/audit_mtrag_document_mapping.py`；源ZIP保存在/tmp，输出目录必须不存在。映射、SHA、逐域计数与开发机会位于`artifacts/eval/rag-g4-document-mapping-2026-09-08/`。整体RAG、1115个差异及原Doc2Dial回归仍开放。下一项先在Cloud可靠映射范围用同开发query比较父级定位，保持全局child分支和最终预算，不盲目扩全库候选。
