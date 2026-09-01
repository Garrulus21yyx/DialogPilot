# 客服 RAG 候选生产切换计划（2026-09-01）

## 目标合同

把 Doc2Dial Dev 冻结候选接入 API 默认链路，同时保持事实权威不变：知识库只提供公共政策证据，订单、退款、账户等实时事实仍由业务工具拥有。一次请求固定同一份检索策略，并满足：

- 索引采用 fixed 512/64；已存在但配置不兼容的索引必须确定性拒绝启动并给出重建要求，不能混用旧 chunk。
- 首阶段采用 BM25 0.75 + Dense 0.25、RRF k=10；Raw 0.25 + Standalone 0.75 融合，保留 Raw fallback。
- 每路召回至 20 个稳定 chunk ID，融合后 listwise 重排 20→5；模型失败保留首阶段顺序。
- 最终知识上下文由 `ContextPacker` 限制为 Top-5/2600 estimated tokens，保留 chunk/document/source offsets。
- `GroundedAnswerGenerator` 只基于打包证据生成可引用的知识草稿；通用 Agent 仍负责把公共知识与实时工具结果组合，Verifier 仍拥有发布门禁。
- `/search` 与 `/chat` 复用同一查询、融合、重排 Owner；缓存键覆盖冻结策略。
- frozen candidate 在 group-disjoint Doc2Dial heldout 上只运行一次，结果不反向调参；确定性合同、延迟和失败路径另设门禁。

## 因果面与步骤

- [completed] 建立运行时策略、索引指纹和不兼容索引的 typed fail-closed 合同。
- [completed] 将 Raw/Standalone 加权融合、20→5 重排接入 `MCPToolManager`，并迁移 Bundle/API 默认值。
- [completed] 将 Top-5/2600 packing 与 grounded knowledge draft 接入 `/chat` 的知识投影边界。
- [completed] 增加 owner/integration/property tests，覆盖 fallback、稳定 ID、预算、旧索引拒绝和实时事实优先级。
- [completed] 构建并一次性运行 Doc2Dial test split 冻结报告；真实流量串行 P95/P99 明确保留为外部发布门禁。
- [completed] 同步项目讲述、架构、面试问答和 RAG 页面，明确“已默认接入”与尚未完成的 shadow/canary 边界。
- [completed] 审查 diff，仅提交本次 RAG 文件并推送主分支。

## 非目标

- 不把 Doc2Dial 文档作为真实商家政策直接发布到生产知识库。
- 不让 HyDE 或模型生成的 Standalone 文本成为事实证据。
- 不替换订单、退款、账户服务的实时业务事实，也不绕过审批和 AnswerVerifier。
- 不因一次 heldout 失败临时改阈值；失败则保留候选状态并记录阻断项。

## 退出标准

1. 配置只有一个明确的默认来源，Bundle 覆盖值完整进入缓存与 trace 语义。
2. 旧索引无法以新策略静默服务；空索引按新配置创建并可导入。
3. Raw 永远存在，Standalone/重排失败均有确定性退化；Top-5/2600 永不越界。
4. heldout 指标、bootstrap 区间、关键 slice、格式/引用失败、端到端延迟均有可复现产物。
5. 代码、测试、文档和线上 Pages 对“默认值、证据、剩余发布门禁”的表述一致。

## Verification record

- 提交文件集复现命令（排除工作区未提交的 3 个 intent 实验测试）：`PYTHONPATH=. .venv/bin/pytest -q --ignore=tests/test_chinese_intent_training_data.py --ignore=tests/test_intent_cascade.py --ignore=tests/test_local_encoder_classifier.py`：329 passed。
- `docker compose config --quiet`：通过。
- 真实 embedded Chroma smoke：v4 fixed 512/64；Raw/Standalone × BM25/Dense 四路排名均进入 weighted RRF；以 360/48 重开同一非空索引得到 `IncompatibleKnowledgeIndexError`。
- Doc2Dial test：40 documents / 48 retrieval cases；模型链为 9 个 dialogue groups。Query harmful 0，Rerank harmful 0，Generation/Judge failure 0/0；详细指标与报告哈希见 `docs/data/rag-heldout-summary-2026-09-01.json`。
- Heldout 期间发现 evaluator 把 `know` 子串误判为 `no`；修复为英文词边界后只用同一 capture 重放，没有修改 Prompt、权重或生产分支。
- 尚未执行且不冒充完成：真实脱敏客服流量、人工盲审 Judge 校准、完整请求串行 P95/P99、shadow/canary。
