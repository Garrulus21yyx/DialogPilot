---
layout: default
title: DialogPilot 客服 Agent 数据集与多模态 RAG 评测方案
permalink: /customer-service-agent-evaluation-datasets.html
---

# DialogPilot 客服 Agent 数据集与多模态 RAG 评测方案

> 状态：两条成绩线 / 五阶段执行的数据集参考
>
> 版本：v1.0
>
> 日期：2026-09-02
>
> 配套文档：[评测收敛执行手册](./customer-service-agent-evaluation-plan.zh-CN.md) · [总体目标架构](./customer-service-agent-target-architecture.zh-CN.md) · [颗粒度实施计划](./customer-service-agent-implementation-plan.zh-CN.md)

## 1. 执行摘要

DialogPilot 不使用一个数据集证明全部能力，也不把 Intent、RAG、Agent、Memory 和多模态压成一个总准确率。最终只有两条成绩线：

1. **公开能力线**：用公开数据和官方 evaluator 产生可横向比较的 Intent、Knowledge、Memory、多模态与 Agent 指标。
2. **项目合同线**：用 80 条冻结的模型生成合成合同验证 DialogPilot 特有的 Owner、状态转换、权威来源、连续图片复用、Handoff 和副作用约束。

最终采用的最小组合如下：

| 能力面 | 主数据集 | 主要证明 |
|---|---|---|
| Intent / OOS | MASSIVE zh-CN、BANKING77、CLINC150 | Macro-F1、OOS/安全 Recall、LLM 调用率 |
| 客服 Agent E2E | τ³ v1.0.1：airline/retail/telecom test + banking_knowledge | 多轮服务完成、RAG + Tool、用户与 Agent 双向协作 |
| 企业知识 RAG | WixQA ExpertWritten + Simulated | 企业文档检索、多文档证据和 Grounded answer |
| 连续对话 RAG | MTRAG | 指代、追问、澄清、不可回答和增量检索 |
| Memory RAG | LoCoMo Dev + LongMemEval frozen test | 历史证据召回、更新/时间准确率和上下文压缩 |
| 文档解析 | OmniDocBench | OCR、布局、阅读顺序、表格和公式解析 |
| 视觉文档检索 | ViDoRe v3 | 页面级视觉检索、Hybrid retrieval 和 Rerank |
| 产品手册多模态问答 | PM209/MPMQA | 手册页面、视觉区域证据和端到端回答 |
| 架构合同 | DialogPilot 80 条合成合同 | 服务连续性、Memory、Handoff、图片安装、业务工具和安全边界 |

关键边界：

- τ³ 必须使用，但它是外部客服 Agent 基准，不是 DialogPilot 的总验收。
- τ³ 中的多模态主要指语音，不包含商品图、破损照片、截图 OCR 或视觉 RAG。
- 多模态 RAG 必须拆成 Media 路由、解析、检索、Oracle-evidence 生成、Retrieved-evidence E2E 和客服业务结果六层。
- 公开数据集使用官方 split 和 evaluator；DialogPilot 只实现薄适配器，不重建 benchmark runtime。
- 80 条数据是 `SYNTHETIC_CONTRACT_LOCKED`，不是人工 Gold；静态锁定与真实运行通过是两个状态。
- 所有 E2E 仍以真实 `ChatApplication.handle()`、权威业务终态和 receipt 为准。

## 2. 评测边界与 Owner

### 2.1 评测入口

所有端到端评测都必须调用生产同源入口：

```text
Benchmark Case
→ Benchmark Adapter
→ ChatApplication.handle()
→ Router / Planner / Agent / KnowledgeRetriever / Tools / Media
→ Publication Candidate
→ Authoritative State & Receipt Grader
```

Benchmark Adapter 只负责：

- 将官方消息和环境状态转换为 DialogPilot 输入；
- 将 benchmark tools 暴露为符合项目 Tool Contract 的测试工具；
- 将 DialogPilot 输出转换回官方 evaluator 格式；
- 绑定 task、trace、invocation、response 和环境终态 ID。

Adapter 不拥有：

- 路由、任务规划、RAG 或工具选择；
- 业务事实和工具副作用；
- 官方 task reward；
- 自定义一套只为通过 benchmark 的 Agent 主链。

### 2.2 三类分数不能混成一个总分

