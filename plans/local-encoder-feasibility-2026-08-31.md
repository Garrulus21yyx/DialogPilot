# 本地 Encoder 意图分类可行性试验

目标：在不修改生产意图路由的前提下，验证 `BAAI/bge-m3` 本地向量加一个监督分类头，是否比零训练的最近模板匹配更可靠，并与已捕获的 V1/LLM 结果作同集对照。

约束：

- 不使用留存集训练模型、选择超参数或确定阈值。
- 优先复用仓库中已有、来源明确的数据与本机缓存模型。
- 先训练冻结 Encoder + 轻量分类头，不做昂贵微调。
- 明确区分已知意图分类与 `OTHER/OOS` 拒识；不把 softmax 置信度当作天然可靠概率。
- 本轮只产出离线实验与可复现脚本，不切换生产实现。

步骤：

- [done] 1. 审计数据集、标签映射、现有捕获输出和本机依赖。
- [done] 2. 定义无泄漏训练/开发/留存协议及正向输出契约。
- [done] 3. 实现冻结 BGE 向量 + Logistic Regression 基线与拒识校准。
- [done] 4. 在开发集选择配置，在未使用留存集上一次性验证。
- [done] 5. 运行回归测试，记录指标、限制和是否值得生产化。

审计记录：

- 本机系统 Python 已有 `sentence-transformers 5.7.0`、`scikit-learn 1.9.0`、CUDA 和本地缓存的 `BAAI/bge-m3`。
- 校准数据共 1000 条：500 dev、500 heldout；标签为 8 个已知意图加 `other`。
- 额外上游 test 验证集 300 条，不参与本次训练或配置选择。
- 当前生产 `core/intent_recognizer.py` 的本地路径是字符 n-gram；真实 BGE 只存在于 V2 原型/离线捕获路径。

最终结果：

- 内部验证：93/100，macro-F1 0.9286，选择联合九分类 `C=10`。
- 500 heldout：477/500，macro-F1 0.9531，OOS recall 94%。
- 300 上游 test：281/300，macro-F1 0.9309，OOS recall 92%；低于当前 V1 的 283/300。
- 85 条兼容标签中文/混合合成诊断：71/85，OOS recall 53.3%；不具备中文生产替换条件。
- 本机 CUDA 暖单条推理 p50 约 10.6ms，p95 约 17.5ms；冷加载约 1.95s。
- 完整回归：318 passed，1 skipped（`.venv` 未安装可选 semantic runtime）。

产物：

- `evaluation/local_encoder_classifier.py`
- `scripts/run_local_encoder_experiment.py`
- `tests/test_local_encoder_classifier.py`
- `requirements-semantic.txt`
- `artifacts/eval/local-encoder-feasibility-2026-08-31/`
- `docs/local-encoder-feasibility-2026-08-31.zh-CN.md`
