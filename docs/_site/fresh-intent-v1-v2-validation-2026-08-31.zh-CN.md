# 意图融合 V1/V2：100 条封存验证

日期：2026-08-31

## 结论

在 100 条 `independently_reviewed_synthetic` 固定样本上：

| 策略 | 正确数 | Accuracy | Macro-F1 |
|---|---:|---:|---:|
| LLM 单路 | 94/100 | 94% | 0.841425 |
| 当前 V1（LLM 0.7 + n-gram 0.2 + pattern 0.1） | **96/100** | **96%** | **0.889302** |
| 冻结 V2（LLM 主判，BGE/pattern 有类型地辅助） | 94/100 | 94% | 0.841425 |

V2 没有优于 V1。相对 V1，V2 修复 1 条、损害 3 条，净减少 2 条正确结果。因此当前证据不支持把 V2 直接替换进生产路径。

## 数据等级与封存

- 候选集：`fresh-intent-candidate.jsonl`
- 候选集 SHA-256：`574f9f1b09531dfe02cc15a3a6b099c929982229e0513c6b5b7972fa118f80b3`
- 数量：100；四个 slice 各 25 条
- 复核状态：`independently_reviewed_synthetic`
- 标签由 LLM 生成并由独立 LLM 复核，不是人工 gold
- 策略、权重和阈值在评分前冻结；未在这 100 条上做网格搜索或阈值选择
- 模型只收到 `input.message` 和 `input.history`；`expected`、`secondary_intents`、`review.notes` 未进入推理输入

## 分 slice 结果

| Slice | 当前 V1 | 冻结 V2 |
|---|---:|---:|
| business_boundary | 23/25 | 23/25 |
| rejection | **24/25** | 21/25 |
| conflict | 24/25 | **25/25** |
| semantic_similarity | 25/25 | 25/25 |

4 条 contextual 样本均把 history 送入 LLM，V1/V2 均为 4/4。

## 单路信号

| 信号 | 正确数 | Accuracy | Macro-F1 |
|---|---:|---:|---:|
| LLM | 94/100 | 94% | 0.841425 |
| 字符 n-gram | 19/100 | 19% | 0.130496 |
| BGE-M3 最近原型 | 66/100 | 66% | 0.521041 |
| pattern | 17/100 | 17% | 0.197680 |

BGE-M3 明显强于字符 n-gram，但单路仍不适合充当主分类器。100 条中，LLM 与 pattern 的意图有 86 条不同，n-gram 与 BGE 有 82 条不同；不能把这些异构分数直接解释成同一种概率。

## V1 的 4 个错误

| ID | Gold | V1 | 直接原因 |
|---|---|---|---|
| fresh-intent-007 | account_security | technical_login | LLM 把“无操作却收到认证数字”当作验证码故障，安全/登录边界不明确 |
| fresh-intent-012 | payment_issue | technical | LLM 把结账失败当作一般功能不可用，支付/技术边界不明确 |
| fresh-intent-050 | query | other | LLM 把“支持哪些币种”判成业务范围外 |
| fresh-intent-056 | technical | technical_crash | V1 pattern 对被“没有”否定的“闪退”仍执行细类覆盖 |

## V2 相对 V1

V2 修复：

- `fresh-intent-056`：pattern evidence 将“闪退”标记为 negative，不再把 `technical` 覆盖成 `technical_crash`。

V2 损害：

- `fresh-intent-037`：“还是没变化。”，gold 为 `other`，LLM 以 0.6 输出 `query`。
- `fresh-intent-039`：“我想问一下之前的事。”，gold 为 `other`，LLM 以 0.6 输出 `query`。
- `fresh-intent-040`：“怎么又这样了。”，gold 为 `other`，LLM 以 0.7 输出 `complaint`。

V1 在这三条上正确，是因为异路得分分散后总分低于 0.5，被动落入 `other`；这属于有效结果，但并非清晰的拒识合同。V2 的 `llm_accept_threshold=0.5` 则直接接受了这些泛化标签。

## 根因与优化方向

最主要的共享根因不是某个权重，而是 `query`、`complaint` 与 `other` 的信息充分性合同没有被执行为显式状态：当前 V2 只看 LLM 标签及自报置信度，无法区分“本项目内的宽泛查询”和“没有业务指代、无法分类的残句”。

建议按以下顺序推进，且不要再用本封存集调参：

1. 在意图标签合同 owner 中定义显式拒识条件：消息及上下文必须包含足够的本项目业务指代；否则输出 `other/insufficient_context`，而不是泛化 `query` 或 `complaint`。
2. 将 LLM 输出合同从单一 `intent + confidence` 扩展为有类型结果，例如 `classified / out_of_scope / insufficient_context / provider_failure`；拒识依据必须可检查，不能仅依赖自报概率。
3. 明确两组易混边界并加入成对测试：未主动请求却收到认证码 → `account_security`，请求的验证码收不到/不能用 → `technical_login`；结账或支付授权失败 → `payment_issue`，感应支付或应用功能失效 → `technical`。
4. 保留 V2 的 pattern polarity，因为它已经修复否定词覆盖；同时让引用、否定和肯定证据的作用域进入属性测试。
5. BGE 继续作为低置信 fallback/一致性证据，不直接接管高置信 LLM。其阈值应在新的开发集上校准，再用另一批未见样本验证。
6. 新建开发集实施上述合同；本 100 条只作为已消费的回归证据。最终再准备一批未参与设计的新鲜人工复核样本做关闭验证。

## 可复现实物

- `artifacts/eval/fresh-intent-v1-v2-2026-08-31/cases.jsonl`
- `artifacts/eval/fresh-intent-v1-v2-2026-08-31/source-outputs.jsonl`
- `artifacts/eval/fresh-intent-v1-v2-2026-08-31/report.json`

产物 SHA-256：

- cases：`2e321e1aa5c6f072dab6e66d16b38074ed12b31c4910d71815c870807c416101`
- source outputs：`501616a685690f51fd12bdae1eafb71a39b26b551e1c21fd1d75c93290be4ac8`
- report：`3d876531f53cb00d84d782a37cb7e2b693fd024d15d6a8fa13525de21b890a66`

全量测试：`310 passed in 7.73s`。