| 分数类型 | Owner | 示例 |
|---|---|---|
| 官方 benchmark 分数 | 官方 evaluator | τ³ task reward、ViDoRe nDCG@5、OmniDocBench TEDS |
| DialogPilot 确定性分数 | Evaluation + 领域 Owner | required-authority recall、receipt validity、duplicate effect |
| 语义评分 | 官方人工标注 / Versioned Judge | 长回答完整性、表达质量、复杂视觉判断；不新建内部人工 Gold |

模型 Judge 不能覆盖确定性失败。例如，退款工具产生了错误业务终态，即使回答文本语气很好，也必须判任务失败。

### 2.3 DatasetLockManifest

除 τ³ 已固定为 `v1.0.1` 外，其余公开数据集在第一次正式运行前必须生成不可变 `DatasetLockManifest`。未完成锁定时只允许本地探索，不得生成 RC、对外报告或简历数字。

```yaml
dataset_id:
upstream_uri:
release_or_commit:
data_archive_sha256:
evaluator_release_or_commit:
config_or_task_ids:
split:
case_ids_sha256:
license_snapshot:
adapter_version:
```

正式锁定规则：

| 数据集 | 正式 corpus / split | 锁定要求 |
|---|---|---|
| τ³ | `v1.0.1` 官方 base tasks | release、commit、domain、task IDs、simulator 全部固定 |
| WixQA | `ExpertWritten + Simulated` 官方测试数据 | Hugging Face dataset revision、config、row IDs 和数据 checksum |
| MTRAG | `mtrag-dialogpilot-v1` conversation-group Dev/heldout；不是上游官方 split | 仓库/数据 revision、split protocol、全部 conversation/task IDs、role 与 corpus checksum |
| OmniDocBench | v1.7 数据及固定 evaluator commit | 数据包 checksum、annotation version、evaluator commit、页面 IDs |
| ViDoRe v3 | 官方 pipeline protocol 的 v3 English splits；单模型另走 MTEB `ViDoRe (v3)` | framework/MTEB version、全部实际 task IDs、language、corpus/query/qrels checksum；未获官方 private-set 运行不得声称完整 v3 leaderboard |
| PM209/MPMQA | 官方 test split | 仓库 revision、数据包 checksum、manual/query IDs 和 evaluator version |
| DialogPilot 合成合同 | `dialogpilot-synthetic-contract-v1` | schema、80 case、fixture/media、case IDs 与文件 checksum；`promotion_allowed=false` |

开发阶段允许使用小子集，但必须另建带全部 case IDs 的 `dev_subset` manifest，不能与正式 full-suite 结果混写。上游数据或 evaluator revision 发生变化时创建新 manifest 和新结果序列，禁止覆盖旧报告。

### 2.4 2026 上游现状与本轮边界

这轮不靠增加数据集数量制造“更 SOTA”的外观，但会显式核对上游已经变化的评测合同：

