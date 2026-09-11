# Encoder → LLM 低置信级联试验

## 结论

Encoder 首判、低置信交给 LLM 的级联方向成立。开发集选择的 97% 接受精度候选在 300 条上游 test 上达到 283/300，与当前 V1 持平，只调用 LLM 14 次（4.7%）；在 85 条中文/混合诊断上达到 82/85，调用 LLM 15 次（17.6%）。它明显优于纯 Encoder，但尚未达到生产接入条件。

## 路由合同

```text
Encoder confidence >= frozen threshold
    → Encoder intent
otherwise
    → LLM 完整复判
```

没有三路加权。阈值由开发数据的 selective risk/coverage 决定，而不是直接在测试集上搜索。

本实验仍只输出具体 intent 或 `other`；`out_of_scope`、`insufficient_context` 与 provider failure 尚未形成独立 typed outcome。

## 无泄漏校准

校准集由 500 条英文 dev 与 500 条中文训练增强组成，共 1000 行、525 个语义 group。英文原句与其中文翻译共享 group，在 5 折 OOF 中始终位于同一折，防止语义副本一边训练、一边验证。

每折只用另外四折训练 Encoder 分类头，再为当前折产生未见样本预测。最终按“满足目标接受精度时覆盖率最高”选择阈值。

| 目标接受精度 | 阈值 | 开发集接受精度 | Encoder 覆盖率 | fallback 比例 |
|---|---:|---:|---:|---:|
| 95% | 0.3076 | 95.08% | 99.5% | 0.5% |
| 97% | 0.5226 | 97.04% | 94.5% | 5.5% |
| 99% | 0.8925 | 99.04% | 62.4% | 37.6% |

主候选在实验前定义为 97% 档。

## 冻结阈值结果

### 300 条上游 test

| 路径 | 正确数 | OOS recall | 安全 recall | LLM fallback |
|---|---:|---:|---:|---:|
| 纯 Encoder | 281/300 | 91% | 96% | 0% |
| 95% 档 | 280/300 | 90% | 96% | 0.3% |
| **97% 档** | **283/300** | **94%** | **92%** | **4.7%** |
| 99% 档 | 284/300 | 95% | 88% | 40% |
| 纯 LLM 捕获 | 282/300 | 95% | 88% | 100% |
| 当前 V1 | 283/300 | 约 96% | 88% | 100% 主调用 |

97% 档与当前 V1 总正确数相同，安全 recall 高 4pp，但 OOS recall 低约 2pp。99% 档多对 1 条，却需要 40% LLM 调用并把安全 recall 降至 88%。

### 85 条中文/混合合成诊断

| 路径 | 正确数 | OOS recall | 安全 recall | LLM fallback |
|---|---:|---:|---:|---:|
| 纯 Encoder | 75/85 | 86.7% | 92.3% | 0% |
| 95% 档 | 76/85 | 86.7% | 92.3% | 2.4% |
| **97% 档** | **82/85** | **100%** | **100%** | **17.6%** |
| 99% 档 | 85/85 | 100% | 100% | 63.5% |
| 纯 LLM 捕获 | 85/85 | 100% | 100% | 100% |
| 当前 V1 | 84/85 | 100% | 100% | 100% 主调用 |

97% 档剩余 3 个错误都由置信度略高于阈值的 Encoder 直接输出：

- `payment_issue → account_security`，confidence 0.5466；
- `technical → account_security`，confidence 0.5639；
- `technical_login → account_security`，confidence 0.6250。

这说明全局阈值不能独自表达类别风险；如果继续工程化，安全类冲突需要类别级校准或明确的高风险升级策略，而不是根据这三个句子增加特殊分支。

## 为什么不直接上线

1. 上游 test、中文诊断和 LLM 捕获都已被此前实验使用，不是新鲜发布门禁。
2. 中文校准部分来自 LLM 生成与复核，没有人工金标。
3. 97% 档的 OOS recall 仍低于当前 V1。
4. 全局置信阈值没有保证安全类别的选择性风险。
5. `other` 仍混合范围外和信息不足，provider failure 也未进入状态代数。
6. heldout 的 LLM 捕获来自旧 prompt，不能用来证明当前线上级联效果。

因此本轮状态是 `offline_cascade_replay_not_production`。下一阶段应先实现类型化拒识合同和类别风险校准，再用全新中文留存集验证，最后才允许通过显式模式接入生产。

## 产物

- `evaluation/intent_cascade.py`
- `scripts/run_encoder_llm_cascade.py`
- `tests/test_intent_cascade.py`
- `artifacts/eval/encoder-llm-cascade-2026-08-31/report.json`
- `artifacts/eval/encoder-llm-cascade-2026-08-31/development-oof-predictions.jsonl`
