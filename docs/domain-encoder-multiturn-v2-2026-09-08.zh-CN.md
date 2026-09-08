# 多轮数据补充 v2：已交付，模型启用门禁未通过

2026-09-08。数据代码提交 eda53e8。只改变训练数据和评测分组，未添加线上路由规则、极性输出或动作参数。

## 补充内容与审核

中英文分别新增训练3,483、校准302、开发302条。
历史从仅0/1条扩展到2/4/6条角色交替，分别包括原问题续接、追问补充条件、撤回旧业务后提出新业务。
覆盖保留、替换、撤销、同域追加、跨域追加、指代未定；同一句短答随历史产生不同领域标签。

这些是合成模板变体：每语言24个训练场景、12个校准场景、12个开发场景。
主要场景与当前答复表达不跨split；FOLLOWUPS领域内追问模板仍共用，不能把数千变体说成数千独立真实对话。
初稿被审查指出general实质任务错误中和、无待办结束语硬套续接，两项已在数据owner修正后再训练。
没有通过生产关键词规则补偿错误数据。

旧v1数据行原样保留；输入、case_id、group和manifest经过检查，新独立集与训练/校准/开发精确输入重合0。
独立审查者在未读取种子、训练数据、旧测试或预测时编写120条新样本（每语言60，38条多轮、15条DEFER）。
旧120条只作已消费回归，新120条在模型/阈值冻结后仅评测一次，均未回流训练。

## 固定实验与结果

保持BGE-small、原backbone revision、seed17、4epochs、256token、原校准门禁，中英文各一次拟合。
训练最长输入中文209、英文161token，无截断。训练用时约47/81秒，不含数据读取和校准。
没有按测试结果改阈值、重训或选取最好一轮。

| 相同旧120条回归 | 中文接受/正确 | 英文接受/正确 |
|---|---:|---:|
| v1 | 8 / 4 | 24 / 18 |
| v2 | 22 / 17 | 23 / 19 |

同集结果有变化，但并非所有错误减少：中文错误接受由4变5，英文由6变4。
正确接受和错误接受必须同时报告，不能只说正确率提升。

| 新独立集 | 接受/正确 | 接受精度 | 覆盖率 |
|---|---:|---:|---:|
| 中文60条 | 20 / 16 | 80.0% | 33.3% |
| 英文60条 | 17 / 14 | 82.4% | 28.3% |

预注册采用条件为每语言至少20次接受且接受精度>=98%，两者不通过。
模板开发集则为中文524接受522正确、英文964接受958正确；这不能覆盖独立失败。
开发分关系结果仍暴露撤销误接受，不能把总分接近100%说成所有对话关系可靠。

独立审查确认7个错例金标符合事前领域合同；错误不是因要求参数或工具选择而判失败。
剩余覆盖包括自然澄清中的否定作用域、完成历史之后的新问候、同轮/历史双域并存。
单纯增加角色数与模板笛卡尔积不足以证明这些结构泛化；仍应基于自由多轮会话族补数据及校准，
不是继续把Encoder扩成完整规划器，也不是为每个错例写分支。

## 交付边界

- 两个v2 artifact均REJECTED，生产加载拒绝；没有修改生产开关或默认模型路径。
- 权重保留本机，不进Git；manifest包含权重和预处理摘要，配置/tokenizer/训练参数与数据入Git。
- 三份报告记录评测时CANDIDATE manifest哈希；评测后唯一变化为status→REJECTED。
- 当前接口仍是选领域→既有专家，复杂理解走Conversation Agent，不存在新增线上路径。
- 隔离回归：在eda53e8归档中运行，176 passed / 3 skipped，没有混入用户其他未提交业务修改。
- 评测执行真实Encoder/cascade/Policy/Compiler，主规划为计数stub；未运行真实业务工具或τ³，不能报告客服完成率。

证据目录：artifacts/eval/domain-encoder-v2-2026-09-08/，含development、regression、independent三个原报告。
新独立集SHA256：f39b7da9b2923e9d73b4f50177e6c01b88b60ac90469c301a2309e6b57b06182。

离线复现使用已保存权重或按原训练命令在新目录重新训练；新报告输出路径不得覆盖原报告：

```bash
.venv/bin/python -m evaluation.domain_encoder_training --data data/training/domain-encoder-v2/zh --output artifacts/domain-zh-v2-rebuild --language zh
.venv/bin/python -m evaluation.domain_encoder_training --data data/training/domain-encoder-v2/en --output artifacts/domain-en-v2-rebuild --language en
.venv/bin/python -m evaluation.domain_encoder_evaluation --zh artifacts/target-domain-encoder-zh-v2 --en artifacts/target-domain-encoder-en-v2 --cases data/eval/domain-encoder-independent-v2-2026-09-08.jsonl --output artifacts/eval/domain-v2-new-report.json --device cuda
```
