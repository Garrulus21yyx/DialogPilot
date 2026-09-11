# 中文 Encoder 训练增强与复测

## 结论

中文训练增强证明了数据方向有效：兼容标签中文诊断从 71/85 提升到 75/85，OOS recall 从 53.3% 提升到 86.7%。但候选仍明显低于当前 V1 的 84/85，且安全类与登录类出现回退；300 条上游 test 仍为 281/300，没有超过当前 V1 的 283/300。因此不接入生产。

## 数据构建

从公开训练 split 中选择并翻译：

- 8 个业务意图各 50 条；
- 75 条明确域外 `other`；
- 额外生成 25 条无历史时信息不足的 `other`；
- 总计 500 条。

DeepSeek V4 Flash 负责自然中文改写，DeepSeek V4 Pro 独立复核。498 条原候选直接通过，2 条由复核模型修正；最终去重门发现 5 组完全重复，经过二次语义保持改写后通过。现有 `fresh-intent-candidate.jsonl` 被禁止进入训练，并执行了规范化完全重复检查。

数据状态是 `llm_reviewed_synthetic_training_only`，不是人工金标。翻译样本继承公开源数据的标签映射。

## A/B 协议

为隔离“配置变化”和“训练数据变化”，沿用增强前已经选择好的配置：

```json
{
  "architecture": "joint",
  "classifier_c": 10.0
}
```

Encoder `BAAI/bge-m3` 保持冻结。增强前模型使用 500 条英文 dev；增强后使用同一批 500 条英文 dev 加 500 条中文训练增强。上游 test 和中文诊断均不用于重新选择配置。

## 指标

| 数据 | 增强前 | 增强后 | 变化 |
|---|---:|---:|---:|
| 500 heldout | 477/500，95.4% | 475/500，95.0% | -2 |
| 300 上游 test | 281/300，93.7% | 281/300，93.7% | 0 |
| 85 中文/混合合成诊断 | 71/85，83.5% | 75/85，88.2% | +4 |
| 中文诊断 OOS recall | 53.3% | 86.7% | +33.4pp |
| 上游 test OOS recall | 92% | 91% | -1pp |

上游 test 的细分类变化：

- `payment_issue` recall：80% → 88%；
- `technical` recall：92% → 88%；
- `account_security` recall：保持 96%；
- `technical_login` recall：保持 100%；
- ECE：0.1285 → 0.0821。

中文诊断仍有 10 个错误，集中在：

1. 本人支付扣款异常与安全事件；
2. 虚拟卡/新手机/验证码故障与安全事件；
3. 另一家银行、国际包裹等近域 OOS 被吸进 `query`；
4. 登录验证码过期、页面回到入口等隐式登录故障。

增强同时产生两个新回退：一条隐式非本人消费被拒为 `other`，一条隐式登录故障被判为 `payment_issue`。这说明继续按单个失败句子追加分支或样本不能作为闭环证据；需要按意图边界族构建困难负例，并使用全新中文留存集验证。

## 决策

当前候选不接生产，原因是：

- 中文总体仍低于当前 LLM/V1；
- OOS、安全和登录指标没有同时满足无回退条件；
- `other` 仍混合了 `out_of_scope` 与 `insufficient_context`；
- 中文诊断集已被多轮查看，不能再充当新鲜发布门禁；
- 没有人工金标。

后续最小一致性改进应是：以明确合同生成 `payment_issue / technical / account_security / technical_login / near-domain OOS` 成组困难负例，建立独立 typed rejection，再用全新未查看中文集验证。若部署混合架构，还必须先在开发集校准 Encoder 的接受阈值，其余样本才交给 LLM。

## 产物

- `data/training/intent-chinese-augmentation-2026-08-31/cases.jsonl`
- `data/training/intent-chinese-augmentation-2026-08-31/manifest.json`
- `scripts/build_chinese_intent_training_data.py`
- `scripts/run_augmented_local_encoder_experiment.py`
- `artifacts/eval/local-encoder-chinese-augmented-2026-08-31/report.json`
- `artifacts/eval/local-encoder-chinese-augmented-2026-08-31/classifier.joblib`
