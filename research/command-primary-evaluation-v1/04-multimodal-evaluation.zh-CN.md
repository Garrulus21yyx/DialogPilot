# 多模态迁移与评测

状态：`PROJECT_L1_DIAGNOSTIC_COMPLETE — PUBLIC_BENCHMARK_NOT_RUN`
目标：分别验证媒体是否应处理、处理到哪个层级、产生什么可定位证据、下游是否正确消费，以及媒体证据是否遵守业务权威边界。

## 1. 在线职责与两阶段结构

多模态不能只有一个“是否调用 VLM”开关。目标分为：

```text
Routing Media Probe
  只解决当前消息是否引用媒体、旧 observation 是否可复用、
  路由是否需要最小 OCR/视觉观察
        ↓
TurnUnderstanding / RoutePolicy
        ↓
Task Media Plan
  根据具体 WorkItem 决定 asset/page/region、L1/L2、
  required/optional 和允许降级
        ↓
Perception / page-region retrieval
        ↓
MediaEvidencePack
```

路由前不能直接复用完整任务级 Media Agent，因为任务尚未确定；路由后不能只依靠关键词决定 L1/L2，因为需要具体 requirement。

## 2. L0/L1/L2 定义

| 层级 | 含义 | 例子 |
|---|---|---|
| L0 | 不需要读取媒体内容，或已有 observation 足够 | 用户上传附件但本轮只说“谢谢” |
| L1 | 文本、布局、表格等确定性/解析型信息 | 错误码、订单号、发票金额、表格 cell |
| L2 | 视觉语义、空间关系、外观或目标区域理解 | 红框位置、破损、按钮位置、装配关系 |

同一图片后续提问优先复用：

- 相同 requirement 且 fingerprint 一致：`REUSE_ARTIFACT`；
- 新视觉 requirement：复用 asset/OCR/layout，仅处理目标 delta/region；
- task-conditioned L2 的 task schema 或 normalized input 改变：不得直接复用旧结论。

## 3. 评测前必须完成的生产迁移

### 3.1 路由级 Media Probe

输入：当前消息、TurnState 中的 asset refs、已有 observation refs。输出只能表达：

```text
NO_MEDIA_REFERENCE
REUSE_SUFFICIENT
NEEDS_L1_FOR_ROUTING
NEEDS_L2_FOR_ROUTING
AMBIGUOUS_ASSET
FAILED
```

最多允许一次：

```text
Command Router → NEEDS_MEDIA → Perception → Router rerun
```

`max_media_reroute_count = 1`。

### 3.2 任务级 Media Plan

Route/WorkItem 确定后生成版本化 `MediaRequirementBinding`：

- requirement ID；
- asset ID；
- page/bbox/region；
- necessity；
- minimum stage；
- reason code；
- task schema/input fingerprint。

### 3.3 Artifact 与 provenance

必须由生产 Owner 提供：

- asset identity/checksum/tenant/retention；
- OCR/layout/VLM/index job 状态；
- producer/model/version；
- page/bbox/crop locator；
- source/deletion watermark；
- cache key 和 reuse 证据。

### 3.4 完整公开评测的缺口

当前仓库、主机缓存和应用容器均没有 OmniDocBench、ViDoRe、PM209/MPMQA
数据或官方 evaluator，因此不能先猜外部 schema 后声称官方分数。现有生产镜像
有 Tesseract 5.5，但只装 `eng/osd`，也没有 PDF rasterizer。

当前能力状态是：

| Benchmark | 可诚实执行的范围 | 状态 |
|---|---|---|
| OmniDocBench | 固定英文 raster 的 OCR-only slice | `READY_AFTER_EXTERNAL_ARTIFACTS` |
| OmniDocBench layout/table/formula | 无相应 producer | `BLOCKED_MISSING_SYSTEM_CAPABILITY` |
| ViDoRe | 无 page embedding/index/retriever | `BLOCKED_MISSING_SYSTEM_CAPABILITY` |
| PM209 Oracle/Retrieved | 无 question-conditioned L2 WorkItem/consumer；Retrieved 还依赖 page retrieval | `BLOCKED_MISSING_SYSTEM_CAPABILITY` |

