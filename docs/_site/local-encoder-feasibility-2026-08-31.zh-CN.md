# 本地 Encoder 意图分类可行性试验

## 结论

冻结的 `BAAI/bge-m3` 向量加 Logistic Regression 分类头，在英文公开数据上可行且明显优于零训练的模板相似度；但它没有超过当前 V1 在更严格上游 test 上的结果，并且在中文合成诊断上暴露出严重的 OOS 泛化不足。因此本轮不切换生产意图路由。

## 正向契约与范围

- 支持标签：`account`、`account_security`、`logistics`、`payment_issue`、`query`、`refund`、`technical`、`technical_login`。
- `other` 表示 OOS；本实验没有把它进一步拆成 `out_of_scope` 和 `insufficient_context`。
- Encoder 完全冻结，只训练轻量分类头。
- 输入 Encoder 的只有消息和显式对话历史，不包含标签、数据源或评审信息。
- 这是离线可行性试验，不是生产发布证据。

## 数据协议

- 500 条 dev：确定性分层切为 400 训练、100 内部验证，用于选择结构和超参数。
- 选定配置后在全部 500 条 dev 上重训分类头。
- 500 条 heldout：本实验不用于选择，但在此前权重实验中已被使用，因此不属于项目级全新封存集。
- 300 条上游 test：本实验不用于选择，但此前也已用于 V1/V2 验证。
- 100 条中文/中英合成样本：只保留标签代数兼容的 85 条作诊断；它们由独立 LLM 复核，没有人工金标且此前已经使用。

## 选择结果

验证集在联合九分类与两阶段 OOS gate 两种结构间选择。最终选择：

```json
{
  "architecture": "joint",
  "classifier_c": 10.0
}
```

联合分类验证集为 93/100、macro-F1 0.9286、OOS recall 95%。最好的两阶段配置同为 93/100，macro-F1 0.9274、OOS recall 95%；差异很小，按简单性选择联合分类。该结果不代表生产上已经解决 typed rejection。

## 结果

| 数据 | Encoder 分类头 | 直接 LLM 捕获 | 当前 V1 | OOS recall |
|---|---:|---:|---:|---:|
| 500 heldout | 477/500 (95.4%) | 428/500 (85.6%，旧捕获) | 427/500（旧报告） | Encoder 94% |
| 300 上游 test | 281/300 (93.7%) | 282/300 (94.0%) | 283/300 (94.3%) | Encoder 92%，V1 约 96% |
| 85 中文/混合合成诊断 | 71/85 (83.5%) | 85/85 (100%) | 84/85 (98.8%) | Encoder 53.3% |

500 heldout 与300上游 test 的差异说明同源划分与更独立上游 split 之间存在泛化差距。中文诊断进一步说明：多语言 Encoder 并不会自动弥补训练标签只有英文的问题。

## 主要错误机制

1. `payment_issue` 与 `technical` 边界混淆，例如本人卡支付失败被判为一般技术故障。
2. `payment_issue` 与 `account_security` 边界混淆，例如“额外扣费”和“陌生交易”的主体归属不明确。
3. OOS 表面包含账户、物流、查询词汇时，被吸入已知类别。
4. 中文信息不足表达（“还是没变化”“怎么又这样了”）被判为 `technical/query`，说明尚未建立 `insufficient_context` 契约。

## 性能

在当前机器 CUDA 环境的单次测量：

- BGE-M3 冷加载约 1.95 秒；
- 分类头加载约 24 毫秒，文件约 38 KB；
- 100 条批处理约 70.8 毫秒，约 0.71 毫秒/条；
- 单条暖推理 p50 约 10.6 毫秒，p95 约 17.5 毫秒。

这些是本机可行性数字，不是跨机器 SLA。

## 下一步退出条件

在考虑生产接入前，至少需要：

1. 新建不与诊断集复用的中文训练集，覆盖每类直接表达、隐式表达、否定、纠正和多意图边界。
2. 为 `out_of_scope` 与 `insufficient_context` 建立独立金标与类型化输出。
3. 加入 `payment_issue ↔ technical ↔ account_security` 困难负例。
4. 在未参与训练和提示词迭代的新鲜中文留存集上验证，且 OOS、安全类和总体指标均满足门槛。
5. 对分类概率重新校准；当前上游 test 的 ECE 约 0.129，不能直接把 raw probability 当可靠路由概率。

## 产物

- `evaluation/local_encoder_classifier.py`
- `scripts/run_local_encoder_experiment.py`
- `tests/test_local_encoder_classifier.py`
- `artifacts/eval/local-encoder-feasibility-2026-08-31/report.json`
- `artifacts/eval/local-encoder-feasibility-2026-08-31/classifier.joblib`