- [MTRAG 官方仓库](https://github.com/IBM/mt-rag-benchmark)现已同时发布 MTRAG-UN；本轮仍在原 MTRAG human data 上先锁定 conversation-group Dev/heldout，Dev 做 Query/Recall 选择、heldout 做冻结复核，并用 WixQA ExpertWritten + Simulated 承担完整官方企业知识外测。MTRAG-UN 不加入首轮成绩单，避免临时扩大范围。
- [LongMemEval](https://github.com/xiaowu0162/LongMemEval)已有 cleaned 数据更新，且上游另发布了 [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2)。本轮锁定 cleaned v1 的明确 revision 来测试对话事实、更新、时间和拒答；V2 面向 agentic experience/runbook memory，不与当前 ServiceEpisode 检索分数混写。
- [OmniDocBench](https://github.com/opendatalab/OmniDocBench)当前已更新到 v1.7；首次正式运行固定 v1.7 数据与 evaluator commit，不跟随浮动 `main`。
- [ViDoRe](https://github.com/illuin-tech/vidore-benchmark)已把复杂 pipeline 评测留在官方 framework，并把单模型评测迁到 MTEB。正式报告按实际协议标注 English/public task IDs；private-set leaderboard 未运行时不得写“完整 v3”。

这些比较只收紧版本与适用范围，不新增第五条成绩线或新评测平台。

## 3. τ³-bench：外部客服 Agent 主基准

### 3.1 是否采用

采用，并固定 [`sierra-research/tau2-bench v1.0.1`](https://github.com/sierra-research/tau2-bench/releases/tag/v1.0.1)。官方在 v1.0.1 修复了 `banking_knowledge` 的任务和评分错误，因此 `<1.0.1` 与 `>=1.0.1` 的结果不得直接比较。

`τ³-bench` 是当前官方总发布名称，但 Python package 和 CLI 仍使用 `tau2`。DialogPilot 在 manifest 中必须同时记录：

```text
benchmark_name = tau3-bench
repository = sierra-research/tau2-bench
release = v1.0.1
commit_sha
domain
task_ids
trial_count
user_simulator_model
agent_model
```

### 3.2 采用的领域

| 领域 | 是否首轮采用 | 对 DialogPilot 的意义 |
|---|---|---|
| `retail` | 是 | 订单、退换货、政策约束和工具调用，最接近电商客服 |
| `telecom` | 是 | 用户需要按步骤操作，适合验证持续安装/故障排查式双向协作 |
| `banking_knowledge` | 是 | 约 700 份政策文档，要求知识检索、业务工具和政策推理结合；对应 [τ-Knowledge](https://arxiv.org/abs/2603.04370) |
| `airline` | 是 | 补足官方 base test 的跨领域泛化；它的 20 题计入最终 197 题 |
| `voice` | 暂不采用 | 只有语音客服进入产品范围后才启用；不能作为图片多模态证明 |

### 3.3 运行层级

```text
PR smoke
  20 tasks × 1 trial

开发回归
  固定小集 × 1 trial

冻结配置的成本受限结果
  airline test 20 + retail test 40 + telecom test 40
  + banking_knowledge 97
  = 197 tasks × 1 trial

最终稳定性结果（只有它可以发布 pass^4）
  同一 197 tasks × 4 trials = 788 conversations
```

开发阶段只在 dev/smoke 上调整 Prompt、路由、检索和工具描述。正式 task IDs、模型、检索 generation 和 policy fingerprint 冻结后才运行 RC 或最终评测。

`197×1` 的发布等级是 `RC_PASS1`，可以只报告 `pass^1`；`197×4` 的发布等级是 `FINAL_STABILITY`。788 次不阻塞其他工程验收，但任何简历或成绩单里的 `pass^4`、`safe_pass^4` 或“连续四次稳定完成”都必须来自后者。

### 3.4 接入方式

直接使用[官方仓库](https://github.com/sierra-research/tau2-bench)的 programmatic API，实现一个薄适配器：

```python
class DialogPilotTauAgent:
    async def respond(self, benchmark_message, benchmark_state):
        request = tau_to_chat_request(benchmark_message, benchmark_state)
        outcome = await chat_application.handle(request)
        return chat_outcome_to_tau_message(outcome)
```

约束：

- 不使用额外 sidecar；
- 不复制或修改官方 evaluator；
- `banking_knowledge` 的正式结果必须使用 DialogPilot `KnowledgeRetriever`；
- 官方 `golden retrieval`、BM25、Dense 等配置只用于检索上限和消融，不作为主系统结果；
- 每个 task 运行前重置 benchmark 环境，运行后读取权威环境终态。

### 3.5 报告指标

主报告保留官方指标：

- binary task reward；
- `pass^1`；
- `pass^4`（仅当每题 `num_trials >= 4`）。

同时从 DialogPilot Trace 追加：

- required-authority recall；
- forbidden-tool / forbidden-effect rate；
- 工具参数正确率；
- duplicate effect；
- `OUTCOME_UNKNOWN` 对账完成率；
- 每成功任务的 Retriever、Tool、LLM 和 Worker 调用数；
- P50/P95 延迟；
- Token、模型费用和每成功任务成本。

τ³ 不证明以下能力：

- 跨会话 Memory；
- 未完成工单或服务债务；
- checkpoint 崩溃恢复；
- HandoffContract；
- 商品图片、破损照片或视觉 RAG。

这些能力必须由 DialogPilot 80 条合成合同和故障注入验证；这条项目合同线不冒充自然分布能力分数。

## 4. Intent、Knowledge 与 Memory 数据集

### 4.1 Intent / OOS

使用 MASSIVE zh-CN、BANKING77 与 CLINC150 比较当前三路融合、Typed Fusion V2 和 `BGE-M3 + LogisticRegression → 低置信 LLM`。三者的标签必须先映射到同一个版本化 DialogPilot ontology；各数据集分别报告 Macro-F1 和 slice，不把不同原生标签空间直接拼成一个总准确率。

历史 Intent artifacts 中已经出现过的 BANKING77/CLINC150 row IDs 全部进入 consumed regression 清单。它们可防回归，不能再次承担新阈值的正式 test。正式结果使用冻结后未消费的官方 IDs，并明确 subset manifest；MASSIVE zh-CN 同样使用官方 split 和版本锁。

主要指标：

- Macro-F1 与逐类 Recall；
- OOS、`account_security` 和高风险意图 Recall；
- ECE/Brier 或版本化概率校准误差；
- LLM fallback/call rate；
- P50/P95 与模型内存。

配置选择先应用 OOS/安全非劣硬门禁，再最大化 Macro-F1；统计打平时才最小化 LLM 调用率和 P95。CrossEncoder 不用于 Intent。

### 4.2 WixQA：企业知识库 RAG

[WixQA](https://arxiv.org/abs/2505.08643)用于验证企业网站/知识库问答。正式结果只使用：

- `ExpertWritten`：真实用户问题和专家答案；
- `Simulated`：经过专家验证的对话式问题。

大规模 `Synthetic` 部分只用于：

- 索引和吞吐压力；
- Query rewrite 开发；
- 负样本与 hard-negative 构造。

Synthetic 不得作为简历 headline test。

主要指标：

- Article Recall@K；
- MRR、nDCG@K；
- gold-article coverage；
- All-article Recall（全部 `article_ids` 是否都被召回）；
- claim-level citation precision/recall；
- unsupported claim rate；
- 无证据时的正确拒答率。

### 4.3 MTRAG：连续对话检索

[MTRAG](https://github.com/IBM/mt-rag-benchmark)用于验证：

- 后续轮指代和省略；
- `CONTINUE / EXPAND / SWITCH / AMBIGUOUS`；
- 需要澄清、部分可回答和不可回答；
- 是否复用已有 EvidencePack；
- 是否只为新增 requirement 增量检索。

除了官方答案与检索指标，还要记录：

- full-router avoided rate；
- duplicate retrieval rate；
- stale-evidence reuse；
- later-turn Recall@K；
- follow-up P50/P95；
- 每个后续轮新增模型和检索成本。

上游 110 个 human conversations / 842 tasks 没有可直接借用的官方 Dev/Test split。本轮在任何新调参前生成 `mtrag-dialogpilot-v1`：以完整 conversation 为不可拆 group，在每个 domain 内按 `SHA256("mtrag-dialogpilot-v1\0" + conversation_id)` 升序排列，前 `floor(0.70 × N_domain)` 个进入 Dev，其余进入 heldout；所有 turns/tasks 继承 conversation 角色，禁止事后换组。四个 domain 都必须出现在两侧，精确 IDs、角色与 SHA-256 写入 manifest。所有 Query、K、CrossEncoder/Parent 触发判断只读 Dev；heldout 只在 Bundle 冻结后运行，并明确标为 DialogPilot group split。Manifest 未锁定前状态为 `BLOCKED_DATASET_NOT_LOCKED`。

### 4.4 Memory RAG：LoCoMo 与 LongMemEval

LoCoMo 只用于按完整 conversation/group 切分的开发选择；配置冻结后用 LongMemEval test 报告。Knowledge 与 Memory 可以共享 PostgreSQL 检索基础设施和 capture/replay 实现，但数据、权重、freshness 与阈值必须独立。

主要指标：

- Evidence Recall@5、All-evidence Recall 和 MRR；
- 事实更新、时间顺序与过期事实淘汰准确率；
- wrong-user / stale-memory evidence；
- Context Token、P50/P95 与生成准确率。

当前 `.30/.60/.10,k=60` 只是未调 baseline；ServiceEpisode dense projection 未写入真实 embedding 前，不运行权重搜索。

### 4.5 补充与回归边界

| 数据集 | 用途 | 不允许的表述 |
|---|---|---|
| CLINC150 | Intent/OOS 公开能力；历史已使用 IDs 只作回归 | Agent E2E 成功率 |
| Banking77 | Intent 公开能力；历史已使用 IDs 只作回归 | RAG 或工具能力 |
| MultiWOZ | DST、slot/entity correction、跨域切换辅助 | 现代客服 Agent 业务终态 |
| Doc2Dial | 保留现有检索回归 | 完整多文档、工具或服务连续性证明 |

如果已经采用 MTRAG，不再为了数字数量同时把 Doc2Dial、MultiDoc2Dial 和 MultiWOZ 都放入主报告。

## 5. 多模态 RAG 数据集组合

### 5.1 OmniDocBench：摄取与解析门禁

[OmniDocBench](https://github.com/opendatalab/OmniDocBench)只验证文档入库前的解析质量：

- OCR 文本；
- 页面布局；
- 阅读顺序；
- 表格；
- 公式。

主要指标：

- normalized edit distance；
- reading-order edit distance；
- Table TEDS；
- Layout mAP/mAR；
- 公式指标仅在产品文档确实需要时启用。

报告必须按扫描件、双栏、旋转页面、复杂表格和语言分桶。OmniDocBench 分数不能冒充 RAG 或回答正确率。

### 5.2 ViDoRe v3：视觉页面检索

[ViDoRe benchmark](https://github.com/illuin-tech/vidore-benchmark)用于比较：

```text
OCR text only
vs page image only
vs text + image hybrid
vs hybrid + rerank
```

开发阶段可以使用 manifest 中明确列出的 industrial、finance、HR 小子集。复杂 pipeline 的正式 headline 按官方 framework 运行 v3 English splits；单模型对照另按 MTEB `ViDoRe (v3)` 协议。两者不得混表，private sets 未经官方运行时也不得声称“完整 v3”。所有方案固定相同 corpus、filter、Top-K、generation 与 query set。

主要指标：

- nDCG@5；
- Recall@1/5；
- MRR；
- P50/P95 检索延迟；
- 索引时间、大小和重建时间。

### 5.3 PM209/MPMQA：产品手册端到端问答

[PM209/MPMQA](https://github.com/AIM3-RUC/MPMQA)包含消费电子产品手册、人工问题、文本答案和相关视觉区域，与产品咨询、安装和维修问答最贴合。

每个问题必须运行两种模式：

```text
Oracle Evidence
  直接提供正确页面/区域
  → 测试 VLM 在正确证据下的回答上限

Retrieved Evidence
  DialogPilot 检索页面/区域
  → VLM 基于实际召回答案
  → 测试完整多模态 RAG
```

两者的差距用于区分检索错误和生成错误。

主要指标：

- Page Recall@1/5；
- MRR、nDCG@5；
- region/bbox Evidence Precision、Recall、F1；
- answer EM/token-F1 或官方答案指标；
- claim-level grounding；
- retrieved-evidence E2E success。

### 5.4 可选专项集

- [ChartQA](https://github.com/vis-nlp/ChartQA)：只测已给定页面后的图表推理，不作为完整检索证明。
- MMDocRAG / REAL-MM-RAG：在首轮核心套件稳定后，用于多页、跨模态、相似页面 hard-negative 挑战。
- ABO：只作 image-to-SKU 商品识别诊断，使用前单独核验许可证。
- MVTec LOCO / MMAD：只作缺陷感知辅助，不能替代真实客服图片和业务判定。

## 6. 多模态 RAG 的六层评测

多模态系统必须逐层评分，不能只报告最终答案。

| 层级 | 输入与输出 | 主指标 | 主要数据 |
|---|---|---|---|
| A. Media 路由 | 文本/附件 → L0/L1/L2 | Macro-F1、Need-Vision Recall、Unnecessary-VLM | 80 条合成合同 |
| B. 解析 | 原始页 → OCR/Layout/Table | Edit Distance、TEDS、mAP | OmniDocBench |
| C. 检索 | Query → page/region | Recall@K、nDCG、MRR、All-evidence Recall | ViDoRe、PM209 |
| D. Oracle-evidence 生成 | Gold page/region → Answer + Citation | VLM 上限、Correctness、Citation P/R | PM209 |
| E. Retrieved-evidence E2E | 实际召回 page/region → Answer + Citation | E2E Correctness、Grounding、Unsupported Claim | PM209 |
| F. 客服结果 | Answer/Action → 权威业务终态 | Contract Pass、Authority/Effect Correctness | 80 条合成合同 |

正式报告 schema 固定输出 A–F 六个 section。D 与 E 必须分别报告，不能把 Oracle 和实际检索结果平均成一个“多模态准确率”。

### 6.1 Media 路由

固定评测以下路径：

```text
L0：纯文本/业务工具，无视觉调用
L1：OCR、表格或布局解析
L2：VLM 视觉理解与区域证据
```

硬指标：

- `Need-Vision Recall`：真正需要 L2 的样本不能被压到 L0/L1；
- `Unnecessary-VLM Rate`：纯文本/OCR 足够的样本不应调用 VLM；
- risk-critical false downgrade：高风险安装或维修不得错误降级；
- typed invalid/unavailable：损坏、隔离或无权限媒体不能被解释为“没有视觉信息”。

### 6.2 检索与证据

除页面相关性外，必须验证：

- 引用是否绑定正确 asset、page、bbox/crop；
- 多页问题是否召回全部必要证据；
- OCR 文本与视觉区域冲突时是否正确降级；
- 不相关图片是否被排除；
- 无证据时是否拒答或请求补充材料。

### 6.3 生成与业务权威

图片只能证明“看到了什么”，不能替代订单、政策和业务工具。例如破损退款：

```text
视觉证据：照片中可见的破损位置
订单工具：用户买了什么、何时签收
政策证据：是否满足售后条件
最终业务动作：由权限、风险与工具回执决定
```

评分必须分别验证视觉 claim、业务 claim 和政策 claim 的权威来源。

## 7. 连续图片对话与缓存评测

多模态评测必须覆盖同一图片跨轮复用，不能只测单轮 VQA。

### 7.1 必测场景

| 对话变化 | 期望行为 |
|---|---|
| 同一图：“下一步呢？” | 复用 asset、OCR、layout、embedding 和已有观察；通常 0 次新 VLM |
| 同一图：“这个孔是不是歪了？” | 只对新增区域做 crop + L2，不全图重跑 |
| 新图：“现在这样对吗？” | 只处理新 checksum，保留旧步骤与手册证据 |
| 换 SKU/设备 | 旧实体与相关证据失效，重新绑定产品资料 |
| “坏了能退吗？” | 增加 Billing/Knowledge requirement；照片不能单独决定退款 |
| 知识/模型/预处理版本变化 | 对应 artifact 或 EvidencePack 必须失效 |

### 7.2 缓存指标

- correctness-conditioned media cache hit rate；
- OCR/layout/artifact reuse rate；
- unnecessary full-image VLM rate；
- targeted crop escalation success；
- wrong-image/stale-artifact reuse，目标 `0/N`；
- 冷缓存与热缓存的 P50/P95；
- 每解决一个视觉工单的增量成本。

Provider prompt/KV cache 只作为算力优化单独记录，不能计作业务证据缓存命中。

## 8. DialogPilot 80 条合成客服合同

公开数据集无法证明 DialogPilot 自己定义的服务连续性、副作用、TaskFormation 和连续图片行为，因此保留一个可确定性评分的合成架构合同集。它由模型生成并使用 fabricated fixture，明确不是内部人工 Gold、真实业务分布或生产准确率；本轮固定为 80 条，不再扩展到 120 条。

### 8.1 固定规模

| Slice | 数量 | 内容 |
|---|---:|---|
| PRODUCT_ID | 20 | 合成商品、区域定位和不充分图片 |
| INSTALLATION | 20 | 同图下一步、新图、低/高风险安装边界 |
| DAMAGE | 10 | 可观察破损与禁止业务推断 |
| SCREENSHOT | 10 | OCR-only、错误码与 L2 分界 |
| POLICY_TOOL | 10 | 政策、实时工具、版本与副作用 |
| SERVICE_CONTINUITY | 4 | 活动/终态工单和续接 |
| MEMORY | 2 | Principal 范围与跨用户拒绝 |
| HANDOFF | 4 | 持久工单、幂等重试和 typed conflict |

合同同时包含 40 个 counterfactual pair、20 个连续图片 case、24 个 Clarify/Handoff/Await 终态和 13 个 HIGH/CRITICAL case。数据锁定后，任何内容修订都建立新版本，不覆盖 v1。

### 8.2 每条合同的最小结构

```yaml
case_id:
group_id:
initial_business_state:
conversation_turns:
media_assets:
expected_media_need:
expected_regions:
required_authorities:
required_tools:
forbidden_tools:
required_evidence:
expected_business_state:
expected_handoff:
continuation_expectation:
cache_expectation:
deterministic_assertions:
provenance_and_fixture_refs:
```

`SYNTHETIC_CONTRACT_LOCKED` 只表示 Schema、case、fixture、asset 和 checksum 已冻结。真实 `ChatApplication.handle()` 尚未运行时必须另记 `NOT_RUN`，不能因为静态引用校验通过就写 `80/80`。

## 9. 固定消融矩阵

### 9.1 Intent

```text
当前 LLM + n-gram + Pattern
Typed Fusion V2
BGE-M3 + LogisticRegression
BGE-M3 + LogisticRegression + 低置信 LLM fallback
```

### 9.2 Knowledge RAG

```text
Lexical only
Dense only
Lexical + Dense + RRF
Hybrid + Rerank
Hybrid + Rerank + Evidence packing
```

### 9.3 Memory RAG

```text
Lexical only
Vector only
当前 Vector/Lexical/Recency=.30/.60/.10,k=60
LoCoMo Dev 选择的冻结配置
```

### 9.4 多模态 RAG

```text
OCR text only
Page image only
Text + image hybrid
Hybrid + rerank
```

### 9.5 证据与生成

```text
Oracle evidence generation
Retrieved evidence generation
No-evidence / distractor evidence
```

### 9.6 成本路由

```text
VLM always-on
L0/L1/L2 adaptive routing

Cold cache
Warm artifact cache

Full re-analysis
Continuation + delta analysis
```

同一 paired 消融使用相同 case、模型版本、温度、最大输出、工具 schema、语料 generation 和 Trial 策略。确定性 capture/replay 只跑一次；需要稳定性结论时才增加随机 trial。

## 10. 统计与发布规则

### 10.1 运行规范

- 官方 benchmark 使用官方 split，不修改 test 标签。
- 所有 manifest 在运行前冻结并记录 SHA-256。
- 新旧方案采用 paired evaluation。
- 开发调试默认 1 trial；普通回归 1～2 trials，不报告 `pass^4`。
- 最终 τ³ 只有在每题至少 4 trials 时报告 `pass^1` 与 `pass^4`；成本受限的 197 次运行只报告 `pass^1`。
- 比例报告 `x/n` 和 95% CI，不只报告百分比。
- `0/N` 不能写成零风险；需要同时给样本数。
- Judge 必须固定 model、prompt、schema 和 calibration version。
- Judge 失败输出 typed invalid score，不得用中性分参与平均。
- 基础设施错误单列，不静默删除、重试到成功或混入业务失败。

### 10.2 硬门禁

以下失败不能被其他平均分抵消：

- 跨 tenant/用户证据泄漏；
- 重复业务副作用；
- 禁止工具或越权动作；
- stale/wrong-image evidence 被用于发布；
- `UNAVAILABLE` 被解释为 `NO_EVIDENCE/NO_MATCH`；
- required L2 被错误降级且影响安全；
- 无效引用或不可解引用 MediaLocator；
- Memory/服务债务事实被摘要或模型猜测覆盖。

### 10.3 非劣与成本门禁

自适应路由、缓存和上下文优化必须同时满足：

```text
E2E Task Success：相对冻结 baseline 非劣
Required-authority Recall：非劣
Hard safety failures：不得增加
P95 / Token / VLM calls：至少一项显著改善
```

命中率、Token 降幅或 VLM 调用减少不能单独作为成功指标。

默认可执行判定见[评测收敛执行手册 §2.6](./customer-service-agent-evaluation-plan.zh-CN.md)：quality 非劣 margin=`1pp`，按 conversation/document/group 做 10,000 次 paired bootstrap，单侧 95% CI 下界不低于 `-0.01`；hard safety/authority/effect 新增失败必须为 0。CrossEncoder 和 Parent 还必须分别满足预声明的最小样本与瓶颈归因触发条件，不能看到结果后改阈值。

## 11. 执行顺序

### 阶段 1：合成合同锁定（已完成）

8 个失效引用已修复，数据已重命名为 `dialogpilot-synthetic-contract-v1`，Schema/fixture/media/case/file checksum 已冻结并静态校验到 `valid=true`；当前 `run_status=NOT_RUN`，锁定不等于 E2E 通过。

### 阶段 2：统一入口

补 `run_synthetic_contract_eval.py`、`run_postgres_rag_eval.py`、`run_memory_rag_eval.py` 和 `tau3_adapter.py`。所有 E2E 通过 `ChatApplication.handle()`；OmniDocBench、ViDoRe、PM209 在 evaluator 边界只加输入输出 Adapter，但这不补产品能力。当前完整 layout/table、visual page index 和 page/region retriever 分别标 `UNSUPPORTED_SCOPE` 或 `BLOCKED_MISSING_SYSTEM_CAPABILITY`，不得在 evaluation sidecar 里临时实现一条拿分主链。

### 阶段 3：Dev 选参并冻结

1. Intent：先统一 ontology，再比较 V1、Typed V2 和 BGE-M3+LR→LLM cascade。
2. Knowledge：先接真实、输入对称的 BGE-M3；依次调 Chunk、RRF、Query、K、Rerank、Packing。
3. Memory：补 ServiceEpisode dense projection；LoCoMo 调权，LongMemEval 留作冻结测试。
4. Multimodal：依次选择 L0/L1/L2、解析、视觉检索和 Oracle/Retrieved generation。

详细参数网格、历史实验复用与重开条件见[评测收敛执行手册](./customer-service-agent-evaluation-plan.zh-CN.md)。

### 阶段 4：冻结测试与 E2E

1. 80 条合成合同走真实主链，报告 `x/80` 与 hard failures。
2. 公开 Intent、Knowledge、Memory 和多模态 test 只运行冻结配置。
3. τ³：airline/retail/telecom 官方 test 共 100 + banking_knowledge 97；`RC_PASS1` 跑 197 次只报 `pass^1`，`FINAL_STABILITY` 跑 `197×4=788` 并报告 `pass^1/pass^4`。
4. 五个成本消融同样走真实主链，不能用离线计数冒充 task-success 结论。

### 阶段 5：最终成绩单

只保留 Intent、Knowledge、Memory、多模态、τ³ 五组公开数字，再附 80 条项目合同证据；不生成虚假的综合总分。

## 12. 产物与可追溯性

每次正式运行至少生成：

```text
artifacts/eval/<run_id>/
├─ manifest.json
├─ predictions.jsonl
└─ report.json
```

官方分数、失败、Trace、成本和 checksum 可以作为附加文件，但三个核心文件的名字和语义保持一致。

`manifest.json` 至少包含：

- dataset/release/split/task IDs；
- dataset license 与来源；
- DialogPilot commit；
- model/provider/prompt/tool schema；
- route、knowledge、memory、media policy fingerprint；
- corpus/source revision/retrieval generation；
- random seed 与 trial；
- evaluator/judge version；
- 开始和结束时间。

## 13. 简历与报告口径

完成正式评测前不得填入虚构百分比。可使用以下模板：

> 在 `τ³-bench v1.0.1` 的 airline、retail、telecom 官方 test 与 banking_knowledge 共 197 个任务、每题 4 次运行中，取得官方 `pass^1=X`、`pass^4=Y`；同时报告项目定义的 safe pass、Retriever/Tool/LLM 调用、Token 和 P95。若只跑 197 次，则删除所有 `pass^4` 表述。

> 在 ViDoRe v3 与 PM209 冻结测试集上，Hybrid retrieval 的 nDCG@5 为 `X`、All-evidence Recall 为 `Y`；相较 VLM always-on，L0/L1/L2 路由将 VLM 调用率降低 `Z%`，并保持端到端任务成功率满足非劣门禁。

> 80 条模型生成合成客服合同在真实 `ChatApplication.handle()` 主链上通过 `x/80`；高风险违规 `x/N`，重复业务副作用 `x/N`，并报告连续图片复用和 TaskFormation。该数字不是人工 Gold 或生产准确率。

这些结果分别证明外部可比能力、多模态检索能力和 DialogPilot 业务闭环，不能互相替代或拼接成一个“总体准确率”。

## 14. 最终决策

DialogPilot 的正式评测栈固定为：

```text
MASSIVE zh-CN + BANKING77 + CLINC150
+ LoCoMo + LongMemEval
τ³ v1.0.1
+ WixQA
+ MTRAG
+ OmniDocBench
+ ViDoRe v3
+ PM209/MPMQA
+ DialogPilot 80 条合成客服合同
```

MASSIVE/BANKING77/CLINC150 负责 Intent/OOS；τ³ 负责公开 Agent 服务链；WixQA 与 MTRAG 负责文本知识和连续追问；LoCoMo/LongMemEval 负责 Memory；OmniDocBench、ViDoRe 与 PM209 分别证明多模态摄取、检索和回答；80 条合成合同负责 DialogPilot 特有的服务连续性、Memory/Handoff、图片路由和副作用合同。

这套组合优先复用官方数据、可用的官方 split、evaluator 和指标。DialogPilot 只在 evaluator 边界实现输入输出适配，在项目合同侧实现权威业务状态评分、缓存/路由观测和 grader；MTRAG 自定义 group split 必须显式披露。任何 benchmark 所需的 parser、visual index 或 page/region retriever仍由 production Owner 实现，不能塞进 Adapter 冒充系统能力。