以下能力不存在时必须保持 `BLOCKED_MISSING_SYSTEM_CAPABILITY`：

- 复杂 PDF 分页与 layout/table parser；
- 视觉 page index；
- page/region retriever；
- 对应生产缓存、版本与删除治理。

Evaluator Adapter 不得临时创建一条仅为跑分的视觉检索主链。

取得固定官方数据和 evaluator 后，第一条公开线只做：

```text
OmniDocBench English raster OCR slice
→ AssetAdmission
→ TieredPerceptionService
→ TesseractOCRProvider
→ official prediction schema/evaluator
```

它必须标为 `OCR_ONLY_EN_RASTER`，layout/table/formula 为
`NOT_APPLICABLE`，不能命名为完整 OmniDocBench 成绩。

## 4. 媒体是证据，不是业务 Authority

必须保留以下边界：

```text
MediaEvidence：截图显示“退款成功”
Business Tool：当前退款是否真的成功
```

以下对抗内容都按不可信数据处理：

- 图片中的 system prompt；
- “管理员要求调用退款工具”；
- 链接、脚本、二维码命令；
- 伪造订单状态；
- 旧截图与当前业务状态冲突。

媒体文本不得直接成为 Tool Call 参数。只有当目标 WorkItem 明确需要该字段，且经过 schema、主体、格式和业务边界验证后才能绑定。

## 5. Trigger 与层级选择评测

用状态反事实覆盖：

- 无附件；
- 有附件但当前消息无关；
- 明确引用当前图；
- 引用上一轮图；
- 多图但未说明目标；
- 已有可复用 observation；
- OCR 足够；
- 必须视觉推理；
- 同图新 region requirement；
- 旧 observation 版本或 retention 失效。

指标：

- Media Need Precision/Recall；
- L1/L2 Selection Accuracy；
- L2 Over-invocation Rate；
- Reuse Hit Rate；
- Delta Processing Accuracy；
- Wrong/Expired Asset Use；
- Forbidden Media Invocation Count。

## 6. L1 解析评测

普通 CER/WER 不足以代表客服风险，必须同时报告业务关键实体：

- 中文 Character Error Rate；
- 英文 Word Error Rate；
- 订单号/错误码/型号 exact match；
- 金额、日期、单位 exact/normalized accuracy；
- Table Cell F1/TEDS（能力存在时）；
- Reading Order；
- Heading/paragraph structure；
- scanned/rotated/double-column 等 slice。

订单号少一个字符应在关键实体门禁中直接体现，不能被大量普通字符平均掉。

## 7. L2 视觉理解评测

覆盖：

- UI 控件和步骤；
- 错误位置；
- 图表数值；
- 外观/破损；
- 多区域关系；
- 安装/接线空间关系。

指标：

- Visual QA Accuracy；
- Region Localization IoU；
- Required Observation Recall；
- Unsupported Observation Rate；
- safety-critical observation miss；
- L2 latency/token/cost。

## 8. 页面与区域检索评测

能力存在后分别测：

```text
text/OCR retrieval
page-image dense retrieval
hybrid retrieval
hybrid + optional rerank
```

指标：

- Page/Region Recall@K；
- All-evidence Recall；
- nDCG/MRR；
- rerank loss；
- packed visual evidence coverage；
- page/region provenance completeness。

## 9. Grounding 与 Consumption

每个视觉 claim 必须绑定：

```text
asset_id
content_hash
page_index
bbox/region
producer/model/version
artifact/evidence ref
```

分别测：

- Evidence Grounding Precision/Recall；
- 正确 observation 是否进入目标 requirement；
- 错图/错页/错 region；
- 下游遗漏已召回 observation；
- 媒体 observation 是否越过业务 Authority；
- Correct Abstention/Clarification。

