# DialogPilot command-primary 迁移与评测专题

状态：`IMPLEMENTATION_IN_PROGRESS`
基准日期：2026-09-03

本目录把生产架构迁移、组件评测和最终端到端验收拆开记录。它不覆盖现有已发布文档，也不把历史实验重新解释成新架构的最终成绩。

核心结论：

> 先建立 state-first、command-primary 的共同边界；随后分别测 Understanding、Knowledge、Memory 和 Multimodal；最后将冻结配置装入同一个 `ChatApplication.handle()` 主链做合同与公开 E2E。

## 文档导航

1. [M0–M4 总迁移计划](./00-m0-m4-migration-master-plan.zh-CN.md)
2. [Intent / TurnUnderstanding 架构与评测](./01-intent-understanding-architecture-and-evaluation.zh-CN.md)
3. [Knowledge RAG 迁移与评测](./02-knowledge-rag-evaluation.zh-CN.md)
4. [Memory RAG 迁移与评测](./03-memory-rag-evaluation.zh-CN.md)
5. [多模态迁移与评测](./04-multimodal-evaluation.zh-CN.md)
6. [E2E、故障注入与成绩汇总](./05-e2e-evaluation-and-scorecard.zh-CN.md)

## 如何阅读

- 要确定先做什么，读总迁移计划。
- 要替换旧 Intent，读 TurnUnderstanding 文档。
- 要解释为何旧 Chunk 结果仍保留、但新主链还需重建基线，读 Knowledge RAG 文档。
- 要区分每轮状态与长期记忆检索，读 Memory RAG 文档。
- 要区分附件路由、OCR、视觉推理和页面检索，读多模态文档。
- 要知道最终怎样判通过、怎样汇总以及何时报告 `pass^4`，读 E2E 文档。

## 当前可复用与阻塞状态

| 能力 | 可以复用 | 仍阻塞最终成绩 |
|---|---|---|
| Intent | state-first planner、Registry、selective producer、严格结构化 LLM command producer、历史 V1/V2 | 旧 9 类 artifact 不能充当 Command/Flow 头；需新标注、真实 artifact、校准与 heldout 门禁 |
| Knowledge | raw Dense/FTS 输入已分离；同一 provider/profile；本地 BGE-M3 generation 与四组 candidate 诊断已跑通 | 必须在 Doc2Dial Dev 选 Chunk，再调融合/查询/选择；不得用已查看 heldout 选参 |
| Memory | Turn State 与 ServiceEpisode 已分离；Dense/purpose/generation 已接通；LoCoMo session baseline 已跑通 | 需 owner-valid 非空 ServiceEpisode corpus；公开线还需 BGE/RRF 与 LongMemEval S-cleaned frozen test |
| Multimodal | 显式 routing probe、asset/evidence 合同、task-owned 单资产 L1 command-primary 消费链 | L2、多资产、跨轮复用、PDF/layout 与 page/region retrieval 尚未闭环 |
| E2E | 真实 `ChatApplication.handle()` 已跑通 Knowledge、sticky read-only 和 L1 transport 切片 | 80 条合同仍为 `NOT_RUN`；Intent/Media 门禁、Shadow 与 τ³ bridge 尚未完成 |

## 文档权威边界

本目录定义目标迁移与验收协议，不声称其中目标类型已经实现。运行事实仍以代码、数据库 Owner、测试结果与冻结 artifact 为准。文档中的 `READY`、`PASS` 和最终分数只能由对应门禁产物产生。
