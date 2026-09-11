# 意图识别校准、权重搜索与 V3 合同优化

日期：2026-08-31

## 2026-09-01 Pattern 极性增量验证

生产 Pattern 已从无方向关键词升级为带 span 的 `positive/negative/uncertain/quoted` 证据。对意图 `i` 的常规融合贡献为：

```text
positive:  + pattern_weight × confidence
negative:  - pattern_weight × confidence
uncertain / quoted: 0
```

同一意图同时出现相反证据时Pattern弃权；细粒度纠偏只接受无矛盾的正向证据。否定按局部子句作用，并为“不是我操作的”账户安全事件与“退款没有到账”等含否定词的业务正向表达保留显式合同。

本轮没有重新搜索权重，也没有重新调用LLM。它复用冻结的LLM/n-gram原始输出，仅重算Pattern，隔离比较旧关键词与新极性代数：

| 数据 | 旧 Pattern | 极性 Pattern | 改正/伤害 | OOS recall | Security recall |
|---|---:|---:|---:|---:|---:|
| 202条冲突诊断 | 167/202 | 169/202 | +2 / 0 | 100%→100% | 88.46%→88.46% |
| 500条校准Dev | 466/500 | 466/500 | 0 / 0 | 97%→97% | 90%→90% |
| 300条冻结验证 | 283/300 | 283/300 | 0 / 0 | 96%→96% | 88%→88% |

因此极性修改获得“目标slice改善且既有冻结回归无退化”的证据，但这不是对有符号Pattern重新完成2,332组全局权重搜索，也不把当前权重升级为理论最优。

## 最终结论

生产权重暂时保持：

```text
LLM = 0.70
n-gram = 0.20
pattern = 0.10
threshold = 0.50
```

本轮没有找到能稳定超过当前权重的替代方案。真正得到验证的优化是修复意图标签合同、项目业务范围和污染的 few-shot，而不是修改权重。

## 数据与隔离

### 权重校准集

- 来源：BANKING77 upstream train + CLINC150 `oos_train`
- Dev：500 条
- 每个项目内标签 50 条；`other` 100 条
- 参数搜索只读取 Dev

### 第一次 upstream test

- 来源：BANKING77 test + CLINC150 `oos_test`
- 500 条
- 用于否决第一版权重候选，此后转为已消费回归

### V3 新鲜验证集

- 来源：未被前述数据使用的 BANKING77 test + CLINC150 `oos_test`
- 300 条
- 每个项目内标签 25 条；`other` 100 条
- 在 V3 合同和候选参数冻结后才运行
- 数据 SHA-256：`07e47defe61861aeb41f7c16d154daea6a806dc76912df9380c2ebd1ea59b3a0`

所有外部标签均为 `auto_mapped`，不是 DialogPilot 人工 gold。身份验证相关的 BANKING77 标签因跨越安全/登录边界而被排除。Bitext 未使用，因为其 CDLA-Sharing 条款需要用户显式接受，评测流程没有代用户接受许可。

## 校准与搜索方法

1. 每条样本只采集一次 LLM、n-gram、BGE-M3、pattern 原始输出，并绑定输入哈希与分类器指纹。
2. 使用五折按标签、按 group 分层交叉验证。
3. 每一折只使用另外四折拟合 PAVA isotonic reliability；验证折标签不会进入校准器。
4. 搜索 2,332 个候选：LLM 权重 0.50–1.00、embedding 0–0.40、pattern 0–0.30、阈值 0.30–0.80，并比较 BGE 与 n-gram。
5. 硬门禁要求 `other` recall 和 `account_security` recall 不低于当前 V1。
6. 候选在 Dev 冻结后，才允许在 upstream test 上报告。

## 第一轮权重候选被否决

第一轮 Dev 选择了：

```text
LLM 0.65 + BGE 0.35 + pattern 0.00，threshold 0.30
```

| 策略 | Dev | 第一次 upstream test |
|---|---:|---:|
| 当前 V1 | 418/500 | **427/500** |
| 校准候选 | 419/500 | 425/500 |

候选在 test 减少 2 条正确结果，账户安全 recall 从 82% 降到 80%；paired bootstrap 的准确率差值为 -0.4%，95% CI `[-1.2%, +0.4%]`，McNemar exact p=0.625。该候选没有晋升。

## 发现的共享根因

