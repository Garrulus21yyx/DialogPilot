# 中文 Encoder 训练增强与复测

目标：在不污染现有中文诊断集的前提下，为8个已知业务意图与 OOS 构建中文训练增强数据，重训冻结 BGE-M3 分类头，验证中文/OOS 泛化是否改善。

正向契约：

- 训练标签仅限 `account`、`account_security`、`logistics`、`payment_issue`、`query`、`refund`、`technical`、`technical_login`、`other`。
- `other` 训练数据必须同时覆盖明确域外和信息不足，但报告中承认当前模型仍未输出独立 typed status。
- 当前 `fresh-intent-candidate.jsonl` 及其85条兼容样本禁止进入训练或配置选择，只作为已消费诊断集。
- 合成/翻译样本必须记录来源、生成方式和审查状态，不声明人工金标。
- 只有在中文诊断、上游 test、OOS 和安全类均无不可接受回退时，才把候选交给后续生产接入；本轮默认仍是离线实验。

步骤：

- [done] 1. 审计可用生成模型、标签契约、源训练样本与重复风险。
- [done] 2. 实现可复现的中文训练增强生成与结构/重复校验。
- [done] 3. 生成每类50至100条中文训练样本并记录 provenance。
- [done] 4. 重训英文+中文 Encoder 分类头，只在开发数据上选择配置。
- [done] 5. 在上游 test 与既有中文诊断集上复测，分析增益和回退。
- [done] 6. 运行完整回归并形成是否接入生产的结论。

结果：

- 生成并复核 500 条中文训练增强：8 个业务意图各 50，`other` 100；SHA-256 `08a36107d3e7bb4451f6a26a343553e6915b31fdfdc12a55eab4234daa47b41a`。
- 增强后 500 heldout：475/500（增强前 477/500）。
- 增强后 300 上游 test：281/300（增强前 281/300，当前 V1 283/300）。
- 85 条中文/混合诊断：75/85（增强前 71/85，当前 V1 84/85）；OOS recall 53.3% → 86.7%。
- 安全与登录边界仍有回退，且没有全新中文留存集或人工金标，候选不接生产。
- 完整 `.venv` 回归：321 passed，1 skipped；可选 semantic runtime 的 Encoder 单测在系统 Python 通过。

产物：

- `data/training/intent-chinese-augmentation-2026-08-31/`
- `scripts/build_chinese_intent_training_data.py`
- `scripts/run_augmented_local_encoder_experiment.py`
- `tests/test_chinese_intent_training_data.py`
- `artifacts/eval/local-encoder-chinese-augmented-2026-08-31/`
- `docs/chinese-encoder-augmentation-2026-08-31.zh-CN.md`