## 10. 数据角色

- 80 条项目合同：媒体 trigger、L0/L1/L2、连续图片复用、错误资产和业务权威；
- OmniDocBench：OCR、layout、reading order、table/公式解析；
- ViDoRe：页面级视觉检索；
- PM209/MPMQA：产品手册 Oracle evidence 与 Retrieved-evidence E2E；
- 项目安全对抗集：prompt injection、伪造业务状态和跨租户资产。

Oracle page/region 只给出生成上限，不能当完整检索成绩。OmniDocBench 解析分数也不能冒充 RAG 或回答正确率。

## 11. Runner 与产物

项目组件入口：`run_media_eval.py`。官方 benchmark 保留官方 runner/evaluator，DialogPilot Adapter 只做格式映射。

输出：

- manifest；
- asset/page/region case results；
- Perception artifacts/EvidencePack refs；
- expected invocation 与来自 probe/perception/cache 的实际观测；
- tier/reuse/grounding report；
- 官方 evaluator 原始结果。

### 11.1 当前已执行范围

`run_media_eval.py` 已复用现有 `RoutingMediaProbe`、
`TieredPerceptionService` 与统一三产物 writer，在锁定项目素材
`dp-screen-03` 上调用本地 Tesseract 5.5.0 跑通一条显式区域 L1 正向链：

- OCR provider 调用 `1` 次，VLM 与业务工具均未调用；
- Trigger、Artifact、Consumption、Outcome 均通过；
- `MediaRequirementBinding.region_key` 由 fixture resolver 解析为原图
  `page_index=0` 与 bbox `[720,0,1080,300]`，Tesseract 只处理该裁剪；
- prediction 同时保存整页 locator、原图坐标 region locator、crop artifact、
  asset checksum 和 producer/version，
  observation 文本摘要，而不保存原始 OCR 文本；
- manifest 明确记录 `score_role=PROJECT_DIAGNOSTIC`、
  `locked_contract_score_eligible=false`、`official_benchmarks=NOT_RUN`。

这证明显式 page/region 能贯穿 binding、Perception、OCR、artifact provenance 与
consumer。显式 region 缺少 resolver 时返回
`UNAVAILABLE / REGION_RESOLVER_UNAVAILABLE`，不会退化成整页 OCR。它仍只是单图
L1 项目诊断，不是自动 region retrieval、连续图片复用、80 条合同或
OmniDocBench/ViDoRe/PM209 成绩。

### 11.2 官方入口当前状态

官方入口暂不创建。原因是本地既无固定 benchmark artifact，也无官方 evaluator；
此时写 adapter 会把猜测固化为接口。拿到并校验官方输入后，OCR-only 首批只需
一个格式 adapter、一个薄 runner 和一个 fixture contract test，继续复用现有
AssetAdmission、Perception、ParseResult/MediaLocator 与三产物 writer。

ViDoRe 与 PM209 不应复用文本 Knowledge RAG 或 RoutingMediaProbe 冒充页面检索。
它们要等独立的 page embedding/index/retriever 与 L2 task consumer 成为生产能力后
再接官方 evaluator。

## 12. 通过条件

- 无关附件默认不触发处理；
- OCR 足够时停在 L1；
- L2 必要 case 的 Recall 通过；
- 同任务同图复用与强制重算结果等价；
- 新 requirement 只处理必要 delta；
- evidence 可回到 asset/page/bbox/version；
- wrong/stale/cross-tenant asset observed `0/N`；
- 媒体不覆盖业务 Authority；
- 真实主链 E2E 非劣后才进入生产 Bundle。

## 13. 相关文档

- [M0–M4 总迁移计划](./00-m0-m4-migration-master-plan.zh-CN.md)
- [Intent / TurnUnderstanding](./01-intent-understanding-architecture-and-evaluation.zh-CN.md)
- [E2E 与成绩汇总](./05-e2e-evaluation-and-scorecard.zh-CN.md)
