# G4：三套外部数据的本地暴露审计

2026-09-07，起点 `405f0ed`。本轮调用 API 0 次，未运行模型、调参或评估答案。审计目的：建立保守排除范围，防止把已有实验再次叫作新鲜验收。

| 数据 | 已锁定范围 | 本轮发现 | 下一步用途 |
|---|---|---|---|
| Doc2Dial | 官方 test 661 个对话 | 当前文件与旧排除清单合计覆盖 516；未观察到 145 | 516 仅用于回归；其余需确认来源和可答性后才能封存 |
| MTRAG | 110 个对话；自定义 dev 75 / heldout 35，检索问题分别 519 / 258 | 当前扫描没有 task/conversation ID 或三种官方查询的匹配；原始文件校验一致 | 保留已有分组，先补检索语料、qrels 与评测入口；尚无成绩 |
| WixQA ExpertWritten | 200 题 | 15 题问题重合；37 题涉及历史 evidence 中的文章 | 如要求相关文章不跨开发/验收，保守排除这 37 题 |
| WixQA Simulated | 200 题 | 16 题问题重合；17 题涉及历史 evidence 中的文章 | 同一规则排除这 17 题；保持模拟标签 |

WixQA 两组排除后的数量上限为 163 和 183，**不是已经证明新鲜的验收数量**。本轮 article 匹配来自历史 evidence.document_id，并非仅因为文章存在于 corpus 就认定泄漏；真实检索允许开发/验收共用索引，但相关文章分组更严格。尚需检查新验收组之间共享相关文章的连接关系，避免按单题随机切分。

原始锁定文件已有 21 份来源 checksum，本轮全部核对一致。WixQA 语料为 6,221 篇文章；标签为 article relevance，不能计算 Doc2Dial 式字符 span 完整召回。MTRAG 的 35 个 heldout 对话是本项目已有自定义分组，并非官方独立测试集。

## 为什么保存两轮审计

初轮只扫 artifacts/eval 的 275 份 case/query/prediction JSON/JSONL（含 gzip），得到 Doc2Dial 425 个已出现 test 对话、WixQA 两组各 12 个问题匹配。

第二轮加入历史 consumption-audit 的 excluded_group_ids，以及已发现的两处 WixQA 临时输出目录，共 278 份文件。Doc2Dial 排除数变为 516；WixQA 问题匹配变为 15/16。这说明当前文件树不是完整消耗账本，历史排除记录必须保留。

这里“暴露”包含准备过的案例，不能自动等同于执行过模型；“未匹配”也不能证明没有在其他机器、已删除文件、改写查询或未扫描报告中使用。产物明确记录 `freshness_attested=false`，当前不签发封存证明。初轮报告只作为范围不足的历史证据。

## 复现与检查

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_rag_dataset_exposure.py \
  --root artifacts/eval \
  --root /tmp/dialogpilot-wixqa-heldout.XlAqyC/output \
  --root /tmp/dialogpilot-wixqa-smoke.2sEbkr/output \
  --lock artifacts/eval/rag-three-dataset-lock-v3-2026-09-07 \
  --cache /tmp/dialogpilot-rag-external-lock-20260907 \
  --archive /tmp/doc2dial_v1.0.1.zip \
  --output /tmp/rag-exposure-new-output
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_rag_dataset_exposure.py
```

临时原始文件缺失时必须恢复锁定版本并核对 checksum；扫描目录不存在不能被当作有效的零暴露证据。历史文件今后增加时重跑计数可以增长；提交的 inventory 保留本轮逐文件 SHA、问题 hash、ID 和匹配结果，可独立复算当前汇总，不包含原始答案文本。

检查：2 项测试通过，包含多层嵌套与继承排除信息的不变量，以及产物计数/来源校验/非新鲜状态检查。代码只新增审计入口，没有改变生产检索或核验。

产物：[最终报告](../artifacts/eval/rag-g4-exposure-audit-v2-2026-09-07/report.json)、[逐文件清单](../artifacts/eval/rag-g4-exposure-audit-v2-2026-09-07/inventory.json.gz)。

下一活动项：补齐 MTRAG 的生产检索评测数据适配与完整语料校验；保留已有对话分组，先零 API 验证 query/qrel/source 对齐，再预注册少量开发检查。公共检索基准不强行通过电商 Agent 的业务范围路由。中文电商真实 Context→工具→答案验收继续单列。总体 RAG 验收未完成，微调保持暂停。
