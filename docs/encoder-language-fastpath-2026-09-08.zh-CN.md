# 中文/英文独立快速路由：启用与验证

## 交付范围

使用同一上下文Encoder/Policy/TaskGraph链。配置语言选择一个产物，不增加语言检测LLM、
不依次尝试中文和英文、不要求混语验收。

| 部署语言 | 默认产物 | 通过门槛的能力 |
|---|---|---|
| zh | target-encoder-zh-context-v2 | refund_status |
| en | target-encoder-en-context-v2 | knowledge_search、product_identification |

未通过的中文知识/商品、英文退款保持禁用；不是所有类别已完成。低置信、缺少可信参数、
状态冲突或未建模摘要仍进入原ConversationAgent。知识追问需要完整query时也由原主Agent处理。
这次没有恢复tau3不兼容Registry的Encoder开关；英文输入支持与工具能力绑定不同。

`TARGET_ENCODER_ENABLED`默认true；`TARGET_ENCODER_LANGUAGE=zh/en`显式覆盖部署语言，
否则采用装配时response_locale/TARGET_RESPONSE_LOCALE（默认zh-CN）。模型文件缺失或语言不匹配明确报错，
没有自动切回旧产物。代码默认已更新，本轮未重启正在运行的容器或声称部署已完成。

## 数据和采用

中文2075/439/354，英文4456/1121/909（train/calibration/heldout）。英文使用固定版本Bitext
客服instructions与自编多轮/商品样本，中文使用已有数据和自编样本。来源、许可、划分和局限见
[数据说明](../data/training/encoder-language-v3/README.md)。总体是合成数据，不能称真实客服Gold。

保持原门槛：calibration Wilson下界>=.88，各启用类别heldout>=10候选且精度>=.98；
分别训练、分别校准，没有降低阈值。中文知识38/41、商品45/50，英文退款106/117未通过并禁用；
这些失败仍在各manifest。此前混语REJECTED产物保留，不作为默认。

## 完整接受策略，而非仅分类候选

使用真实TargetEncoderUnderstanding、CascadedTargetUnderstanding、RoutePolicy，并提供两臂相同的
order_id/asset_id可信fixture。没有把类别命中直接视为执行成功。

| 数据 | 语言 | 实际接受/总数 | 接受中标签及参数正确 | 主规划调用：关闭→开启 |
|---|---|---:|---:|---:|
| 开发划分 | zh | 73/354 | 73/73 | 354→281 |
| 开发划分 | en | 277/909 | 277/277 | 909→632 |
| 独立挑战 | zh | 2/25 | 2/2 | 25→23 |
| 独立挑战 | en | 5/25 | 5/5 | 25→20 |

上述主规划为计数替身，用于验证调用路径，不模拟真实模型质量或延迟。
独立50题由独立审查者在不看本轮训练/预测的条件下编写，zh/en各25，均包含负例；
冻结后未回灌训练。样本小且为合成，未观察到错误不等于证明真实分布达到98%。
独立多轮正例没有被接受，不能宣传多轮快速路由已广泛泛化；开发中zh35/en4条有历史接受。

## 真实模型对照

独立集每语言选最多3条已接受样本，再取DEFER补足5条，共10条，交替ON/OFF顺序。
这是预先记录选择规则的分层诊断，不是线上请求分布。使用既有SDK/provider的
deepseek-v4-flash、NONE、2048输出预算、0重试；预算上限20，实际15次模型调用。

| 指标 | 关闭Encoder | 开启Encoder |
|---|---:|---:|
| 实际规划模型调用 | 10 | 5 |
| 10条累计规划耗时 | 12.7115s | 5.3205s |
| 输入+输出tokens（SDK报告） | 76,339 | 38,528 |
| 计划校验错误 | 0 | 0 |

中文退款的工具/订单参数两臂一致；英文知识查询表达不同，人工检查保留了原问题范围。
没有运行业务工具、RAG、最终答案和Delivery，所以不是端到端业务成功率。
本地Encoder独立集P95约zh0.30ms/en0.54ms，不含加载模型；启动时一次加载。
不能把分层10题50%模型调用下降或58%耗时下降写成线上总体收益。

## 证据

- [开发完整策略](../artifacts/eval/encoder-language-fastpath-2026-09-08/development.json)
- [独立完整策略](../artifacts/eval/encoder-language-fastpath-2026-09-08/independent.json)
- [真实模型选择与预算](../artifacts/eval/encoder-language-fastpath-2026-09-08/live-pair/manifest.json)
- [真实调用与输出](../artifacts/eval/encoder-language-fastpath-2026-09-08/live-pair/results.jsonl)
- [真实耗时汇总](../artifacts/eval/encoder-language-fastpath-2026-09-08/live-pair/summary.json)

56项相关回归通过（含真实PostgreSQL装配/归因）；2项依赖tau2的测试跳过，不声称已通过tau3。
独立审查和提交状态见[计划](../plans/encoder-language-fastpath-2026-09-08.md)。
