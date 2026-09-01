# 客服 RAG 多条件检索收敛实验

## 状态

- [x] 明确重复重开后的共同根因与本轮边界
- [x] 审计现有 Query、Retrieval、Rerank、Packing 的权威合同
- [x] 实现结构化条件拆分与可复现 capture
- [x] 实现条件级召回、集合选择和父子扩展对照
- [x] 增加合同测试与属性测试
- [x] 运行 36 条全量、16 条 multi-span 及 12 条真正并列条件分层评测
- [x] 用门禁判定，不以单个总分宣称上线
- [ ] 更新文档、复现命令和线上页面（本地文档已更新，待构建/发布验证）

## 共同根因

此前把父子 Chunk 当作多条件完整性的修复手段。父子结构的权威职责是：命中一个细粒度锚点后补充同一局部上下文；它不拥有“问题包含哪些独立信息需求”这一事实。结果是 Parent 扩大了上下文，却没有保证不同条件各自被召回和保留，甚至因 Token 预算挤掉其他证据。

## 正向合同

1. Query 分析器只依据 Raw、Standalone 与对话历史，输出 `simple | parallel_composite | dependent` 和至多 3 个可独立检索的 requirement；Gold 永不进入生产路径。
2. Raw 查询永远保留；拆分失败以类型化结果回退 Raw，不静默制造空查询。
3. 每个 requirement 独立进行 Sparse/Dense 召回，候选保留 requirement 来源；候选集合先保证各 requirement 的配额，再全局去重。
4. 集合选择以“所有 requirement 的联合覆盖”为目标，输出只能引用输入候选的 stable chunk ID；格式错误可有限重试，仍失败则回退确定性配额策略。
5. 父子扩展发生在条件锚点选定之后，只补局部上下文；放不下 Parent 时降级 Window/Child，不得丢掉该 requirement 的锚点。
6. Packing 在预算内优先保留每个 requirement 至少一个锚点，再按边际覆盖补充；无法覆盖时显式记录 missing requirement。

## 对照组

1. `baseline-512-64`：Raw/Standalone → Hybrid → Listwise rerank → 普通 packing。
2. `requirements-512-64`：条件拆分 → 每条件 Hybrid → 集合选择 → 512/64 packing。
3. `requirements-dynamic-parent`：同上，选中锚点后再做动态 Parent/Window/Child 扩展。

## 数据标签审计

Doc2Dial Dev 当前 16 条所谓 multi-condition 是按 `evidence_count >= 2` 得到的；抽查显示多数只是同一答案在标注中被切成相邻的 2–3 个 span，而非两个独立用户需求。因此保留它作为 `multi-span completeness`，但不能单独证明 Query decomposition。另建小型、确定性、来源可追溯的并列客服问题 stress split，Gold 为已有单条件 case 的证据并集，专门测“每个独立条件是否被保住”。

## 主要指标与门禁

- 全量与 multi-condition 子集分别报告 Candidate Recall@20、Selected Recall@5、Packed Evidence Recall、Multi-condition Completeness、Harmful Context、P95 Token/延迟、模型调用与 Token 成本。
- 候选必须先改善或不退化；若 Candidate 已缺失 Gold，后续层不得被归因成生成问题。
- 候选进入下一层的损失单列，禁止用端到端均值掩盖。
- Harmful Context 不得增加；Multi-condition Completeness 的改善不能用不可接受的 Token/延迟换取。
- 本轮为 Dev 上的架构候选筛选，不等价于上线结论；通过后仍需 untouched Heldout、人工 Judge 校准和 shadow/canary。

## 非目标

- 不把 Gold 条件文本喂给 Query 分析器或检索器。
- 不接入 SetR 的训练栈；仅复用其“需求识别—证据映射—集合选择”责任划分。
- 不把所有客服查询强制拆分，也不把所有命中无条件升级为 Parent。

## 实验记录

- `query-requirement-capture-v1`：长文 36/36=`simple`；并列 stress 12/12=`parallel_composite`；两批协议错误均为 0。
- 真并列条件：拆 Query 将 Candidate `.7292→.7708`；Flash set selector Packed `.4931`，证明选择层是瓶颈。
- MiniLM cross-encoder + 每条件 2 anchors：并列 stress Packed `.6319`、P95 约 `203ms`、rerank Token 0，但 harmful `3/12`。
- 普通 36 条：cross-encoder Packed `.6111`，低于 Flash `.7222`，harmful `6/36`。
- rank-5/6 margin cascade 不能识别失败；达到相对 Flash `-1pp` 目标需要 100% fallback。
- Dynamic Parent 没有修复 cross-encoder 候选/选择缺口；不进入 Generation/Judge。

## 产物

- `mcp/query_requirements.py`：typed requirement Owner 与 Raw fallback。
- `mcp/evidence_set_selector.py`：typed set selector 与确定性 quota fallback。
- `evaluation/rag_requirement_capture.py`：一次捕获、离线复用。
- `evaluation/rag_parallel_stress_builder.py`：12 条来源可追溯的并列条件 stress split。
- `evaluation/rag_multi_condition_ablation.py`：Candidate→Selected→Packed 分层报告。
- `evaluation/rag_cross_encoder_ablation.py`：成熟 CrossEncoder、q1/q2 anchor 和低置信 cascade 回放。
- `docs/assets/eval/rag-multi-condition-cascade-dev-v1.json`：不含原文的发布摘要。
