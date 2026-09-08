# 语义 Encoder：实现、失败验收与临时止损

后续候选覆盖实验及GPU延迟实测见[整条请求覆盖报告](encoder-whole-request-2026-09-08.zh-CN.md)。
本文保留上一轮的历史实验结论。

## 当前结论

完成中英文各两次语义分类训练、校准、真实FastPathPolicy对照及独立160题验收。
**语义候选没有通过采用门槛，没有替换生产模型，也没有增加串联推理层。**
旧快速模型在新挑战上同样出现遗漏复合目标，因此代码默认
`TARGET_ENCODER_ENABLED=false`。现有ConversationAgent继续处理，业务工具/状态恢复不变。
这是临时止损，不能计作Encoder多轮闭环完成。显式true配置不被默认值覆盖；未重启服务。

## 改了什么，没改什么

- 用现有Transformers `AutoModelForSequenceClassification`、`Trainer`和标准safetensors。
  中文骨干BAAI/bge-small-zh-v1.5，英文BAAI/bge-small-en，各自固定revision；不是混语训练。
- 训练和预测共同使用有角色、有顺序的整段输入，联合编码history/objectives/current_user。
  输入上限256tokens，超过预算直接DEFER，不截掉限制条件后继续接受。
- 数据补充同答异史、否定、无关历史、完整句追加第二目标及规则/实时业务边界。
  全家族同split；未增加库存、取消、退款等运行时关键词规则。
- 校准复用既有Wilson/阈值实现，语义候选额外要求calibration实测精度>=.98；
  CPU按保存后的fp16权重转fp32重算校准。开发集出现DEFER误接的类别单独禁用。
- 没动主Agent、TaskGraph、审批、RAG或工具执行；语义适配器仅在evaluation中可用。

实现参考：
[Transformers分类文档](https://huggingface.co/docs/transformers/tasks/sequence_classification)、
[MINT电商多轮训练研究（CIKM2025）](https://arxiv.org/abs/2411.14252)。
本轮借鉴上下文训练和对照数据，不宣称复现MINT-CL，不使用SOTA称号。

## 数据、预算和局限

最终训练/校准/开发：zh6814/1270/788；en11101/2408/1827。
来源是已使用的v3数据、Bitext衍生样本与本轮自编对话及组合算子，均按合成数据报告。
Bitext衍生部分继续遵循原[数据许可说明](../data/training/encoder-language-v3/README.md)。
新增24个上下文家族按hash分组，只有1个落入开发分区；不能把大量变体当独立对话覆盖。

第一份120题中有一条常见英文支付问句与既有开发数据完全相同，保留结果并标明它不是完全fresh。
第二份160题由独立作者在不看新训练/预测时编写，逐语言80题，其中24条多轮正例、41条负例；
最终160题与train/calibration/development输入无精确重合。双语及家族变体仍相关，不是160条真实客户Gold。
源文件冻结hash：`d4ac3dabe9580950346f622cfca0fc94ad17aec7d683f8ca180a7622665be736`。
第一份题目转为回归；第二份从未回灌训练，最终结果出来后未调阈值/训练第三次。

四次训练共约116秒（GPU Trainer报告，不含加载/CPU校准评测），每次4epochs、seed17、lr3e-5。
模型API调用0，业务调用0。训练输出和失败记录均保留。

## 最终真实策略结果

评测经过既有EncoderUnderstanding、级联和RoutePolicy，两臂使用相同可信order/asset fixture。
统计的是请求是否完整被正确路由、参数是否合法，不是只看softmax最高类别。
规划模型是计数替身，不冒充真实回复质量/业务E2E；正常部署没有这些诊断重复调用。

| 最终独立集 | 旧字符模型 | 语义候选 |
|---|---:|---:|
| 中文正确接受/接受/总数 | 5/7/80 | 11/11/80 |
| 中文多轮正例正确接受 | 2/24 | 3/24 |
| 英文正确接受/接受/总数 | 7/9/80 | 16/17/80 |
| 英文多轮正例正确接受 | 0/24 | 9/24 |

中文候选虽通过新集，但旧题回归17/18：把“公布的退款到账时间规则”识别成个人退款状态查询。
英文新集16/17：对“Identify it and see whether it is in stock.”只接了商品识别，丢失库存目标。
两种语言均未达到所有验证范围的98%门槛；英文也未满足零DEFER误接，**均拒绝采用**。

62项代码回归通过（含真实PostgreSQL），2项缺tau2跳过；它们验证实现合同，不是模型成功率。

CPU预热后的Encoder入口P95约中文23.33ms、英文32.83ms，包含策略前处理，不是纯模型算子耗时。
不含模型加载；本组输入都在预算内。速度门槛通过不能抵消质量失败，也不能宣称已获得线上收益。

新挑战同时暴露旧模型中文“查询后撤销”、英文“识别后查运费”“换默认支付卡”的误接。
因此不能继续引用上一轮小样本零错误作为全局质量结论，默认快速接受暂时关闭。

## 证据与复现

- [首次对照（失败，含已注明重复问句）](../artifacts/eval/semantic-encoder-2026-09-08/adoption.json)
- [最终采用检查（zh/en均false）](../artifacts/eval/semantic-encoder-final-2026-09-08/adoption.json)
- [最终独立逐例](../artifacts/eval/semantic-encoder-final-2026-09-08/independent-semantic.json)
- [旧题回归逐例](../artifacts/eval/semantic-encoder-final-2026-09-08/regression-semantic.json)
- [训练/校准配置与权重hash](../artifacts/eval/semantic-encoder-final-2026-09-08/models/zh-manifest.json)
- [实施记录](../plans/semantic-encoder-2026-09-08.md)

训练文件、Tokenizer和模型均用标准SDK保存，实验机器最终权重在
`/tmp/dialogpilot-semantic-{zh,en}-v2-calibrated`，未作为生产产物分发。
报告保存权重hash、骨干revision、数据hash、配置及预测；临时目录不可视为长期备份。
完整重训需要已安装的Transformers4.46.3、torch2.13.0、datasets5.0.1、accelerate1.14.0，
骨干快照按`evaluation/semantic_encoder_experiment.py`中的revision下载。

```bash
PYTHONPATH=. python scripts/build_semantic_encoder_data.py \
  --boundaries data/training/semantic-boundary-operators-v1.json --output /tmp/semantic-data
PYTHONPATH=. OMP_NUM_THREADS=2 python -m evaluation.semantic_encoder_experiment \
  --data /tmp/semantic-data/zh --language zh --output /tmp/semantic-zh-fit
PYTHONPATH=. OMP_NUM_THREADS=2 python -m evaluation.semantic_encoder_experiment \
  --data /tmp/semantic-data/zh --language zh --output /tmp/semantic-zh-calibrated \
  --finalize-from /tmp/semantic-zh-fit
# 英文独立重复上述训练和CPU校准。
```

## 解除临时止损的条件

不是调低置信度，也不是为上述句子增加分支。需要用完整请求覆盖的监督与校准，
在独立新对话及旧失败回归上同时满足接受精度、零遗漏额外目标和实际成本要求。
当前单标签+DEFER模型对多目标完整覆盖仍不充分；扩大同义模板不是已证明的解决办法。
下次先审查任务标签/多目标监督是否匹配fast-path职责，再登记新实验；本轮不继续追分。
