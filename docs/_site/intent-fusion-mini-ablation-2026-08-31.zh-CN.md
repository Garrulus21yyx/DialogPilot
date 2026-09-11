# 意图三路融合小规模消融报告（2026-08-31）

> 2026-09-01增量：当前V1 Pattern已接入有符号极性。复用相同LLM/n-gram输出回放后，202条由167/202提升至169/202，修正2条、伤害0条；500条Dev与300条冻结验证预测不变。下文167/202及Pattern纠偏分析保留为改造前基线。

## 结论

当前 `LLM 0.70 + n-gram 0.20 + Pattern 0.10 + threshold 0.50` 没有在这批小规模诊断回归中优于 LLM 主导方案：

| 方案 | 全部正确 | Accuracy | Macro-F1 | Regression Accuracy | Conflict Accuracy |
|---|---:|---:|---:|---:|---:|
| LLM only | 171/202 | 0.8465 | 0.7951 | 0.8056 | 0.68 |
| LLM 0.85 + Pattern 0.15 | 171/202 | 0.8465 | 0.7968 | 0.8056 | 0.68 |
| 当前 n-gram 三路 | 167/202 | 0.8267 | 0.7828 | 0.7778 | 0.60 |
| BGE-M3 替换 n-gram | 168/202 | 0.8317 | 0.7900 | 0.7778 | 0.62 |

粗粒度 Dev 搜索选出 `LLM 0.90 + Pattern 0.10 + threshold 0.40`，不使用相似度信号；但它与多个候选并列，Regression 表现也只与 LLM-only 持平。因此它只能作为下一轮候选，不能据此直接修改生产配置。

真正的根因候选是融合合同，而不只是某个权重：

1. LLM confidence、字符 n-gram cosine、BGE-M3 cosine、Pattern 命中分数不是同一量纲，却被直接线性相加。
2. n-gram 与语义向量没有 `OTHER` 模板或独立拒识边界；它们必定投给某个范围内类别。
3. `threshold=0.5` 作用于未归一化的“获胜类别累计支持度”。信号分歧时，即使 LLM 判断正确，也可能因为乘上 0.7 后低于阈值而变成 `OTHER`。
4. Pattern 细粒度纠偏在本轮只改变 2 条预测，都是“错类变成另一个错类”，没有修正任何样本。

## 数据合同

评测共 202 条，每类约 50 条：

| Slice | 数量 | 来源 | 主要事实 |
|---|---:|---|---|
| `business_boundary` | 50 | BANKING77 映射 | 账户安全、支付、退款、登录等细粒度边界 |
| `rejection` | 50 | CLINC150 OOS 30 + BANKING77 in-scope 20 | `OTHER` 拒识与范围内召回 |
| `conflict` | 50 | 项目 routing contract | 否定、复合领域和主意图冲突 |
| `semantic_similarity` | 52 | 13 个项目语义 family，每组 4 改写 | 词面与语义相似度 |

按 `source_group_id` 稳定划分为 Dev 130 和 Regression 72，同一语义组不会跨 split。

这些标签已经被项目开发过程消费：BANKING77/CLINC150 映射为 `auto_mapped`，routing 标签为 `provisional`。本报告是诊断回归，不是 fresh heldout 或生产准确率证明。

## 实际被测组件

- LLM：当前 `ModelRole.INTENT` 配置（本轮为 `deepseek-v4-flash / reasoning=none`）；202 次调用，失败 0 次。
- n-gram：生产代码的字符 1/2/3-gram、256 维稳定哈希与余弦相似度。
- Semantic：本地缓存的 `BAAI/bge-m3`，使用与生产 n-gram 相同的 `_TEMPLATES` 最近模板分类合同。
- Pattern：生产 `_pattern_recognize()`。
- Fusion：离线逐字复现生产 `_vote()` 的 LLM 失败、权重、细粒度纠偏和阈值顺序。

每条样本只调用一次 LLM，并保存四路 `(intent, confidence, failed, reasoning)`；200 组权重候选全部离线重放，未重复调用模型。

## 单路能力

