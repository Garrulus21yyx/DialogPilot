# DialogPilot command-primary 迁移与评测专题

状态：`DRAFT_FOR_IMPLEMENTATION`
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
| Intent | V1/V2 capture、Encoder 训练与校准骨架、历史错误样本 | Flow/OOS 合同未统一；生产主链仍由旧 intent 驱动 |
| Knowledge | Chunk、query、rerank、packing 指标与历史 artifacts | 当前 PG Dense 为 hash 且文档/查询输入不对称；需真实 BGE-M3 generation |
| Memory | ServiceEpisode 生命周期、检索/证据合同、Commitment 独立 Owner | Turn State 与检索需要彻底分离；ServiceEpisode Dense projection 与目的化 policy 尚需冻结 |
| Multimodal | 直接图片的 L0/L1/L2、asset/evidence 合同、本地 E2E | 完整 PDF/layout、视觉 page index、page/region retriever 尚未齐全 |
| E2E | `ChatApplicationRunner`、Admission/CAS、Publication/Delivery 基础 | command-primary 主链、统一 Capability Trace、80 条真实 runner、τ³ bridge |

## 文档权威边界

本目录定义目标迁移与验收协议，不声称其中目标类型已经实现。运行事实仍以代码、数据库 Owner、测试结果与冻结 artifact 为准。文档中的 `READY`、`PASS` 和最终分数只能由对应门禁产物产生。
