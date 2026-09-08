# 领域快速路由：接口收敛与失败实验

日期：2026-09-08。接口实现已完成；本次中英文模型独立验证不通过，不能宣称快速路由已可上线。

## 唯一线上理解链

状态绑定 →（不能直接解决）领域 Encoder →（DEFER）Conversation Agent → Policy / Compiler → 既有执行图。

Encoder 接受时跳过 Conversation Agent 的全局规划，只产生 DELEGATE_TASK。
Domain Agent 继续接收用户原话和原有相关历史，自主选择已有 Tool / Skill，必要时提出写操作申请。
Policy 只开放只读工具和该领域的动作提案；批准、幂等与真正提交仍由原执行边界负责。

Encoder 不填参数、不改写查询、不选择工具、不批准动作、不拆任务图。
同域多个问题可以给一个专家；跨域、歧义交主 Agent。否定需要影响正确选域，
不要求另一个否定极性字段。已有待输入、审批、Workstream 或目标控制状态优先走原协调链。
摘要输入尚未校准时 DEFER；超过 256 token 不截掉末尾用户限制，而是 DEFER。
这两项是当前模型范围限制，不是要求所有未来 Encoder 永远如此。

生产装配只加载 TargetDomainEncoder。旧能力适配器移到 evaluation/legacy_capability_encoder.py，
仅用于历史实验复现，不作为线上 fallback。没有增加新的 LLM 分诊层。

## 数据、模型和固定条件

按原始业务语义审核 Bitext 的 27 个意图与现有中英文种子；不把旧三个动作标签直接改名。
生成组级隔离的数据，关联复合样本的两个父样本必须处于同一个 split。
general 组合规则仅适用于本数据源的礼貌/结束语，不能推广成“所有通用问题都忽略”。

- 中文训练 8,053，校准 1,177，开发 894。
- 英文训练 9,088，校准 1,689，开发 1,261。
- 中英文各一个 BGE-small 分类器，固定 backbone revision，标准 Transformers Trainer，CUDA，seed 17，4 epochs；各拟合一次。
- 领域阈值按校准数据选定；每类校准/开发至少 10 次接受、接受精度至少 98%；共同 margin 0.08。
- 中文 general/human_service、英文 general 缺乏足够开发覆盖，没有启用对应类别。

独立审查者未读种子、训练数据或模型预测，另写 120 条：每语言 60 条、29 条多轮、15 条 DEFER。
与训练/校准/开发完整输入精确重合数为 0。该集合仍为合成挑战集，不是线上用户分布。

## 结果（保留失败）

| 数据 | 中文接受/正确 | 英文接受/正确 |
|---|---:|---:|
| 同源开发 | 456 / 456 | 983 / 982 |
| 独立挑战 | 8 / 4 | 24 / 18 |

独立接受精度分别 50%、75%，覆盖率 13.3%、40%；不达标。
CUDA 单次 Encoder 中位延迟约 1.9 / 4.2 ms，P95 约 2.2 / 5.0 ms，仅此小样本无负载测量。
评测调用真实分类器、级联、Policy、Compiler，但主规划是计数 stub，没有调用真实业务工具。
因此“少调 8/24 次主规划”不是有效业务收益：其中含错误分流，更不是客服成功率。

已确认的错误包括：旧历史压过当前撤销/改域、没有明确指代仍选域、跨域只选一域、工单误分账号。
训练上下文只包含零/一条历史，独立样本包含多轮角色交替。数据结构覆盖及校准分布失配是有证据的主要缺口；
不是接口要求 Encoder 生成过多业务字段，也不是历史漏传。未以逐例规则或测试后阈值调整修补。

## 交付与复现

两个 manifest 已标为 REJECTED，生产加载拒绝；TARGET_ENCODER_ENABLED 默认仍为 false。
离线评测显式 evaluation=True，可复现实验，不构成第二个线上入口。
独立报告记录的是评测时 manifest 哈希；评测后 status 改为 REJECTED，并补记 config/tokenizer 文件摘要，
权重和阈值未改变。恢复原 status 并移除新增 input_files_sha256 后可精确复算独立报告哈希。
开发报告在 REJECTED 状态下生成；移除新增摘要字段即可复算。加载同时验证权重、配置、tokenizer 摘要和固定标签顺序，
防止权重不变而标签映射置换造成错误分流。
训练代码已改为只生成 CANDIDATE（或 REJECTED），不再以同源开发结果自动宣称 ACTIVE。

标准模型文件保存在本机 artifacts/target-domain-encoder-{zh,en}-v1；权重不提交 Git，
Git 保存 tokenizer、配置、训练参数、数据和权重 SHA256。新机器需安装 requirements-semantic.txt，
预先下载 manifest 标明的 backbone revision，再使用新的输出目录训练：

```bash
.venv/bin/python -m evaluation.domain_encoder_training --data data/training/domain-encoder-v1/zh --output artifacts/domain-zh-new --language zh
.venv/bin/python -m evaluation.domain_encoder_training --data data/training/domain-encoder-v1/en --output artifacts/domain-en-new --language en
.venv/bin/python -m evaluation.domain_encoder_evaluation --zh artifacts/target-domain-encoder-zh-v1 --en artifacts/target-domain-encoder-en-v1 --cases data/eval/domain-encoder-independent-2026-09-08.jsonl --output artifacts/eval/domain-new-report.json --device cuda
```

原报告：artifacts/eval/domain-encoder-2026-09-08/{development,independent}.json。
隔离回归：基于1198ed3仅叠加本次提交文件，137 passed / 3 skipped；未依赖用户其他未提交业务修改。
后续应补充与真实会话形态一致的多轮训练/校准来源，另封存新测试集。
不应把已看到的 120 条再次称为 fresh heldout，也不应继续要求 Encoder 承担完整规划来解决分类泛化。
