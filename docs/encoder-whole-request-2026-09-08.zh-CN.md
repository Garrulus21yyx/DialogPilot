# Encoder 整条请求覆盖实验

## 为什么这次改变训练目标

旧链路把一个高置信类别编译为一条任务。已有 DEFER 负例，但类别分类仍可能只抓住
复合请求中的一个主题；适配层的 multi_intent=False 并不是训练出的多目标判断。
这不意味着所有错误都已归因为同一个模型问题，也不意味着换一种损失函数一定有效。

本次验证候选条件分类：把有角色的当前对话与能力说明作为文本对，判断该能力能否
**单独完整满足本轮请求**。不是先分类再调用第二个模型审核。每语言一份模型，三个
现有候选一次批量评分；不新增在线 LLM、意图检索、业务关键词或编排层。

复用 [Transformers Trainer](https://huggingface.co/docs/transformers/tasks/sequence_classification)
和标准文本对输入。[Cross-Encoder](https://sbert.net/docs/cross_encoder/usage/usage.html)
是成熟的文本对评分机制；“完整请求覆盖”的监督是本项目的实验假设，不是官方保证，
也不据此宣称 SOTA。文档核对日期：2026-09-08。

## 职责和判定

- EncoderInput：当前用户文本、有序历史和当前目标，训练推理共用。
- 模型：COMPLETE / NOT_COMPLETE 两类评分，不授权工具、不生成订单参数。
- 评分转换：若多个候选各自判 COMPLETE，则全部转 DEFER；全为负也转 DEFER。
  `1-max(score)` 只是边界分数，不能称为已校准 OOD 概率。
- 既有 FastPathPolicy：类别阈值、状态冲突、只读限制、参数来源和最终 ACCEPT。
- 既有 ConversationAgent：处理 DEFER；已有确定性状态续接仍在 Encoder 前。

歧义转换发生在校准与推理共同入口，不是在评测末尾过滤失败。
二分类概率不在不同能力之间重新 softmax；一项高分不强迫其他项成为负类。
能力说明目前只对应三个既有评测目标，不宣称覆盖所有电商业务。

## 数据和固定条件

沿用 semantic-encoder-v2-final 原始三分区，没有新增同义模板，也未回灌以前的挑战。
每条原始样本展开三个文本对，仅完整匹配 gold 的能力为正，DEFER 三项均为负。
这继承了原始标签质量和合成数据局限，并没有自动得到人工标注的多意图结构。

中英文分别用之前固定的 BGE-small 骨干；seed17，4epochs，每语言一次拟合。
沿用同一 Trainer、校准与真实策略评测入口。文本对预算384tokens，超限不截断后接受。
最终校准使用保存后的权重在 CPU/fp32 下计算，GPU 日志不是部署打分依据。

新独立160题（每语言80）由不读取训练数据和预测的审阅者编写，含24条多轮正例和
41条负例/语言；在训练前冻结并验证无精确训练重合。
SHA256：`1500c1737f18bf4e58f6ce8b19a354271821eb7127495694851bd1936406eeef`。
此前三份挑战均作回归，不能继续称为全新独立验证。双语和家族变体有关联，不能
把160条合成记录当作160个独立真实客户样本。

采用条件未放宽：每个范围接受精度≥98%、零 DEFER 误接、多轮正确接受优于旧基线、
总正确接受不下降、预热 CPU Encoder 入口 P95≤100ms。规划器为计数替身，不是
真实 LLM 性能或客服业务 E2E。失败结果保留，不调整阈值后重新报同一新集。

## 验证状态

**候选拒绝采用，生产默认临时关闭状态不变。**

| 同一新160题 | 旧字符模型正确/接受 | 上轮语义分类正确/接受 | 本轮覆盖配对正确/接受 |
|---|---:|---:|---:|
| 中文80题 | 2/3 | 9/10 | 9/10 |
| 英文80题 | 7/9 | 15/16 | 22/25 |

多轮正例正确接受：中文旧字符0/24、上轮语义2/24、本轮5/24；英文分别0/24、8/24、9/24。
本轮开发集中文229/229、英文811/811，但330条回归中文40/41、英文59/61。
开发表现不能代替泛化；覆盖增加伴随错误，未满足98%和零DEFER误接门槛。

具体还会漏掉“退款进度和账户余额”中的余额、“识别商品并查询发货日期”中的发货日期，
误解英文“现在不需要识别了”和带否定的历史确认。公开退款规则/个人状态的旧混淆也仍存在。
这推翻了“仅改为候选条件分类就足以解决”的假设；配对并未消除既有标签和合成样本的
泛化局限。未按这些句子追加分支、样本或重新调阈值；没有把实验代码接入生产。

### GPU 修正：速度不是主要障碍

中英文训练原本就在GPU上，Trainer报告耗时约100s/272s。最初推理门槛沿用旧CPU分类器
部署假设，用户指出当前有GPU后，用同一权重、同一CPU校准阈值补测RTX3080，未重训。

| 预热Encoder入口P95，新160题 | CPU | RTX3080 GPU |
|---|---:|---:|
| 中文 | 102.17ms | 3.56ms |
| 英文 | 144.62ms | 6.57ms |

GPU float32推理包含tokenize、设备传输、候选评分、策略前处理；结果转回CPU后计时结束，
不把异步CUDA提交耗时冒充完成耗时。无模型加载时间、服务排队或并发压力；不是线上SLA。
CPU和GPU在这160题的接受集合、选中目标和错误完全一致。速度达标不能抵消语义失败。
原adoption.json保留最初CPU实验判定；其CPU超时不代表GPU部署超时。GPU回放不是另一份
fresh验证，现有CPU校准阈值也不能因此自动认证为GPU部署产物。

代码检查76通过、4项PostgreSQL/tau2条件缺失跳过，已在排除其他未提交修改的快照复核。
模型全策略对照在共享工作区运行，两臂使用相同应用实现；不据此声称主链全量发布认证。
数据隔离、候选重排序、二分类平局、多候选冲突、超限不调用模型和真实Policy组合均有测试。

## 结果与复现边界

- [原CPU采用判定](../artifacts/eval/encoder-whole-request-2026-09-08/adoption.json)
- [新集逐例](../artifacts/eval/encoder-whole-request-2026-09-08/independent-semantic.json)
- [旧题回归](../artifacts/eval/encoder-whole-request-2026-09-08/regression-semantic.json)
- [同权重GPU回放](../artifacts/eval/encoder-whole-request-2026-09-08/independent-cuda-replay.json)
- [模型描述/阈值/权重hash](../artifacts/eval/encoder-whole-request-2026-09-08/models/en-manifest.json)

完整权重暂存实验机`/tmp/dialogpilot-coverage-{zh,en}-calibrated`，不是长期备份或生产分发；
仓库保留标准模型配置、训练配置、源模型revision、数据hash和所有对照记录。
读取这些权重可通过`SemanticCandidate(path, device="cuda")`显式选择GPU，不静默退回CPU。
后续GPU部署校准使用`--finalize-from ... --device cuda`，在manifest记录calibration_device；
本轮保存的是原CPU校准产物，未在看到新题后重校准。复现本轮原实验则使用`--device cpu`。
本次没有改变线上模型装配。训练/校准共用旧数据，许可和局限沿用上一轮报告。

## 复现入口

```bash
python -m evaluation.semantic_encoder_experiment \
  --data data/training/semantic-encoder-v2-final/zh --language zh \
  --objective whole-request-pair-v1 --output /tmp/coverage-zh-fit
python -m evaluation.semantic_encoder_experiment \
  --data data/training/semantic-encoder-v2-final/zh --language zh \
  --finalize-from /tmp/coverage-zh-fit --output /tmp/coverage-zh-calibrated
# 英文独立重复，使用 en 数据和语言参数；运行环境同上一轮实验报告。
```