旧合同只说“本项目范围内返回 query”，却没有告诉模型项目范围是什么。结果是官方 test 中 50 条支持币种、ATM、汇率、卡片受理范围等合法银行问题，只有 3 条被识别为 `query`。

旧 few-shot 还存在跨标签污染：

- `query` 示例包含订单状态、重置密码、物流问题；
- `technical` 示例包含崩溃、登录、500；
- `billing` 示例包含重复扣款、退款、发票。

这些示例与细粒度标签合同互相冲突，权重只能掩盖问题，不能修复分类 authority。

## V3 owner-level 修复

`core/intent_recognizer.py` 现在明确：

- 项目是在线银行与银行卡客服；币种、汇率、ATM、支持国家、Visa/Mastercard 属于项目内 `query`；
- 没有业务指代的“还是没变化”“之前的事”属于信息不足，返回 `other`；
- 丢卡、陌生交易、陌生直接借记、未请求的验证码属于 `account_security`；
- 本人付款失败、被拒、重复扣款、手续费属于 `payment_issue`；
- 卡片本体、虚拟卡、非接触功能故障属于 `technical`；
- 主动请求但收不到或不能使用验证码属于 `technical_login`；
- few-shot 改为与标签 owner 一致的无冲突示例；分类器合同升级为 V3。

## V3 结果

### Dev 500

| 策略 | Accuracy | Macro-F1 | OOS recall | Security recall | Query recall |
|---|---:|---:|---:|---:|---:|
| 当前 V1，V3 合同 | **93.2%** | **0.7648** | 97% | 90% | 100% |
| 最佳校准候选 | 93.0% | 0.7635 | 97% | 90% | 98% |

最佳校准候选退化 1 条，所以没有替换当前权重。它对应 `LLM 0.85 + embedding 0 + pattern 0.15`，进一步说明 BGE/n-gram 在当前融合代数中没有提供稳定增益。

### 新鲜官方 test 300

| 策略 | 正确数 | Accuracy | OOS recall | Security recall | Query recall |
|---|---:|---:|---:|---:|---:|
| 当前 V1 | 283/300 | 94.33% | 96% | 88% | 100% |
| 最佳校准候选 | 283/300 | 94.33% | 96% | 88% | 100% |
| typed V2 | 283/300 | 94.33% | 95% | 88% | 100% |

校准候选与当前 V1 正确性逐条完全打平；没有晋升依据。V2 修复 1 条又损害 1 条，也没有官方 test 净收益。

### 已消费回归

| 回归集 | 旧 V1 | V3 V1 | 旧 V2 | V3 V2 |
|---|---:|---:|---:|---:|
| 独立 LLM 复核中文 100 条 | 96 | 97 | 94 | **98** |
| 原 202 条诊断集 | 167 | 170 | 171 | **172** |

V3 没有在这两套回归中引入净退化。

## 生产决策

- 接受标签合同和 few-shot 的 V3 修复。
- 保持当前 V1 权重 `0.70 / 0.20 / 0.10` 与阈值 `0.50`。
- 不晋升 `0.65 / 0.35 / 0.00`，因为 upstream test 明确退化。
- 不把 `0.85 / 0 / 0.15` 当成新权重，因为 Dev 更差、fresh test 只打平。
- typed V2 保留为候选实现，暂不接管生产路径。

## 证据产物

- 校准选择：`artifacts/eval/intent-weight-calibration-2026-08-31/dev-v3/selection.json`
- 新鲜官方 test：`artifacts/eval/intent-weight-calibration-2026-08-31/fresh-verification-v3/report.json`
- 中文 100 条回归：`artifacts/eval/fresh-intent-v3-regression-2026-08-31/report.json`
- 202 条回归：`artifacts/eval/intent-fusion-mini-v3-regression-2026-08-31/report.json`

对应 SHA-256：

- Dev selection：`9e1c19dfc9a0405633829995b76412f35e2035aec7e082f76e96ee52e67d4a78`
- Fresh official report：`b7e8b6146a987c2b375d4876af87b308afac56cf2b67a4e7ba1a2b616ee89a1b`
- Fresh 100 report：`37837536e3c857a69780d68f9b8d3406dfc6fd557ebd80e52783c2657274c61a`
- Mini 202 report：`23f5d56e1087b4c0861545f48aca515995c437f87c31a594c608e85432d9ca65`

仓库全量测试：`318 passed in 8.33s`。
