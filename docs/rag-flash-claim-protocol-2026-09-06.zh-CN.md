# Flash 逐项核验协议：开发结果与未完成接线

按照用户明确要求，本轮全部使用Flash，无thinking。保留现有synthesizer/verifier，不增Agent，不修改生产默认模型或发布入口。services/claim_verification.py 是待集成的核验所有者模块，由脚本离线调用。

## 实现

模型返回各claim的原句、三值判决、证据JSON Pointer、原因和缺失证据。服务端验证schema、单个完整tool output、实际叶字段、最终答案文本覆盖、重复/不唯一定位、判决与缺失证据一致性。最终all_supported由程序聚合。assessment绑定问题、最终文本和证据内容hash，matches可检查修改失效。

文本分段由最终答案生成，不依赖synthesizer自报claim。忽略空白和句间分隔符的覆盖差异，数字中的小数点/千分逗号、百分号和正负号仍需覆盖。该检查保证可定位文本受检，不保证模型识别每个隐含命题；并非发布授权。21项测试通过，含三值组合、文本/证据变化、错误位置、缺项、预算、截断与重复输出。

## 同样六条开发变体

| 方案 | 无依据误放行 / 3 | 正确答案未获通过 / 3 | 协议失败 / 6 |
|---|---:|---:|---:|
| Flash原整段核验 | 2 | 0 | 0 |
| Flash逐项v1 | 0 | 1 | 3 |
| Flash逐项v2 | 0 | 1 | 1 |

v1有标点覆盖失败，同时将正确“无法确认到账”误判证据不足，且混淆CONTRADICTED/INSUFFICIENT。不能把协议失败算成正确检测。

v2明确核验完整认识限定命题，区别“不确定事件”与“准确陈述不确定性”；三条无依据权益表述均返回INSUFFICIENT。正确bounded和not_disproved通过，unknown_right返回空tool input，被程序拒绝。未自动重试覆盖原始失败。

协议变化伴随输出预算256→4096，实际调用仍各6次；这不是相同token成本对比，需结合原始usage继续核算。六例已用于改进，不能再作封存验收。三轮共18次API，本轮没有取得生产发布质量改善结论。

## 仍需完成

1. 退款事实owner受控片段，FOUND/NO_APPLICATION及异常语义边界，fact_ref到服务端原文渲染。
2. AnswerVerifier集成逐项结果并迁移消费者；完整性/需求覆盖与支持性分别处理。
3. 最多一次答案修订，最终文本绑定检查接到实际发布调用，证据时效/权限复验保留。
4. 新的分组变异案例与混合入口验收，量化误放行、误拒、协议失败、需求遗漏、修订成本。

目前hash绑定只是模块能力，尚未接入发布，不能宣称杜绝核验后改写。未用具体答案字符串后处理改判。

复现：
MODEL_VERIFIER=deepseek-v4-flash MODEL_VERIFIER_REASONING=none .venv/bin/python -m scripts.run_claim_verification_replay --capture artifacts/eval/rag-entailment6-input-2026-09-06/cases.jsonl --output <新目录>

首次v1协议需在对应历史产物中核对原始request，当前模块为v2并补充数字分隔符覆盖。原始捕获均保留。
