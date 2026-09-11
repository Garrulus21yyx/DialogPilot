# DialogPilot 评测收敛与证据归档计划（2026-09-02）

## 目标

把已经完成的 Intent、Chunk、Query、Rerank、父子 Chunk、Memory、多模态与 Agent E2E 工作放进同一套可追溯评测合同，明确哪些证据可以复用、哪些只能作为开发诊断、哪些入口仍缺失，并给出从开发集调参到冻结测试和最终成绩单的唯一执行顺序。

## 约束

- 80 条现有数据定位为合成架构合同，不冒充人工 Gold 或公开能力分数。
- 公开基准与项目合同分成两条成绩线；开发集、冻结测试集严格分离。
- `pass^4` 只在最终随机 Agent E2E 中计算；确定性检索回放不重复四次。
- 所有 Agent E2E 必须经过真实 `ChatApplication.handle()`。
- 已有实验保留为带适用范围的证据；失败的父子 Chunk / MiniLM CrossEncoder 不重复包装成候选默认。
- 不增加新 Dataset 平台、人工 Gold、灰度链路或与本轮收敛无关的框架。
- 工作区现有未跟踪文件视为用户工作，不覆盖、不删除、不擅自提交或推送。

## 步骤

| 状态 | 步骤 | 验证与产物 |
|---|---|---|
| done | 1. 审计本地代码、报告、数据清单和线上已发布实验 | 已核对 Intent/RAG artifacts、当前 PostgreSQL/Memory/Media 代码、80 条数据验证报告、`f5b1aa1` 及 τ³ v1.0.1 指标实现 |
| done | 2. 定义统一评测合同和参数所有权 | 已定义两条成绩线、配置/数据双状态、统一三文件产物、trial 边界、Owner 前置修复与冻结规则 |
| done | 3. 写出端到端执行路线 | 主档案已覆盖 Intent head → Knowledge Chunk/RAG → Memory RAG → 多模态 → 合同集 → τ³ E2E，并记录历史实验重开条件 |
| done | 4. 机械收口 80 条合成合同集与文档导航 | 已迁移为 `dialogpilot-synthetic-contract-v1`；4 个旧符号造成的 8 条引用错误已修复；80 条状态与 manifest/checksum 已冻结；`valid=true`、`run_status=NOT_RUN` |
| in_progress | 5. 验证文档与仓库一致性 | 运行相关校验/测试、链接和负向搜索；用 fresh-context reader review 检查歧义与矛盾 |

## 预期修改文件

- `docs/customer-service-agent-evaluation-plan.zh-CN.md`：唯一主评测档案与执行手册。
- `docs/customer-service-agent-evaluation-datasets.zh-CN.md`：数据集角色、split 和泄漏边界。
- `docs/rag-pipeline-evaluation.zh-CN.md`：引用主档案，保留 RAG 历史实验的局部证据。
- `docs/index.md`：增加主档案入口（若当前导航结构需要）。
- `data/eval/dialogpilot-synthetic-contract-v1/*`：锁定后的 80 条合成合同；不得重写样本语义。
- 本计划文件：持续记录状态与实际修改清单。

## 暂不视为完成的事项

- 尚未运行的公开基准不得填入成绩。
- 未接入真实 BGE-M3 的向量权重调优不得宣称有效。
- 仅 12 条合成多条件集上的 CrossEncoder 改善不得晋级线上默认。
- 父子 Chunk 未通过跨切片非劣门禁，不进入最终配置搜索。
- `num_trials=4` 不代表 τ³ 所有开发评测必须跑四遍。

## 修改记录

- 2026-09-02：创建计划；开始仓库与线上证据审计。
- 2026-09-02：完成证据盘点。确认旧 Intent 结果为已消费回归；旧 RAG 结果可作历史选择/拒绝证据，但 Chroma-era capture producer 已删除；当前 Knowledge/Memory dense 仍为 hash embedding；四个统一薄入口和三套多模态公开适配均缺失；80 条合同静态校验仅因 4 个已删除测试符号产生 8 个引用错误。
- 2026-09-02：完成 80 条合同机械收口。目录、dataset/schema/status 已改为 synthetic contract；新增 dataset role、authoring provenance、lock/run 双状态与全文件 checksum；校验结果 `valid=true`，明确尚未执行真实 E2E。
