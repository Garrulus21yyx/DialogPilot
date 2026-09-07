# G4：MTRAG 完整语料与检索数据适配

2026-09-07，起点 `9efc79c`。本轮完成来源对齐和现有 `RagDataset` 入口验证，**尚未运行检索、embedding、精排或答案生成**，推理 API 0 次。

| 领域 | 完整非空片段数 | 开发问题 | 封存分组问题 | 正相关 qrels / 精确 ID 匹配 |
|---|---:|---:|---:|---:|
| ClapNQ | 183,408 | 137 | 71 | 578 / 578 |
| Cloud | 72,439 | 128 | 60 | 494 / 494 |
| FiQA | 60,984 | 119 | 61 | 535 / 535 |
| Govt | 49,607 | 135 | 66 | 521 / 521 |
| 总计 | **366,438** | **519** | **258** | **2,128 / 2,128** |

沿用已锁定的110个conversation、75/35开发/heldout分组；没有按结果重新选题。这里封存指既有分组用途，不代表本轮证明全局未使用。完整语料含所有非空干扰片段，没有按gold缩小候选库。三个官方查询版本 `lastturn/questions/rewrite` 的问题ID集合一致；默认产物使用 lastturn，其他版本保存在独立文件，切换必须显式指定 `--mode`。

## 两个实际修复

1. 原始语料有41个空白片段（Cloud 3、FiQA 38），没有重复ID，也没有正相关qrel指向这些空片段。适配器记录所有排除ID；任意正相关片段缺失或重复ID直接失败，不能静默缩减分母。四域ID加领域前缀，避免跨域碰撞。
2. 现有 `evaluation/rag_pipeline/dataset.py` 的 `str.splitlines()` 会把JSON字符串内合法的Unicode分隔符拆成新记录，导致完整导入报错。修复在JSONL读取owner，按物理行读取，原文内容不变。测试覆盖U+0085/U+2028/U+2029在query、正文、引用中的往返保存。

首次失败与修复原因保存在 validation.json；不把失败轮删掉后声称第一次即成功。

## 标注口径

官方锁定版本推荐已提供的 [passage-level 语料](https://github.com/IBM/mt-rag-benchmark/tree/2c618bb98db3c8526433e22d8a2f7320f10a7470/corpora/passage_level)，本轮按下载文件实际计数。其ID与全部qrels直接对应；不再套用旧README中的“去掉末尾偏移再匹配”方式。官方语料说明的领域数量表与实际文件计数有差异，验收以记录checksum的文件为准。[锁定版本说明](https://github.com/IBM/mt-rag-benchmark/blob/2c618bb98db3c8526433e22d8a2f7320f10a7470/corpora/README.md)

这些是**片段相关性标注，非精确答案字符span**。适配到现有合同的 `document` granularity，每份document就是一条官方passage。不能把它当Doc2Dial的完整答案span召回；若后续再次切块，只能计算父passage是否命中，不能声称子块本身覆盖正确答案。

777个检索任务中709个ANSWERABLE、68个PARTIAL；保留原标签于query_types与official-queries，不用正相关qrels推导完整可回答。原generation reference中的55个UNANSWERABLE、10个CONVERSATIONAL不在这份官方retrieval任务集合内，不能凭本集测试弃答能力。官方rewrite不是当前Conversation Agent生成的query；二者收益不能混称。

## 复现

四个zip来自上述固定revision，下载至 `/tmp/dialogpilot-mtrag-corpora-20260907`。manifest记录每份URL、SHA256和文件大小；重建须核对相同checksum。锁定queries/reference/qrels来自现有缓存，适配器先检查全部原始SHA。

```bash
.venv/bin/python scripts/adapt_mtrag_retrieval_dataset.py \
  --cache /tmp/dialogpilot-rag-external-lock-20260907 \
  --corpora /tmp/dialogpilot-mtrag-corpora-20260907 \
  --lock artifacts/eval/rag-three-dataset-lock-v3-2026-09-07/mtrag-split.json \
  --output /tmp/mtrag-new-output --mode lastturn
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_rag_pipeline_evaluation.py tests/test_doc2dial_heldout_dataset.py \
  tests/test_mtrag_retrieval_adapter.py
```

相关测试33项通过；实际完整RagDataset加载校验通过。

实际生成物位于 `/tmp/dialogpilot-mtrag-adapted-v2-20260907`；完整语料不重复提交进Git。提交[manifest](../artifacts/eval/rag-g4-mtrag-adapter-2026-09-07/manifest.json)和[完整加载校验](../artifacts/eval/rag-g4-mtrag-adapter-2026-09-07/validation.json)，足以核对重建文件身份，但原始大文件需按URL恢复。

下一项：从开发分组按预先哈希规则少量抽题，保持对应领域完整语料，先建立无生成API的词法检索和lastturn/rewrite对照。每领域独立计算Recall/MRR/nDCG；不把四域合库成绩直接对比官方按域成绩。再按成本接本地Dense与生产工具边界；当前未证明这36.6万片段已在生产PG完成索引或查询。