| 单路 | 全部 Accuracy | Business | Rejection | Conflict | Semantic slice |
|---|---:|---:|---:|---:|---:|
| LLM | 0.8465 | 0.90 | 0.94 | 0.68 | 0.8654 |
| n-gram | 0.2475 | 0.04 | 0.00 | 0.42 | 0.5192 |
| BGE-M3（清理后 V2 prototypes） | 0.6238 | 0.80 | 0.32 | 0.48 | 0.8846 |
| Pattern | 0.5545 | 0.42 | 0.64 | 0.48 | 0.6731 |

BGE-M3 使用独立、去除泛化/细粒度重复真值的 V2 prototypes 后，单路分类明显强于 n-gram：整体由 50/202 提高到 126/202，语义改写 slice 由 27/52 提高到 46/52。但将它以相同的 `0.20` 权重放入当前融合代数，只把最终结果从 167/202 提高到 168/202，仍低于 LLM-only 的 171/202。

这说明“真正 Embedding 更好”和“当前融合能利用它”是两个不同命题。本轮支持前者，不支持后者。

## 冲突机制反例

### 正确 LLM 与正确 n-gram 仍被阈值拒绝

消息：`订单还没发货而且账单多扣了`

| Source | Intent | Confidence |
|---|---|---:|
| LLM | `order_status`（正确） | 0.60 |
| n-gram | `order_status`（正确） | 0.3213 |
| Pattern | `payment_issue` | 0.50 |

当前三路对 `order_status` 的累计支持为：

```text
0.70 × 0.60 + 0.20 × 0.3213 = 0.4843
```

虽然两路都支持正确类别，但 `0.4843 < 0.50`，最终变成 `OTHER`。清理后的 BGE-M3 对复合消息给出 `payment_issue / 0.7895`，而且与第二名 `refund` 的 margin 只有 `0.005`，应由 V2 语义拒识合同拒绝，不能继续作为可直接相加的概率。

### LLM 正确但辅助信号分裂，最终被拒绝

消息：`应用崩溃而且订阅重复扣费`

| Source | Intent | Confidence |
|---|---|---:|
| LLM | `technical_crash`（正确） | 0.70 |
| n-gram | `technical` | 0.2782 |
| BGE-M3 | `technical_crash` | 0.7448（margin 0.0078） |
| Pattern | `payment_issue` | 0.50 |

LLM 对正确类别只贡献 `0.70 × 0.70 = 0.49`。n-gram 版本因此输出 `OTHER`；清理后的 BGE-M3 虽与 LLM 同意，但 top-1/top-2 margin 很小。旧加法碰巧修正这一条，V2 则通过“具体 LLM 主权威”直接保留正确意图，不依赖不稳定的余弦幅度。

这不是增加某个模板即可闭合的问题；融合器把“来源权重”和“来源自报 confidence”相乘后，又拿未归一化累计值与固定阈值比较，导致拒识语义随信号数量和分歧方式变化。

## Pattern 纠偏

当前 n-gram 三路开启/关闭 `refined_by_pattern` 均为 167/202；semantic 三路均为 168/202。

纠偏只改变两条：

- `不涉及验证码，请处理账单`：`billing → technical_login`，期望 `payment_issue`；
- `查询物流并解释这笔扣费`：`query/OTHER → logistics`，期望主意图 `order_status`。

两条都没有变成正确答案。当前小样本没有提供“Pattern 强制细化产生净收益”的正证据。

## Confidence 诊断

| 方案 | Mean confidence | ECE-10 | Correctness Brier |
|---|---:|---:|---:|
| LLM only | 0.9206 | 0.0800 | 0.1191 |
| LLM + Pattern | 0.8144 | 0.1156 | 0.1134 |
| 当前 n-gram 三路 | 0.6872 | 0.2358 | 0.1380 |
| BGE-M3 三路 | 0.7724 | 0.1278 | 0.1177 |

这些 confidence 不是已校准概率；ECE/Brier 只作为“confidence 与本轮正确率是否一致”的诊断。当前三路的 ECE 高于 LLM-only，进一步说明不能把融合输出直接当概率解释。

## Fusion V2 双跑（步骤 1～6）

已实现：

