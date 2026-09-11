# Encoder → LLM 低置信级联试验

目标：验证增强后本地 Encoder 在高置信样本上直接分类、其余样本交给 LLM，能否在保持当前质量的同时减少 LLM 调用。

正向合同：

- Encoder 是首阶段分类器；只有开发集校准后达到接受门槛的样本才能直出。
- 其余样本由 LLM 完整复判，不进行三路线性加权。
- 阈值 Owner 是开发集 risk/coverage 校准；测试集不得用于选择阈值。
- 主要候选目标：Encoder 已接受样本准确率至少 97%，在满足约束的阈值中覆盖率最高。
- 同时报告 95%、97%、99% 三档，展示质量/成本权衡。
- `other` 本轮仍是未分型拒识；`out_of_scope`、`insufficient_context` 和 provider failure 不得宣称已经实现。
- 本轮只做离线 replay，不切换生产。

步骤：

- [done] 1. 审计开发集分组、增强数据来源映射与现有 LLM 捕获覆盖。
- [done] 2. 实现无语义泄漏的分组 OOF Encoder 预测与 risk/coverage 阈值选择。
- [done] 3. 冻结阈值，回放 heldout、上游 test 与中文诊断的 Encoder→LLM 级联。
- [done] 4. 报告准确率、macro-F1、OOS/安全召回、LLM fallback 比例及错误迁移。
- [done] 5. 运行回归并决定是否值得进入生产集成阶段。

结果：

- 97% 主候选阈值：0.522578537464；开发 OOF Encoder 覆盖率 94.5%。
- 300 条上游 test：283/300，LLM fallback 14/300（4.7%）；与当前 V1 总正确数持平。
- 85 条中文/混合诊断：82/85，LLM fallback 15/85（17.6%）；高于纯 Encoder 75/85，低于当前 V1 84/85。
- 99% 档可达上游 284/300、中文 85/85，但 fallback 分别为 40% 和 63.5%，安全 recall 降到 88%。
- 全局阈值仍允许三条高于阈值的 `→ account_security` 中文误判；未解决类别风险和 typed rejection。
- `.venv` 完整回归：321 passed，2 skipped；系统 semantic runtime：10 passed。
- 结论：方向值得继续，但不进入生产集成；先补类型化拒识、类别风险校准和全新中文留存证据。

产物：

- `evaluation/intent_cascade.py`
- `scripts/run_encoder_llm_cascade.py`
- `tests/test_intent_cascade.py`
- `artifacts/eval/encoder-llm-cascade-2026-08-31/`
- `docs/encoder-llm-cascade-2026-08-31.zh-CN.md`
