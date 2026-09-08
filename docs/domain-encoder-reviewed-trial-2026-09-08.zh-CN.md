# 审校数据GPU试训

## 结论

中文、英文各完成一次固定配置试训，均不通过，不启用线上Encoder。
两种模型在训练、校准、开发输入上都只预测billing_refund；因此失败不只是校准样本不足，
本次训练没有表现出领域区分能力，更不能证明学到了跨轮上下文关系。

| 语言 | 训练正确 | 校准正确 | 开发正确 | 开发macro-F1 | 可启用领域 |
|---|---:|---:|---:|---:|---:|
| 中文 | 61/225 | 21/70 | 20/67 | 0.0657 | 0 |
| 英文 | 70/237 | 17/47 | 8/24 | 0.0714 | 0 |

表中是未做阈值筛选的argmax分类，不是接受精度。实际快速接受数为0，接受精度未定义。
不能将0接受解释成所有分类都错，也不能将多数域命中算作有效路由。

## 固定条件和数据

- 数据：`data/training/domain-encoder-v3-reviewed`，不混旧口径、隔离样本和封存评测。
- 中文BGE-small-zh-v1.5、英文BGE-small-en，沿用已有固定revision；分别训练，不混语言。
- Transformers Trainer、RTX3080、seed17、4epochs、batch24、学习率3e-5、fp16、最大输入256token、rows采样。
- 每语言40个训练step，无超参搜索、无择优重跑、无教师API。
- 保持既有98%精度和每域至少10次接受的条件；当前训练输出manifest均为REJECTED。
- 开发集已用于审校，仅是开发证据，不称新鲜留出成绩。

小样本、类别不平衡和有限训练步数可能影响学习，但本次实验不能区分其各自因果贡献；
没有据此宣称只增加epoch或只补数据即可修好，也没有改线上路由兜底掩盖结果。

## 产物与复现

`artifacts/domain-encoder-v3-reviewed-trial-2026-09-08/{zh,en}`保留权重、tokenizer、训练配置、
训练指标和manifest。`zh-metrics.json`、`en-metrics.json`记录每条预测、概率、数据与模型哈希。
权重留在本地，不将大模型文件写入Git；配置、指标、数据来源可追溯。

```bash
.venv/bin/python -m evaluation.domain_encoder_training \
  --data data/training/domain-encoder-v3-reviewed/zh \
  --output artifacts/domain-encoder-v3-reviewed-trial-2026-09-08/zh --language zh
```

英文替换语言目录和参数。输出已存在时拒绝覆盖；以上仅记录首次命令，不用于重复跑。
`evaluation.domain_trial_metrics`直接读取冻结权重做离线诊断，支持REJECTED产物，
不改变线上manifest门槛或增加第二条在线推理路径。

## 后续验收范围

按用户新增目标，补一批独立助手创作且不读取本轮预测的校准/评测数据，固定权重复核。
这仍是合成数据，不称真人Gold；小样本可以否定当前学习效果，不能证明98%生产精度。
不达标就保持Encoder关闭，随后运行10个此前本地未注册的τ³开发任务，完整保留失败。
τ³当前适配是单retail领域，不把它的结果宣称为Encoder或多域路由收益。
