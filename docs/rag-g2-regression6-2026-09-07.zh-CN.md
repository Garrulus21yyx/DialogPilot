# 六条冻结回归与发布校验修复

2026-09-07，起点cff1809；23次Flash调用，上限28。既有六条案例保持不变。

| 维度 | 结果 |
|---|---:|
| Completed/verified | 6/6 |
| 必要原文打包且工具输出可见 | 5/6 |
| 有证据答案的引用有效 | 2/5 |
| 畸形引用 | 3条 |
| 无证据弃答 | 1条，EU工具返回AMBIGUOUS |

更正进度中的“四条引用格式不合法”：三条为畸形引用；第四条是缺证据弃答，没有引用本身不是格式错误。审计现在分别记录citation_applicable和格式有效性，并优先读取实际output_for_model。

## 引用发布根因

ResponseAssembler原来只提取合法形状的ID，再检查是否存在未知ID。[E]和[E:...]没有被提取，空集合会通过。语义verifier通过不能补足这一程序缺口。

知识工具合同现要求：有证据时至少一个精确供给ID；畸形、未知、未闭合E标记不能被忽略。最终发布边界调用该校验，不修改核验后的文本或猜测引用。无证据的合理弃答不强制制造引用。

零API重放原答案：三条畸形引用均拒绝，两条有效引用仍通过。永远PASS的verifier故障注入测试也无法发布坏引用。这证明发布门槛有效，不证明生成器已稳定产生合法引用；修复后完整链尚未重跑。

## 日期歧义的夹具根因

EU问题被添加当天policy_date。部分基础模拟政策未定义effective_from，SourceStore以导入时刻作为生效时间，落在所查当天区间，触发POLICY_DATE_REQUIRES_TIME。

模拟资料owner现显式声明2020-01-01生效，与适用性资料基准一致；生产生效默认值和跨版本日期校验不变。EU修复后全链尚未复验，不能写成已恢复。

## 验证范围

43项相关测试通过，覆盖引用/语义/来源、混合结果、撤回和新增畸形/缺失引用，以及模拟目录/日期。没有为刷通过重跑模型。

本次运行加载了另一工作项未提交的response_assembly/orchestration改动，full-manifest记录源hash；不能称为干净Git版本验收。本次只提交自己的citation gate调用hunk。

- [原运行结果](../artifacts/eval/rag-g2-regression6-2026-09-07/full-report.json)
- [独立审计](../artifacts/eval/rag-g2-regression6-2026-09-07/independent-audit.json)
- [引用门槛重放](../artifacts/eval/rag-g2-regression6-2026-09-07/citation-gate-replay.json)
- [验收矩阵](../plans/rag-acceptance-matrix.md)

下一步固定版本再验完整链；若引用继续失败，沿既有有界修订路径处理明确的引用反馈，不放宽门槛。整体RAG仍开放，微调暂停。