1. LLM Few-shot 与 semantic prototypes 分离；54 条 semantic prototypes 经唯一 Owner 合同校验，`OTHER` 不再伪造成原型。
2. `BGESemanticIntentProvider` 显式拥有 BGE encoder、原型缓存、阈值、margin 和 provider failure。
3. Semantic evidence 同时记录 top-1、top-2、score、margin、threshold 和 accepted。
4. Pattern evidence 记录关键词、span、offset 与 `positive/negative/quoted` 极性；本轮捕获 266 条证据，其中 positive 231、negative 26、quoted 9。
5. `FusionPolicyV2` 保留 accepted specific LLM；只有 semantic 与 positive Pattern 同意且满足合法 parent-child 投影时，才允许泛化类细化。
6. 在相同 202 条、相同 LLM 原始输出上完成 V1/V2 双跑。

| 方案 | 全部 | Dev | Regression | Conflict |
|---|---:|---:|---:|---:|
| 当前 V1 n-gram | 167/202 | 0.8538 | 0.7778 | 0.60 |
| V2 default (`semantic>=0.72`, `margin>=0.05`) | **171/202** | **0.8692** | **0.8056** | **0.68** |
| LLM-only reference | 171/202 | 0.8692 | 0.8056 | 0.68 |

V2 相对当前 V1 改变 8 条预测，修正 4 条、伤害 0 条；四条修正全部来自“accepted specific LLM 不再被辅助信号稀释成 OTHER”：

- `订单还没发货而且账单多扣了` → `order_status`；
- `应用崩溃而且订阅重复扣费` → `technical_crash`；
- 两条登录与重复扣款复合消息 → `technical_login`。

本轮 V2 的 167 个范围内分类全部由 accepted LLM 决定，35 个高置信 `OTHER` 成为 typed `out_of_scope`；没有样本触发 semantic fallback 或 parent-child refinement。因此 25 组 semantic threshold/margin 候选产生相同最终预测，Dev 选择出的 `0.80/0.10` 只是并列排序结果，不是阈值已被校准的证据。Semantic Provider 的 fallback/refinement 合同目前由单元反例验证，仍需第 7 步的新鲜样本覆盖真实调用分支。

## 当前建议

1. 不把 `0.7/0.2/0.1` 描述为已验证权重；本轮证据显示它低于 LLM 主导方案。
2. 暂不直接把生产配置改为 Dev 搜索赢家；Regression 没有证明其优于 LLM-only，且标签不是 fresh Gold。
3. V2 已建立 top-1 threshold + margin、Pattern 极性和 typed decision；下一步用独立复核的新鲜样本覆盖 semantic fallback、合法细化、否定与引用分支。
4. 在新鲜验证前保持生产默认 V1；V2 当前只是实现完成和 consumed regression 通过，不是 verified closure。
5. 下游迁移时让 `OUT_OF_SCOPE/AMBIGUOUS/PROVIDER_FAILURE` 成为显式处置事实，不再只消费一个含义混杂的 `OTHER + confidence`。

## 复现

```bash
# 1. 构建 202 条小规模诊断集并捕获 LLM/ngram/pattern；中断后会复用已有输出
PYTHONPATH=/home/yang/DialogPilot \
  .venv/bin/python scripts/run_intent_fusion_ablation.py --skip-semantic

# 2. 在已安装 SentenceTransformers 的兼容环境中添加真实语义向量输出
python scripts/capture_semantic_intent_source.py \
  --cases artifacts/eval/intent-fusion-mini-2026-08-31/cases.jsonl \
  --templates artifacts/eval/intent-fusion-mini-2026-08-31/templates.json \
  --outputs artifacts/eval/intent-fusion-mini-2026-08-31/source-outputs.jsonl \
  --model BAAI/bge-m3 --batch-size 16

# 3. 离线重放固定方案和权重搜索
PYTHONPATH=/home/yang/DialogPilot \
  .venv/bin/python scripts/run_intent_fusion_ablation.py \
  --semantic-model BAAI/bge-m3

# 4. 合同测试
PYTHONPATH=/home/yang/DialogPilot \
  .venv/bin/python -m pytest \
  tests/test_intent_fusion_ablation.py tests/test_explicit_modes.py -q
```

机器可读证据位于 `artifacts/eval/intent-fusion-mini-2026-08-31/`：

- `cases.jsonl`：切片、来源、审核状态和期望标签；
- `templates.json`：本轮两种相似度共同使用的模板；
- `source-outputs.jsonl`：四路原始输出；
- `report.json`：固定方案、单路指标、200 个候选的 Dev 选择摘要与逐条预测。
