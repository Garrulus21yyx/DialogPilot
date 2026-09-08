# RAG 优化主线：持续维护的状态入口

最后核对：2026-09-08；本轮起点 HEAD `6828dba`，微调实验提交 `228a90b`。本文维护当前优先级与验收状态，历史报告维护当时的实验事实。更新时填写实际核对版本，不能把工作区实现等同于已提交／已部署。

**整体状态：三套检索对照、均衡默认采用、有限真实入口执行与统一报告已交付；真实质量验收未全部通过，整体RAG仍未完成。执行完成与质量达标分别记录。微调暂停。**

## 当前执行优先级（2026-09-08 最新用户要求：完成主线，禁止偏离）

独立会话维护记录（不改变并行检索实验优先级）：本会话用户授权修复近期知识证据续接，见[证据复用计划](conversation-evidence-reuse-2026-09-08.md)。原12条重判为10通过/2待核实，不以无RAG调用判业务错误。final/追问的私有证据记录接回主Agent，复用同一来源有效性检查，不加模型或检索层；427项真实PG及相关回归通过，独立审查通过，模型API0。此为实现验证，非R01语义关闭；旧pack缺失不伪造迁移，检索/微调并行工作保持原安排。

**当前活动项R04/R05：MTRAG每路40→融合20实验完成，固定query100最终Recall48.31→48.86，2改善/1退化，分组95%差值区间跨0；FiQA退化，不采用生产。API0、新CE128。下一项固定排名诊断并集→融合20的证据挤出机制，先分析再预注册策略；核验与微调暂停。结果与官方协议见docs/mtrag-depth40-2026-09-08.zh-CN.md及docs/rag-official-benchmark-protocols-2026-09-08.zh-CN.md。**

预注册：冻结原query、语料、分路20、融合20、CE分数、最终5/2600；复用fresh100记录，不调参。测全库包含（Doc）、Dense20/BM2520/并集、融合20、CE Top5、实际pack/wire；逐例保存损失位置，区分指标上限与语义根因。预算：API0、新embedding0、新CE0。验收：复现原baseline与来源身份，分层集合不变量成立，报告每层净损失及局限；不以本次诊断宣称策略改善。完成后优先补Doc仅依据历史的完整query诊断，不重构Agent、不恢复微调。

当前活动项按最新用户指令切换为 R04/R05：只提升固定查询下三套检索表，冻结Agent和微调。基点8d96122。预注册：原Doc300/MTRAG35/Wix20已消费集，原query/corpus/.5候选20不变，原CE排名与候选融合排名做rank RRF（k10，CE权重.75及.5），以及Wix独立的每文章首片段优先后补同文片段；最终5/2600原pack+wire。零embedding/CE/API，先复现原69.3/44.1/67.5；不复现就停止比较。逐臂Recall/MRR/nDCG、救回误伤、来源审计，全部保存。只作开发策略筛选，采用须独立验收，不因小样本变好直接改生产。

2026-09-08 e9fff7a真实回归已完成：12次规划/编译有效，独立复核10通过、1覆盖存疑（Malta附加费遗漏）、1失败（旧助手VS-118被当政策直接回复）。7条要求知识均选动作，但不等于检索成功或需求全部保持；未运行RAG/业务工具/发布。输入与源码审计通过，相关52测试通过。见[完整报告](../docs/native-actions-regression12-2026-09-08.zh-CN.md)。R01仍开放；下一步只围绕对话与政策证据的权威边界、查询完整覆盖审查，不继续无界调参。本轮为测试交付，非语义修复。

2026-09-08 用户要求真实测试：e9fff7a原生动作入口，冻结12条已消费回归题（旧confirmation8＋已知4例），Flash/NONE/2048/0重试，每题一次总预算12，不执行实际业务工具。按完整query/options、自然对话、必要澄清及越权动作判定，不仅看选中工具名；范围为规划回归而非新鲜E2E。预注册见[动作接口计划](conversation-action-interface-2026-09-08.md)。

2026-09-08 用户授权动作接口收敛（起点`d2287a2`）：主规划改为单次SDK原生动作选择，现有内部计划/Policy/TaskGraph保持执行权；审批、补答与恢复仅按当前状态暴露，不新增路由LLM或备用执行路径。见[实施与验收计划](conversation-action-interface-2026-09-08.md)。本次先验证接口/混合状态/回放和持久化边界；原R01语义错误与共享知识过滤参数来源仍待验证，不以接口重构或测试数宣布闭环。微调暂停，不继续few-shot调参。

动作接口本轮实现完成并通过独立静态复核；隔离暂存树525通过、7项PostgreSQL依赖跳过，付费模型调用0。旧文本规划provider/回放双运行模式移除；历史gzip仍可读取。知识快捷动作保留原owner和历史时间语义。详见[交付说明](../docs/conversation-action-interface-2026-09-08.zh-CN.md)。此处标记接口交付，不标记R01语义关闭。

2026-09-08 当前用户授权：比较并选择取证决策的无示例/固定/动态few-shot，沿现有主Agent接入唯一采用路径。活动项仍R01；预注册、预算和采用门槛见[取证决策选择计划](planning-evidence-selection-2026-09-08.md)。不改检索器、不新增分诊或核验模型，微调暂停；此前12题作为开发证据，不能再称封存。

本轮结论：112次规划调用完整保留，固定/动态few-shot有条件污染，仅决策说明在最终确认增加无来源policy_date，三候选均不采用。生产主链不变；选型完成不等于R01修复。122项本地检查通过、1项外部数据库跳过；无业务执行/部署。详情及评审更正见[采用评估](../docs/planning-evidence-selection-2026-09-08.zh-CN.md)。后续须把完整查询参数纳入语义验收，而非只看路由和query文本；本轮不继续模型试验。

当前核对 HEAD `9cf8fa5` 加本轮实验（2026-09-08）。当前活动项 R01：真实查询12条配对校准已完成，开发候选有收益但未采用；取证选择/合理澄清/协议及独立验收仍开放，以下四项为历史交付记录。下列顺序覆盖旧的实验日志优先级。使用同一份状态文件维护，不再另开单例核验支线。

1. **已完成本轮已有样本对照：三套检索对照补齐与统一汇总。** 固定每路20、候选20、最终5、正文2600预算；保留数据各自标注粒度，不混合文章/片段/span。复用向量、排名和精排分数。先补MTRAG .5（旧35题仅.25/.75），再核对Doc2Dial同预算.25/.5，Wix已有20+20。已消费集明确标记，不重新称封存。
2. **已完成：方案采用决策，代码默认0.5/0.5。** 按开发选择、跨集误伤和已有测试结果决定统一固定权重或按库配置；不新增动态模型、切块策略、HyDE、微调。现有策略证据不足则明确不采用，不能无限调参。
3. **执行完成、存在失败：少量同入口最终答案验收。** 三种外部数据各固定少量题，真实Context→Agent query→检索→精排→工具可见→答案；预先记录参考要点，分别统计支持性/需求覆盖/耗时/调用。保留原始失败，不反复重跑。业务模拟集单列。
4. **已完成本轮统一报告，待本提交推送：统一报告与交付。** 给出三套各自Recall/MRR/nDCG、配对救回误伤、答案质量及成本；报告支持格式、metadata与性能验证边界。代码、可复现入口、证据和commit/push一致。实现交付不冒充全范围可靠性保证。

**退出条件：**上述对照、采用结论、有限真实链路验收和统一报告齐全；不得用局部核验实验替代。既有语义漏检继续计入答案失败与已知风险，除非阻止评估运行，不开启新核验设计。微调继续暂停。

**本轮预注册：**MTRAG已消费35题原query/ranks/全库，补.5同预算重放。700个候选query-passage对全部在旧.25/.75本地CE评分中存在，计划新embedding/CE/API均0。验证旧query、gold、顺序和输入身份；比较候选Recall及pack Recall/MRR/nDCG，不据此单独采用。输出rag-mtrag-balanced35-2026-09-08。

## 1. 目标与统一链路

目标是智能电商客服在正确的身份、适用范围和证据下回答问题，并量化质量、误伤、延迟及成本。检索 Recall、核验 PASS 和最终答案正确率分别统计。

```text
离线：原始资料 → 解析／结构／来源位置 → Metadata与版本 → chunks → 词法／向量索引
在线：统一Context → Conversation/领域Agent → 完整检索query
    → knowledge_search（系统注入身份、来源目录、适用范围和预算）
    → 分路召回 → 融合／去重／候选截取 → 精排 → EvidencePack
    → 实际ToolMessage → synthesizer／受控业务事实 → 核验 → 发布
```

权威边界：Context 提供已知会话事实；负责当前任务的 Agent 生成检索问题；知识来源模块拥有原文、适用信息、版本和撤回状态；检索器负责定位证据；业务工具拥有订单状态和执行回执；发布模块使用原始事实与核验材料决定发布。评分报告不补查询、不改业务含义、不作为事实来源。

保持的接口不变量：RESOLVED 查询保留已知主体、否定和必要条件；授权范围由系统注入；召回和精排使用一致的完整问题；来源版本／偏移可复验；工具完成不等于有证据，核验通过不等于答案已被独立评为正确。未知字段按现有合同处理，不由模型臆造日期、渠道或用户资格。

## 2. 缺口台账

状态含义：**未解决**＝有失败证据、尚未完成当前版本修复验收；**待验证**＝实现或局部结果存在、缺代表性验收；**待交付**＝工作区有实现但未完成提交与证据整理；**暂缓**＝本轮不推进。每项只能引用对应范围的实验，不能跨数据集代替验收。

| ID | 环节／责任边界 | 当前事实 | 未完成事项与完成标准 | 状态／阶段 |
|---|---|---|---|---|
| R01 | Context → Agent query | 支持 RESOLVED；手工完整查询有收益 | 同案例捕获真实 Agent 可见 Context 和实际工具参数，区分上下文未提供、查询改变含义、合理简化；不得只按字段出现率判错 | 待验证 G2 |
| R02 | 文档内 child 定位 | 旧 60 条 heldout 有 3 条正确文档排第1但 child 深排 | 原失败用当前代码复测，记录分路／融合／parent／child 位置；有界局部检索需同预算报告救回、误伤和最终可见覆盖 | 未解决 G1/G3 |
| R03 | 完整查询的跨语言召回 | 当前20条完整查询仍有1条西语问英文资料漏召回 | 分别测词法、Dense、融合；保留原查询的翻译扩展仅作待验证方案，不能凭语言差异直接断定根因 | 未解决 G1/G3 |
| R04 | embedding／融合／候选预算 | 已证明单query/BM25满20时，.25/.75与k10使Dense独有候选全部被排除；有Dense第4被丢弃的见证 | 分清单路未召回与融合截断；比较原方案和开发选定候选，冻结每路深度、最终池、token预算；embedding更换必须重算相关索引 | 未解决 G3 |
| R05 | 精排／缓存身份／工具序列化 | 固定1200字符正文截断已去除；本地模型接入、精排模型身份和工具误拦截修复已通过本轮验证 | 137项相关测试及100条记录审计通过；见G0报告。实际上线部署未验证，最终答案验收另列R06 | G0交付 |
| R06 | synthesizer／核验／发布 | 有逐项核验及受控业务事实；6条真实链路完成并verified | 按政策条件、引用支持、需求覆盖、合理未知／弃答独立评分；区分语义误判、漏检、协议失败；业务工具用稳定fixture保持真实编排 | 待验证 G4 |
| R07 | Metadata／来源维护 | 地区、时间、商品适用信息接入；渠道目录动态注入已提交 | 导入→更新／历史生效→撤回→索引／缓存→最终引用的代表性验收；已有单元性质测试不是这一链路的总体成绩 | 待验证 G2/G4 |
| R08 | 解析／chunking | 固定、结构、不同大小、parent策略均有局部比较 | PDF、表格、列表、否定与例外的解析保真未系统验收；先锁定支持格式和保留位置，按源证据完整性验证 | 待验证 G4准备 |
| R09 | 检索性能／索引 | 有界并行实现存在，默认关闭；488文档BM25查询出现750ms超时，需执行计划归因 | 同结果下测请求p50/p95、吞吐、池等待、超时与回退；HNSW/ANN使用状态和近似召回损失需专门核对，不凭名字判断 | 未解决 G3优先 |
| R10 | 评测与交付管理 | 分层结果多，数据／预算／入口不同；旧计划滞后 | 本文统一活动项；每项绑定证据、版本、限制和是否采用；三类数据上的同入口封存验收尚未完成 | 未解决 全程 |
| R11 | 本地精排微调 | 小规模部分参数训练开发和验收均无提升，已提交 | 用户恢复前不新开训练；届时先确认候选完整但排序持续失败、可用训练标签和新的隔离验收集 | 暂缓 |

## 3. 证据索引：指标不能混算

| 编号 | 范围／配置 | 结果及其支持的结论 | 原始证据 |
|---|---|---|---|
| E01 | 旧官方test新鲜60条，488文档，本地三路 | 完整R@20 56/60；1条省略query丢agent问句，3条文档对但child深。须在当前版本重放，不能沿用历史根因作为当前结论 | [报告](../docs/local-public-rag-retrieval-evaluation-2026-09-03.zh-CN.md) |
| E02 | 已暴露开发20条，手工完整query，本地source40/candidate20 | 完整候选17→19，Top5 16→18；不证明真实Agent改写成功 | [报告](../artifacts/eval/rag-authored-query20-2026-09-07/report.json) |
| E03 | 开发300条raw query，PG切块对照，同K非同token | 512/64完整候选207/300；更小切块未胜出 | [汇总](../artifacts/eval/rag-pg-chunk-four-2026-09-07/summary.json) |
| E04 | 开发300条，本地flat vs parent内重检索，候选20 | 207→203，救回3误伤7；这版策略不采用，不等于E01的3条已修复 | [报告](../artifacts/eval/rag-local-parent-pair-v2-2026-09-07/report.json) |
| E05 | 开发300条，本地扩大候选20→80，pack2600tokens/5片段 | 候选223→244，pack208→207；扩大池并未带来最终覆盖收益 | [报告](../artifacts/eval/rag-candidate-budget-2026-09-07/report.json) |
| E06 | 固定完整query20条，实际PG/tool入口，每路20/候选20/pack2600/5；无Agent规划与答案生成 | 修复误拦截后：候选19，本地CE可见18，Flash可见19。Flash排序来自20次已保存调用，修复后的重放0API。重放延迟不含真实精排，不能当模型速度 | [原始](../artifacts/eval/rag-production-reranker-pair20-2026-09-07/summary.json)、[重放](../artifacts/eval/rag-production-reranker-replay20-2026-09-07/summary.json) |
| E07 | 中文电商模拟6条，真实Agent链路24次Flash | completed/verified均6；不是独立答案正确率评分，也不是线上准确率 | [报告](../artifacts/eval/rag-owner-full6-2026-09-07/full-report.json) |
| E08 | 官方train内80/20/40、首轮query、BM25候选、256/32，局部参数微调 | 新分组验收Top5 24/40→24/40，候选上限25/40；无收益不采用。首轮重复片段污染结果标无效，两轮均保留 | [报告](../docs/rag-reranker-finetune-2026-09-07.zh-CN.md) |

E06原始／重放、精确输入快照和审计随G0提交；E02—E05保留其历史证据范围，不能据链接存在推断所有本地产物均已上传。

## 4. 活动队列与可证伪的退出条件

只有一个活动执行阶段；新用户指示可调整优先级，但须先更新本文的原因和影响。独立的数据准备可提前做，不新增付费实验支线。

### G0 已完成本轮交付：收尾已有修复，不新增检索策略

- 核对精排适配、请求／缓存实际模型身份、误拦截修复、校准脚本的依赖和异常路径。区分运行时修复与仅实验接线。
- 对已有测试记录复核并运行受改动影响的检查，复验原始排序→重放候选/正文一致性、pack→实际ToolMessage覆盖。无需重跑已有付费排序。
- 写出 E06 的原始统计曾把工具隔离误当打包丢失的更正；未做的真实Agent回答回归不得写完成。
- 提交相干代码与证据，记录提交ID和实际检查结果。退出：代码、报告、测试和提交状态一致。G0结束不代表召回问题关闭。

本轮交付文件范围（不包括其他任务脏文件）：`api/main.py`、`application/knowledge_retriever.py`、`infrastructure/knowledge_retriever_adapters.py`、`infrastructure/local_knowledge_reranker.py`、`core/input_security.py`、`evaluation/rag_full_chain_probe.py`、`scripts/run_rag_tool_calibration.py`、`scripts/run_rag_production_reranker_pair.py`及相关测试／E06产物。其他任务的脏文件不混入提交。

### G1 诊断完成：恢复已知漏召回的当前版本诊断

- 起步为 E01 的4条旧失败＋E06的1条跨语言失败，去重后形成固定回归清单；旧heldout已暴露，只作回归，不再叫新鲜验收。
- 每条保存：原始用户消息、原有query、完整query的来源、过滤集合、每路gold排名、融合排名、正确文档／child位置、候选／精排输入／pack／实际可见覆盖、失败阶段。
- 两个query的评测分开：旧输入确认历史触发是否仍在，完整输入隔离检索能力。人工完整query不能读gold答案后倒填检索关键词。
- 退出：每条有“仍失败／已恢复／证据不足”的可复算记录。不能为凑分类而强行给根因，也不能只因历史失败恢复就宣布泛化通过。

### G2 开发证据已有、泛化待验证：真实Agent查询与完整query的同案例对照

- 从统一 Context 和工具目录进入同一个 Conversation Agent；保存模型实际可见输入与工具参数。系统身份、目录、时间语义按生产入口注入。
- 初始复用20条诊断案例，先做零API回放；只有缺少真实Agent输出时才补Flash调用并设调用上限。业务读结果使用固定fixture，保持真实工具选择／编排；不执行订单写操作来测RAG。
- 比较真实query与人工完整query的条件语义、召回净收益、实际证据及成本。中文电商模拟条件例在此阶段参与，历史／假设／实时状态区别不等到最后才补。
- 退出：能区分上下文缺失、Agent语义错误、无害简化与检索失败，并确认“人工query的收益”是否迁移到真实入口。

### G3 采用待验证：优先融合候选资格与BM25执行失败

- 单路已找到、融合后丢失：离线重放固定／按库配置／查询自适应融合；候选得分与排名不混用，权重只由开发集确定。
- 正确文档内child遗漏：对比保留全局分支的有界parent内重检索，计额外工作量，不以同最终K声称计算量相同。
- 跨语言或术语表达失败：比较原query与保留原query的扩展分支；区分翻译、同义扩展与上下文消解。
- 每次只选一个有依据的主变量；保留所有救回和误伤。采用条件是目标覆盖／排序或成本改善且无未解释关键误伤，收益必须传到最终可见证据；不胜出则保留当前方案。

### G4 当前活动项：完整答案及封存验收

- Doc2Dial公共对话检索；MTRAG／WixQA补充数据按各自标签和支持入口报告；中文电商模拟集单独标记。先核对可用版本、许可、分组和历史消耗，不把三者凑成一个准确率。
- 冻结来源、分组、query模式、模型、候选／token预算、策略和评分规则后再打开封存集。答案与核验PASS分别记录，按事实／条件／例外／引用／弃答评分。
- 包含来源更新、历史生效、撤回、通用政策与特定适用条件、政策知识与业务状态耦合；补充声明支持的文档格式保真案例。
- 少量完整链路先估计费用与波动，扩样需解释用途。每组模型输入未变可复用，变了才重跑。最终交付分层配对表、逐例归因、复现脚本、成本和不确定性。

## 5. 维护规则与决策记录

- 每次RAG工作开始：读本文、核对HEAD和工作区、继续当前阶段；每次结束：更新事实、证据路径、检查、提交状态和下一动作。新的状态文件不得另立一套优先级。
- 新实验必须先写：对应R编号、要推翻／支持的假设、数据与分组、冻结变量、预算、指标和采用条件。结果无提升也登记。
- 成功完成实验、实现完成、提交完成、封存验收通过是不同状态。旧数据／旧根因不自动代表当前版本；不同入口、候选深度和指标定义不横向拼接。
- 原始、重放和修正统计均保留；失效结果标原因。正文覆盖和合法引用不能证明语义核验正确。
- 2026-09-07：用户要求停止分散推进。统一本文为主线入口；G0活动；微调暂缓；Flash保留；旧召回失败回到G1。主线变化由明确证据或用户指示驱动。
- 本次维护交付：创建本文与根AGENTS持续维护约定，校正精排子计划与微调暂停状态；本地文档链接检查通过。仅文档整理，新增模型/API调用0。文档提交不代表G0的代码交付完成；下一动作仍为G0的工作区修复及原始／重放证据复核。

子实验记录：[生产精排对照](rag-production-reranker-pair-2026-09-07.md)、[微调](rag-reranker-finetune-2026-09-07.md)。其他历史计划保留当时记录，不用于判定当前主线完成。

- G0执行记录：137项测试通过；100条排序／候选／源文／覆盖及当前guard检查通过；本轮新增API 0。生产默认未切换。已完成[交付报告](../docs/rag-production-reranker-pair-2026-09-07.zh-CN.md)，随本次提交推送；G0完成不代表R01—R04或最终答案问题关闭。下一步只做G1的旧4条＋跨语言1条当前版本复测。

- G0推送确认：`5018f83` 已推送到 `origin/feat/customer-service-target-architecture`。G1于本轮开始执行；尚未把旧召回缺口标为已修复。

- G1实验预注册：R01/R02/R03，旧4例各用历史user_history表达与仅依赖会话的手工完整表达，跨语言例沿用已冻结query；两套原语料分别导入隔离PG。生产候选20/.25/.75不变，额外每路1000仅诊断排名、不送入候选。预算API0，记录gold分路／文档内／融合位置。排名未知明确标为1000内未见。无新策略采用；完成条件为逐例可重算归因，非准确率提升。

- G1诊断调整：488文档路径返回UNAVAILABLE，数据库日志确认statement timeout。保留750ms生产预算；另设10s仅用于诊断的连接池获取排序，结果标relaxed_diagnostic，不作为生产修复或收益。先记录环境／执行失败，再分离语义漏召回。

- G1结果：5例9种表达完成候选诊断，API0，3项测试通过。已证明R04的候选排斥代数，实际Dense第4证据被融合删除；R09新增BM25超时，放宽预算仅诊断。旧4例中2例完整query在诊断中恢复，不算生产修复。详见[报告](../docs/rag-known-miss-g1-2026-09-07.zh-CN.md)。
- 依据本轮证据调整顺序：G3优先修融合候选资格与SQL执行失败，再G2真实Agent对照，避免为已知有缺口的候选管线支付额外模型费用。生产权重／预算未改，微调仍暂停。

- G1交付确认：`c8ad0e8` 已提交并推送；9条表达的输入、排名、运行失败和审计记录完整保留。当前下一活动项G3尚未实施修复，优先R04候选融合合同和R09 SQL执行计划诊断。

- G3开始：R09先捕获生产BM25 SQL的EXPLAIN ANALYZE，固定488篇语料／query／过滤；词法专项使用确定性占位向量，Dense关闭，不用于模型质量结论。R04先重放已有排名，固定池20比较候选互补合同，API预算0。不增加生产超时，不按单例选权重。

- G3/R04离线对照预注册：复用已暴露dev300的512/64结构切块、BGE-M3/Python BM25评分矩阵；每路20、最终20、RRF k10及.25/.75不变。比较现有融合与两路交替取未重复候选、再按原RRF排序。只测候选完整覆盖、MRR/nDCG、救回/误伤；不训练权重、不调用API。此实验只筛选候选资格设计，未验证最终可见证据前不改生产默认。

- G3本轮结果：R09在BM25 owner修复重复统计并固定浮点求和次序；同库三个表达中位延迟1671/1804/995ms→128/131/172ms，默认750ms下3/3成功且最终对照分数／顺序一致。R04 dev300候选207→218，救回21／误伤10，MRR .4831→.4854、nDCG .5322→.5417；暂不采用。26项测试通过，API0。见[报告](../docs/rag-g3-bm25-membership-2026-09-07.zh-CN.md)。R09并发／生产部署未验证；G3下一步为10条误伤及最终可见覆盖诊断，不能提前宣称召回闭环。交付：`e0d1af8` 已提交并推送到 `origin/feat/customer-service-target-architecture`；其他任务工作区未混入。

- G3续轮预注册（起点a49633b）：R04固定上轮300例及两组候选，不改权重或配额。先分类21救回/10误伤的分路gold位置与删除位置，再审计已有本地CE缓存的候选ID/输入/模型对应关系。完整匹配才复用；预算先0API，缺失本地评分单列。指标为候选/精排Top5/2600-token、5片段打包完整覆盖及配对救回误伤；最终ToolMessage、Flash与答案未测时明确留空。未得到最终可见收益不切生产。
- 缓存审计：原CE缓存6000对，当前两组候选并集缺2360对、涉及298例。批准的本轮执行预算采用本地现有BGE补算最多2360对，API0；先复建旧候选顺序并校验dataset/model SHA。使用原完整query/child输入、FP16、batch4、不截断。不能把本地缓存重放延迟当Flash延迟。

- G3续轮结果：10条候选误伤均为预算替换移除BM25第11—19的必要证据（7条Dense前20未命中、3条双路均较深）；固定本地CE/packer后，完整覆盖199→205/300，救回11／误伤5，MRR@5 .5644→.5793、nDCG@5 .5895→.6057。新增本地评分2360对，复用5536对，API0。3项性质／产物审计通过，300条无模型重放逐字节一致。详见[阶段报告](../docs/rag-g3-membership-stages-2026-09-07.zh-CN.md)。未测Flash/实际ToolMessage/答案；不切生产。下一动作固定两方案进入小规模生产精排／工具可见配对，先核对已有缓存；不再依据这31条调配额。交付：`58604ab` 已提交并推送到 `origin/feat/customer-service-target-architecture`。
- G3生产边界预注册（HEAD aa39569）：旧Flash缓存query为人工完整表达，与本轮raw输入不等价，不复用排序。按SHA256(case_id)排序从dev300固定抽12条，不按候选救回/误伤筛选；原raw输入、100文档512/64、每路20/候选20/.25/.75/k10/pack2600及5不变。实际PG和知识工具入口，实验候选适配仅支持单query、无metadata hint、时间点；生产owner不变。两方案Flash24次计划调用，含重试上限28、并发1；记录失败而非计入语义miss。测候选/精排/pack/实际ToolMessage覆盖与MRR/nDCG，非新鲜验收，不生成答案。不采用直到证据足够。

- G3生产边界结果：按哈希固定抽取12例，24次Flash调用/0回退/0检索失败；两组候选集合12/12发生变化，但候选、Top5、打包和实际可见均10/12，MRR .7361、nDCG .7609均相同。120个可见源片段逐字核对通过，3项测试通过。见[报告](../docs/rag-g3-flash-pair12-2026-09-07.zh-CN.md)。不采用候选切换，未证明答案改善。两条剩余失败为上下文依赖raw表达；活动阶段回到G2，捕获真实Context→Agent query再作同案例比较，G3融合缺口保持开放。交付：`b533b2a` 已提交并推送到 `origin/feat/customer-service-target-architecture`。
- G2起步预注册（1f7ac5e）：先对G3两个候选失败作定位，不当作随机总体指标。通过PostgreSQL持久化原始user/agent历史、原runtime context loader和manager.prepare运行真实Conversation Agent；不手工拼TargetTurnContext，不读gold生成query。Flash规划上限4次，先只捕获Context/实际模型消息/plan/query，不生成答案。检索复验保持G3 .25/.75、每路20、候选20；禁止沿用旧脚本.5/source40混算。无query记录为规划未产生检索，不能静默回退raw。
- G2实际定位：Doc2Dial yes例完整历史已注入，却因儿童残障福利属于电商范围外而OUT_OF_SCOPE；不能按query生成失败记错。tag例产生单条查询，继续实际PG复验。为检验目标场景，同入口补2条明确标记的中文电商模拟（拆封/非质量否定、假设退款审核不查订单），新增Flash上限2；不改生产业务范围迁就公共数据。

- G2本轮结果：实际Context/Agent公共失败2例＋电商模拟2例，共4次Flash。tag例query由真实Agent补全后PG完整gold从候选缺失→第1；yes例历史2/2已注入，但儿童福利被判OUT_OF_SCOPE，不当生成失败。电商否定/假设2例均生成READ知识查询，保留条件、未计划订单工具。2项产物审计通过。见[报告](../docs/rag-g2-context-miss2-2026-09-07.zh-CN.md)。G2继续：把模拟例实际查询接真实知识证据和答案链；公共检索任务与电商范围评测分开，未改变生产范围。交付：`241989a` 已提交并推送。
- G2答案链预注册（e937b65）：复用上轮两条中文模拟的历史和消息，从真实coordinator入口重新规划并完成执行/生成/核验/发布；不把旧plan强塞给runtime。固定人工模拟政策、.25/.75与现有生产预算；记录新query与旧query差异。Flash总上限16次、单批2例；只读知识查询。逐例人工核对来源原文、否定、假设与到账不确定性、是否出现无据业务断言；verified不当答案正确标签。完整链调用费用不能和前轮只规划混算。
- G2全链首次导入在API前失败：模拟web/store来源与未配置catalog不一致。修复位于模拟资料owner：显式提供模拟渠道目录，校准入口注入同一store快照供导入和runtime使用；生产默认不变。首次失败不计质量样本。保留失败说明后在新隔离输出目录重跑，API预算仍16。
- G2全链v2：5次Flash，拆封例完成且引用支持；假设到账例在规划schema校验失败，未进入检索。根因是provider prompt要求所有规则查询填allow_action_proposals=false，但schema只允许delegate_task携带。owner级修复prompt和字段description明确delegate-only，schema/编译器授权边界不放宽。v3重跑2例，剩余API上限11，使本阶段总预算不超过16；保留v2失败证据，不把协议失败当幻觉。

- G2全链结果：catalog fixture修复后v2为1完成/1规划协议失败（5次Flash）；修复planner提示的delegate-only字段合同后v3两条完成并verified（8次Flash）。总13次，未超16。实际来源/引用审计2/2，Codex逐项语义核对2/2支持，非总体准确率。78项测试通过。见[报告](../docs/rag-g2-full2-2026-09-07.zh-CN.md)。下一步扩回冻结回归并建立验收矩阵，保留失败轮；G2/G4整体量化仍开放。交付：`2e5becc` 已提交并推送。
- 冻结回归预注册（cff1809）：重跑已有full-chain CASES六例（否定、加急运费、历史日期、EU期限、定制例外、生效边界），不生成新案例或调参。实际完整runtime，固定模拟政策/目录及当前生产检索配置；Flash上限28。记录源文件hash（当前其他任务有response_assembly/orchestration工作区改动，不混入本次提交）。评测Completed/verified与原文可见/引用/人工条件评分分开，失败保留，不用旧成功结果替代新版本验收。同步整理各R编号验收矩阵，仍非新鲜heldout。
- 六例回归：23次Flash，Completed/verified 6/6；必要原文可见5/6，有证据引用有效2/5。3条畸形引用漏过发布，另1条EU因当天新生效模拟资料AMBIGUOUS。修复严格citation gate（原答案零API重放三条坏引用均拒绝）及模拟固定生效日期；43项测试通过。未重跑全链，不宣称全部恢复。见[报告](../docs/rag-g2-regression6-2026-09-07.zh-CN.md)和[矩阵](rag-acceptance-matrix.md)。运行包含其他任务工作区响应改动，hash已记录；仅提交本次citation hunk。下一步固定版本再验完整链。交付：`a0de151` 已提交并推送。
- 干净版本复验预注册（4810292）：独立detached工作树运行相同六条full-chain案例和修复后的固定模拟生效日期，Flash上限28次、并发1；不调查询、权重或重排。记录精确commit、源hash与配置，保持引用gate启用；Completed、verified、原文覆盖、引用、人工语义分开。独立工作树只读取现有provider环境，不混入主工作区改动。必要的安全弃答不称正确回答。
- 独立4810292工作树复验：24次Flash，必要原文/引用6/6；EU相同地区/当天日期由AMBIGUOUS→OK。Codex语义核对4条支持、2条需修订（加急肯定开场与否定正文冲突；定制范围概括扩大且重问质量原因）。不能称答案6/6正确。原verifier输入/通过输出已冻结，见[报告](../docs/rag-clean-regression6-2026-09-07.zh-CN.md)。下一步仅诊断极性/范围核验覆盖，复用固定材料，不新增检索实验。交付：`6a63051` 已提交并推送。
- 语义核验实验预注册（3d21c36）：冻结两条witness的原始system/schema/evidence；每条原答案与最小语义修订各一条，先4次Flash复现判决。若仍漏检，再比较仅增加通用极性/范围核对说明的同模型4次；额外最多4条控制，上限12。无检索、生成或业务执行。采用要求是风险样本判别改善且正确修订/控制不误拒；小样本不证明无幻觉。当前services/claim_verification含其他任务脏改动，不混入交付。
- 语义诊断中间结果：原配置4次和附加极性/范围要求4次均把原答/修订答全部放行，提示无可见收益，不采用。最后4次控制固定相同证据，使用明确违背政策与明确符合政策的表述，检验是否连显式矛盾也漏检；原反例保守表达标签与明确事实错误分开，不强行声称统计幻觉漏检率。
- 语义固定材料实验结束：共12次Flash，原提示/附加通用极性范围提示均4条全通过，新增提示无收益不采用。明确对错控制在附加提示下2错拒绝/2对通过（未测原提示控制，不声称提升）。原两条保留表达风险，非无争议幻觉金标；不能以手工4支持/2修订称4/6事实准确率。12条输入/输出不变量审计通过，生产verifier未改。见[报告](../docs/rag-semantic-witnesses-2026-09-07.zh-CN.md)。下一步验收矩阵的数据消耗/标签审计与封存准备，不继续针对歧义句堆提示。交付：`054427b` 已提交并推送。

- G4数据审计预注册（405f0ed）：仅核对本地历史case/query/prediction文件、原始数据checksum和现有分组锁；API0，不读取gold选择易例。Doc2Dial按conversation排除已出现分组；WixQA按问题及相关article交叉检查；MTRAG按task/conversation及query核查。存在文件只证明暴露/准备，不自动证明执行；本地未发现也不证明全局未使用。交付可复算清单、重叠计数与未确定项，本轮不改检索策略、不宣称新鲜验收完成。

- G4审计结果：278份文件/0解析失败，21份原始source checksum一致。继承旧消耗清单后Doc2Dial排除516/661对话；WixQA问题匹配15/16、相关文章匹配37/17（各200题）。MTRAG原始110对话与75/35分组已存在，纠正矩阵此前缺失记录；扫描未匹配不签发新鲜证明。2项测试通过，API0。详见[审计报告](../docs/rag-g4-data-exposure-2026-09-07.zh-CN.md)。下一步补MTRAG生产检索数据适配及完整语料/qrel核对；G4/G2保持开放，当前不调策略。交付：`f45cb41` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4 MTRAG适配预注册（9efc79c）：使用已锁定revision与conversation分组，下载官方推荐passage_level四域完整语料，预算API0/不向量化。先验证唯一ID、qrels覆盖、score语义、三种query身份及分组；官方现成passage不能冒称自定义chunk收益或精确答案span。验收为所有输入源checksum可复查、正相关qrels能关联、适配产物保留原始标注；尚不运行heldout检索。

- G4 MTRAG适配结果：四域366,438个非空passage、777查询（dev519/heldout258）、2,128条qrel精确匹配；41空片段排除ID留痕且不含正相关。现有RagDataset全量checksum/引用/分组校验通过。发现并修复JSONL owner用splitlines破坏合法Unicode分隔符的根因，保持原文不变。68个PARTIAL保留，非精确答案span；未运行检索/API。见[报告](../docs/rag-g4-mtrag-adapter-2026-09-07.zh-CN.md)。下一项为开发小样本、领域完整语料的词法基线与官方query版本配对；不动heldout、不启动微调。

- 本轮检查：RagDataset/Doc2Dial/MTRAG相关33项测试通过；完整MTRAG加载约4秒，API0。交付：`6878700` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4词法基线预注册（b2794e7）：每域从dev按固定seed哈希选择8个conversation，每组再哈希选择1题，共32题；不按答案/召回结果选题。完整官方非空领域语料、原passage不重切、仅正文、现有词法tokenizer和BM25 k1=1.2/b=.75固定；比较lastturn/questions/rewrite三种官方query，Top20固定。预算API0/embedding0；使用仅保留本批query词项的等价流式统计节省内存，须与现有BM25矩阵对照验证。测正相关passage Recall@1/5/20、MRR@20、binary nDCG@20及配对增减；不称完整答案span召回或生产Agent改写收益，不以此直接改线上策略。

- G4词法对照结果：固定32个dev conversation/四域完整366,438非空passage，lastturn/questions/rewrite三表达。Recall@20 33.07/34.38/50.26%，MRR .2130/.2095/.3168，nDCG .2142/.2265/.3329。rewrite的Recall提高8题/降低1题；仍11/32无Top20相关片段。96排名审计等4项通过，API0，统计评分约27.5秒（非在线延迟）。见[报告](../docs/rag-g4-mtrag-lexical32-2026-09-07.zh-CN.md)。这是官方query离线BM25效果，不是当前Agent或完整链收益。下一项固定32题补Dense分路前的缓存/吞吐核对；不缩小干扰语料、不动heldout或生产权重。交付：`7817523` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4 Dense预算预注册（d6dcd54）：先对四域完整语料作与gold无关的固定hash样本，每域64片段共256，测本地BGE-M3编码吞吐、显存及token长度；API0，不微调。样本仅成本/可见性诊断，不报Recall；默认完整支持上限8192，超过上限显式记录。比较512/1024输入预算的样本截断率后确定全量编码配置，生产默认不随探针变化。暂不启动366k全量编码，缓存身份须绑定模型/语料/表示/长度。

- Dense探针：固定hash256片段，512上限截断20/256，1024/8192样本无截断；batch4完整输入编码1.45秒、约176片段/秒、峰值allocated1.2GB，粗估全量35分钟，非服务SLA。全量采用8192上限，逐分片检测实际超限并失败而非静默截断。下一执行预算本地四域366,438片段一次编码、API0，每4096片段持久化身份校验缓存；复用后续融合实验。固定32题官方rewrite，Dense exact dot-product Top20与已有BM25同预算比较，附加每路100只诊断、不改正式预算。生产策略不变。

- G4 Dense全量已启动：exec session2481/PID1508735，缓存/tmp/dialogpilot-mtrag-dense-full-20260907，日志同名.log。阶段快照12,288/366,438已编码；首4096向量SHA/finite/unit-norm通过，2项性质测试通过。见[启动记录](../docs/rag-g4-mtrag-dense-start-2026-09-07.zh-CN.md)。当前没有Dense质量结果。下一轮必须先查同一live会话/PID，不能因跨轮重开重复任务；完成后同query对比BM25再融合。交付：`9036daf` 已提交并推送；推送后核查PID仍运行，已编码32,768/366,438。

- Dense运行续轮：已轮询同一session2481并核查PID1508735存活，40,960片段检查点，未重启。融合预注册：等待四域COMPLETE，固定同32题官方rewrite、每路20/融合20/k10，对照Dense权重0/.25/.5/.75/1；复用生产fuse_rankings，不调用任何模型。联合40只标诊断上界，不能与20预算并列宣称胜出；报告各域与整体Recall/MRR/nDCG、救回/误伤及融合删失。权重扫参仅开发筛选，不采用动态路由或改生产默认。

- 融合重放入口已实现，5项测试通过（同输入校验、纯路端点、预算、.25权重候选排斥见证）。全量编码仍同一PID存活，检查点73,728/366,438；未运行重放、无新质量结论。完成后命令：PYTHONPATH=. .venv/bin/python scripts/replay_mtrag_hybrid_weights.py --dense /tmp/dialogpilot-mtrag-dense-full-20260907 --lexical artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07 --output artifacts/eval/rag-g4-mtrag-hybrid32-2026-09-07。先审核全部向量/域计数再报告采用决定；生产不改。交付：`bf3ed01` 已提交并推送；编码进程保持运行。

- Dense缓存审计：同一session2481/PID1508735继续运行；新增只读审计入口默认要求COMPLETE，allow-running仅签发检查点快照。已核验106,496向量源行顺序/hash、文件SHA、float32×1024、finite与单位范数，6项错误注入/正确合同测试通过。实际进度110,592/366,438，尚无Dense成绩。产物artifacts/eval/rag-g4-mtrag-dense-checkpoint-2026-09-07/report.json明确complete_attested=false。下一轮仍先查现有任务，完成后不带allow-running执行全缓存审计，再运行已交付融合重放。交付：`8dd8cff` 已提交并推送；仍等待同一全量编码任务完成。

- Dense全量完成并审计（2026-09-08）：原PID已结束/progress COMPLETE，366,438片段一次编码约31.4分钟，API0；全缓存source/hash/shape/norm检查通过。四域最大token743/5627/606/1326，均未超过8192。18项相关测试通过。未重启丢失句柄对应任务。
- G4同预算五权重结果：官方rewrite32题、每路20/最终20/k10，当前.25 R@20=50.26%/MRR .3445/nDCG .3505；.5为63.02%/.4099/.4284，Recall提高10题/降低1题；.75为60%/.4978/.4787。当前.25候选32/32等于纯BM25，复现融合候选排斥；两路仍4题均miss。官方rewrite不保证语义完整，含糊/上下文依赖表达留待真实Context验证。详见[报告](../docs/rag-g4-mtrag-hybrid32-2026-09-08.zh-CN.md)。下一项固定.25/.5/.75候选做本地CE Top5对照，不微调、不改生产权重；全链及heldout未完成。交付：`306a468` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4精排预注册（97ef229）：固定32题及.25/.5/.75三个Top20候选，BGE-reranker-v2-m3本地FP16/batch4、完整query+正文不截断，超8192显式失败。同一query/passage pair只评一次，最多1280唯一pair（3×20×32的重复不再支付）；不复用Doc2Dial异输入分数。API0、不微调、不生成答案。比较精排Top5 Recall/MRR/nDCG、救回误伤和候选上界；只保留开发候选方案，不凭小样本切生产。

- G4本地CE完成：32题三组1920候选位置只评分1104唯一pair（节省816重复pair），API0、约10.9秒，最大padded631、不截断。精排R@5当前.25/.5/.75为42.71/47.66/50.00%；.75相对当前提高10题/降低3题，净+7.29pp；MRR两实验臂同.5255。3项测试通过、全部分数/排序零模型重放一致。见[报告](../docs/rag-g4-mtrag-rerank32-2026-09-08.zh-CN.md)。下一项固定精排结果查打包与模型可见序列化，不重算Dense/不微调/不改生产；全链和封存仍开放。交付：`e8dbb49` 已提交并推送到 origin/feat/customer-service-target-architecture。

- 用户改优先级：先查4条双路miss的query（暂停打包重放）。预注册bd571c9：读取reference.input用户/agent会话，不读取targets/contexts/gold正文；冻结4条手工query，Enterprise保留意图解释不唯一的不确定性。比较原官方query、仅去role标签控制、会话补全query；同原完整领域语料/本地模型、每路20/融合20/k10固定，复用文档向量，API0。先比较再读gold诊断，禁止依据结果反复改词。此为已暴露失败集诊断，不是总体提升或真实Agent表现。

- 四miss复测完成：原会话冻结手工query、role去标签控制、原query共12表达；API0/新query向量12/文档向量0。手工仅Enterprise一条gold Dense79→3，当前.25仍删掉，.5/.75保留。web chat1220→277且Top1与gold同URL，但标注指向home screen/start messages，用户目标含糊。诗人/银行手工补背景反把前排拉回旧话题，不能宣称补全稳定成功。1项产物审计通过，基线重现，未在读gold后再改query。见[报告](../docs/rag-g4-query-miss4-2026-09-08.zh-CN.md)。下一项是实际Context→Agent验证最新信息需求与历史背景选择；打包核查仍保留。交付：`6c7bbe2` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G2最新诉求诊断预注册（1d26f9f）：四条中文电商模拟，两条保留必要前文、两条明确切换目标；输入及期望先冻结在rag-g2-query-focus4-2026-09-08/cases.json。通过现有PG历史/context loader/manager.prepare，不手工构造Context；Flash最多6次，仅规划，不检索或生成。记录实际模型消息、查询和来源可见性，人工按意图而非词项齐全评分；公共MTRAG四例不强行通过电商范围。工作区其他任务runtime修改以源hash留痕，不混入提交。此轮不修改生产prompt，成功不代表Recall提升。

- G2最新诉求四例完成：4次Flash，实际历史4/4完整、READ知识计划4/4，明确话题切换2/2未携带旧主题。保留两项不确定性：no-reason扩成unconditional、选择企业会员后仍查双会员范围；未测其召回影响，不误称失败或全部正确。冷投影DEGRADED但消息完整，长记忆未测。1项输入/计划产物审计通过。见[报告](../docs/rag-g2-query-focus4-2026-09-08.zh-CN.md)。原manifest synthetic标记错误已注明，脚本元数据owner修正；未重跑API。下一项恢复冻结MTRAG精排→打包/可见重放，不再以四例猜词改prompt。交付：`a8a97d6` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4打包预注册（0c63de4）：固定MTRAG32题三组CE完整排名，官方passage为来源单位，max_tokens2600/final_k5；复用ContextPacker→EvidencePack→MCPToolManager知识序列化。API0/新模型评分0，校验完整原文与来源ID保真、pack/serialized Recall@5、MRR/nDCG、预算跳过与配对变化。此边界不包含工具安全guard、Agent上下文压缩和最终生成，不称实际模型已看到。采用仍须后续全链/heldout，不切生产权重。

- G4打包完成：32题×3臂，API0/新模型评分0。当前.25/.5/.75打包及知识序列化Recall@5为42.19/47.14/50.00%，MRR .4870/.5255/.5255；.75对当前提高10题/降低2题、净+7.81pp。唯一pack额外损失为govt一题在.25/.5各少一个相关passage；未调预算。96视图与源正文逐字一致，最大序列化估算3277token高于2600正文预算。1项产物审计通过。见[报告](../docs/rag-g4-mtrag-pack32-2026-09-08.zh-CN.md)。尚未覆盖guard/Agent context/答案，下一项沿该边界核查；生产不采用、heldout未运行。交付：`9934a26` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4安全/归档预注册（69ac86e）：冻结96份知识序列化视图，重放UntrustedContentGuard和领域Agent ToolResultPersistence；默认16000-1200-600输入预算的1/5=2840 token，使用该middleware实际SDK估算。API0，不伪称跑完整ToolManager/Agent。测试正常与强制归档边界，记录隔离、原文inline、引用及归档逐字恢复；DIRECT走证据事实→synthesis，不用领域middleware结果冒充DIRECT损失。

- G4安全/归档完成：96视图安全隔离0；默认2840工具预算27转读取引用/69全文inline，原文96可恢复。强制256边界96归档且可恢复。首次fixture work_item_id带句点被SDK拒绝，已在fixture修正，不改生产合同。1项实际重放测试通过，API0。见[报告](../docs/rag-g4-tool-boundary-2026-09-08.zh-CN.md)。领域Agent是否读取所需页未测；DIRECT走Fact→synthesis不套用该归档率。下一步分别捕获DIRECT合成输入和领域offload读取，避免归档恢复冒充模型可见。交付：`b7a5440` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4 DIRECT合成输入预注册（ba1d307）：冻结96份模型证据视图，以可追踪的模拟知识Fact/ResultBoard注入ResponseAssembler候选阶段，调用真实compose provider与预算校验，在模型传输边界用capture stub停止。API0；核对全部原文、来源ID和query进入实际构造消息。只证明Fact→候选合成输入转换，不证明上游真实Fact生产、模型回答、verifier或发布。结果不得计为答案成功。

- G4 DIRECT输入完成：96/96冻结视图从重建知识Fact经ResponseAssembler候选组装、真实compose provider/预算校验后完整到达传输stub；API0，1项96视图重放测试通过。首次fixture非canonical JSON被正确拒绝，修正fixture规范序列化后通过。见[报告](../docs/rag-g4-direct-input-2026-09-08.zh-CN.md)。不包含真实Fact生产、模型、核验/发布或多任务长历史；领域归档读取仍待验。下一项固定小样本Flash当前/候选答案配对，按证据支持与需求覆盖评分。交付：`d1bac07` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4答案预注册（e4fd3e3）：每域按固定hash从32题取2题共8题，两臂.25/.75，原reference.input历史/末轮问题、冻结pack证据，不发送gold/targets。真实ResponseAssembler候选及compose provider，Flash最多16调用、输出800；不跑verifier/发布。候选输入与模型消息留痕，按事实支持/需求覆盖人工逐例评分；小样本非整体或线上准确率，原公共题不冒充电商Agent规划。

- G4答案生成完成：每域2题共8题、两臂16次Flash/16份非空草稿，API任务已结束；原历史/末轮问题与真实捕获请求一致。96视图零API回归及16请求审计各1项通过。见[报告](../docs/rag-g4-answer8-2026-09-08.zh-CN.md)。没有verifier/发布、没有答案正确率结论；下一动作只读冻结答案及引用原文作逐项支持/覆盖评分，勿重复调用。当前生产不改。交付：`528b23c` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4答案来源核对：Codex非盲16草稿评审，.25为6支持/1无依据/1待复核，.75为5/2/1；不是答案准确率，覆盖另记。明确候选错误：1040EZ资料套入1040NR-EZ、qualify加强为excellent credit。引用ID均合法不代表支持，网页建议与频率概括保留复核；限制令覆盖改善但局部引用不足。API0，1项SHA/计数审计通过。见[报告](../docs/rag-g4-answer8-review-2026-09-08.zh-CN.md)。不采用权重，下一项冻结两条明确错误/最小修订检验现有verifier，未调用不得称漏检；领域读回及全链heldout仍开放。交付：`50332d0` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4核验预注册（e92a288）：冻结候选0.75税表/零息两条完整答案及本次已接收证据；每题原答案与来源支持修订一条，共4次Flash上限，调用当前AnswerVerifier，不改prompt，不重新检索/生成。区分语义UNSUPPORTED、需求未覆盖、协议失败、实际publishable；修订前后共享证据。不根据四题结果宣布核验泛化完成。

- G4核验完成：4次Flash，两个原始错误均PASS(supported/answered true,issues空)，两个支持修订均PASS；错误放行2/2、修订误拒0/2，仅已选诊断集。无API/协议失败，1项绑定/完整配对审计通过。当前claim_verification已是整体布尔+issues合同，与历史逐claim报告不同，源码身份留痕。见[报告](../docs/rag-g4-verifier4-2026-09-08.zh-CN.md)。未修复、不关单；下一项仅实验通用对象/条件强度检查对照及新控制，禁止关键词特判/直接恢复旧协议；其他RAG缺口保留。交付：`0bd7695` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4核验焦点预注册（1f7f1f4）：同四份冻结答案/证据、当前Flash/schema，只在独立实验进程追加通用对象一致/必要充分条件/限定词核查说明。4次调用上限，不含案例专名，不改production文件。采用候选门槛为原错误两条均被语义拒绝且修订均不误拒；即使通过仍须新样本，不能直接称修复或切换。

- G4焦点初结果：税表原错被UNGROUNDED拒绝，零息原错仍PASS，两修订PASS；未达门槛不采用。后续预注册：同证据/焦点prompt，关键句与支持修订各一条，共4次Flash；原问题保持，单句未答全允许answered=false，主指标只看supported，避免需求拒绝冒充语义识别。用于区分整段掩盖与单句语义判断，不替代正式验证。

- G4焦点/单句完成：新增8次Flash。完整焦点与单句均只拒绝税表，excellent credit仍supported=true；四个支持修订通过。1项旧/新请求消息与schema一致性审计通过，无API/协议错误。未达两原错都拒门槛，不采用；单句未改善不能继续归因仅段落遮蔽。见[报告](../docs/rag-g4-verifier-focus-2026-09-08.zh-CN.md)。下一项同冻结输入比较更强模型能力与成本，不再堆拆分调用，微调保持暂停。交付：`98af4f0` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4模型对照预注册（6828dba）：项目已有deepseek-v4-pro，NONE，与上一轮焦点Flash保持相同四个完整输入、system/schema、4096输出预算。4调用上限，不用单句替代，不改生产默认。报告两原错误/两修订的supported、publishable、错误、tokens和延迟；价格未核验不编费用。胜出也只构成开发候选，需新控制；不认为Pro必然更好。

- G4 Pro对照完成：4次Pro NONE，与焦点Flash完全同system/messages/schema，两者均错放1/2、修订误拒0/2；输入均27015、输出594/485，四调用中位1.802/1.841秒仅小样本观测。1项输入身份审计通过，无错误。不切模型/不采用焦点，核验未修复。见[报告](../docs/rag-g4-verifier-pro4-2026-09-08.zh-CN.md)。主线收敛：暂停两见证反复调prompt，回到锁定MTRAG heldout消耗核对与冻结候选独立检索验收；领域读回/答案风险/其他集保持开放。交付：`8bc64e4` 已提交并推送到 origin/feat/customer-service-target-architecture。

- G4独立检索预注册（0b1100d）：锁定35 heldout conversation各按hash选一题，官方rewrite、四域完整语料、BGE-M3及8192输入，每路20/最终20/k10；只比当前.25和开发候选.75，不在heldout扫参。结果文件增量扫描无heldout group匹配，范围见exposure-check，结合既有审计不签发全世界未见证明。新增query向量35，文档向量0/API0；随后固定本地CE/pack5/2600，记录Recall/MRR/nDCG及配对置信区间。小组规模只初步验收，生产不自动切换。

- G4 heldout35完成：候选Recall20 41.33→61.58%，pack/序列化Recall5 33.19→44.62%（+11.43pp，11提高/4降低，bootstrap95%[+0.95,+22.86]pp）；MRR .4619→.5700区间跨0，nDCG .3365→.4347。新增35 query向量/1212本地CE pair、文档向量0/API0。5项原回归＋1项heldout审计通过。精排对.75自身Recall有小幅下降，未据验收改策略。见[报告](../docs/rag-g4-heldout35-2026-09-08.zh-CN.md)。35组已消费；不称答案提升，不切生产。下一项汇总开发/独立检索证据并推进电商实际入口与领域读回验收，核验风险保持开放。交付：`4d04bb3` 已提交并推送。首次身份测试误用含无qrel轮次的全部task集合，补存源SHA绑定eligible清单并修正后1项通过；查询/结果未变，补充修正另行提交。

- G4电商入口预注册（10a5a46）：原模拟开发拆封否定/历史日期两例，两个独立PG库分别.25/.75，原coordinator完整链（Agent query/知识工具/Flash精排/合成/核验），每臂12次Flash上限，总24。固定source/scenario，记录实际query差异；规模用于接线回归，不能冒充大语料召回收益或新鲜电商验收。期望有依据否定与历史十二元规则；其他任务工作区源码hash留痕，未做生产策略变更。

- G4电商完整入口回归完成：每臂2例Completed/verified、核心模拟条件2/2、真实来源/工具可见/引用2/2，共16次Flash。实际包权重.25/.75已核对，日期2026-03-01及旧12元保留；query措辞不同，非纯权重因果。1项产物审计通过（首次测试只按source_id覆盖旧版，改联合checksum后通过，结果未变）。修正calibration manifest owner的full-chain case/override元数据，旧输出保留说明。见[报告](../docs/rag-g4-ecommerce-pair2-2026-09-08.zh-CN.md)。不称新鲜电商验收/不切默认；下一项领域归档读回与其他公共客服集验收。交付：`d5a0576` 已提交并推送。

- 用户要求优先查明低Recall是否来自query：零API复算已消费heldout35，两路并集（最多40，仅诊断）Recall66.20%，5/35题双路完全无gold，20/35未完整覆盖。当前35/35候选集合等于BM25，反向.75也35/35等于Dense；因此权重收益主要是切换主导路，未实现候选集合互补。固定query排除了query变化对本次+20.24pp候选收益的解释；剩余召回遗漏尚不能归因全是query。保存recall-loss-audit.json，见低召回诊断报告；不据已消费heldout继续选参。下一动作优先在开发集诊断双路遗漏及候选保留机制，领域读回/其他集保留队列，微调暂停。

- R04开发候选保留预注册：复用MTRAG32官方query、两路各20、最终20、已有BGE逐pair评分；单一候选策略为先保留两路各前5去重，再按.75加权RRF补齐20，保留项仍按同RRF排序。不扫quota、不修改query、不重跑模型/API。与既有.25/.5/.75比较候选Recall20和同本地CE Recall/MRR/nDCG5、救回误伤；门槛是超过最佳固定方案的最终CE Recall且无未解释关键误伤，达到也只进入打包验证，不部署。若失败保留原方案及失败证据，不用已消费heldout选参。

- R04 reserve5开发完成：候选Recall20 .75的60.00→62.24%，本地CE Recall5 50.00→46.35%，0提高/3降低；亦低于均衡.5的47.66%。两例正确证据仍在池但被新增项挤出CE前5，一例在候选阶段被配额挤掉。原hybrid重建完全一致、CE身份SHA核对且所有候选评分齐全。API/新模型评分0。不采用、不继续API/heldout扫参；见rag-reserve5-dev32报告和产物。下一项双路遗漏中的来源内定位诊断，领域读回/其他集仍保留。

- R02来源内定位诊断预注册：开发32题原官方query、已有Dense/BM25各20，读取锁定原始passage的完整URL作为来源身份，不按gold反推父ID。统计双路遗漏gold是否有同URL候选、首个父来源排名及来源片段数；完整URL缺失/不一致单列，最多Top3不同来源的诊断覆盖另列。API0/新模型评分0，不把URL命中当child召回或parent策略收益；若来源定位机会不足，不启动父级实验。

- R02 URL诊断完成：32题15题缺28个gold；10个有效URL中4个有同源Top20、6个无，18个缺可靠URL不可判。4个来源最佳不同父排名10/13/6/5，Top3没有；不启动基于当前Top3的父内实验。ClapNQ183408条url为同一regex，FiQA60984缺url，Govt24非法；首次非空分组报告标无效保留，源SHA与锁定manifest一致。API0，详见parent-opportunity32报告。下一项官方document/passsage映射校验及独立父级定位可行性；不靠前缀猜来源，不泛化为parent无用。

- R02官方映射预注册：下载同锁定revision的四域document_level，按官方document_id/_id匹配passage的末尾起止偏移，要求父ID实际存在且父正文[start:end]逐字等于passage正文；身份猜测不算成功。全非空语料审核成功/缺父/偏移不符，保存SHA与计数；ClapNQ官方document粒度不自动等同整篇Wikipedia。API0/模型0，先建立可靠映射再重算32开发题父级机会。

- R02官方映射完成：365323/366438非空片段通过官方父ID+严格正文转换，1115未解释（Clap225/Govt890）不入映射。Cloud72439/FiQA60984全通过；连续空格规范化解释多数offset差异，不能当原始偏移。开发28个漏gold均有验证父，仍仅4个父进Top20、0个前三，未测父级策略收益。API0；原切片/加标题中间报告保留。一次null标题修正、一次重复规范化主动中断改缓存后完成；无模型任务遗留。见document-mapping报告。下一项Cloud可靠映射下独立父定位与child对照，非直接Top3扩父。

- R02 Cloud父定位预注册：既有8个Cloud开发query、8578官方父文档全文，使用同tokenizer/BM25(k1=1.2,b=.75)，父级Top3/Top20；对照Dense和BM25 child Top20按验证父ID首次出现去重的父排名。报告父qrel宏Recall/MRR、逐题救回误伤和原遗漏passage对应父覆盖；gold只用于评分。API/embedding0。父Top3不能超过两种已有父投影中的最佳结果则不进入该方案child/生成实验；这不代表其他父检索方法无效。

- R02 Cloud父BM25结果：父Recall3 Dense-child投影50.00→独立父BM25 54.17%，2改善/1下降；父Recall20 70.83→68.75，MRR下降。web chat漏gold父到3、语言支持漏gold父到1。仅开发8题，允许下一步定位验证不采用。续预注册：固定父Top3，每父局部BM25取2片段（最多6），保留其后用原Dense Top20补齐最终20；不扩预算、不改query。记录候选passage Recall和被挤掉gold；有候选净收益才进入CE，最终采用仍需可见证据收益。

- R02 Cloud父/child完成：父Recall3 50→54.17%，父Recall20/MRR下降；局部固定每父2条+全局Dense补齐20，passage候选Recall20 55.21→61.46%，1提高/0下降。救回1be662一gold；语言支持/web chat gold父内仍23/14，目标失败未闭环。API/embedding0。见cloud-parent8报告；下一项固定候选本地CE/pack验证唯一救回及排序误伤，未采用、未运行heldout。

- R02 Cloud CE/pack预注册：固定前轮8题baseline Dense候选与parent_local候选，每臂20；同预训练BGE全片段/无截断/FP16 batch4、pack5/2600。新CE评分最多320对、API0、文档embedding0，不微调；报告候选/精排/序列化Recall、MRR/nDCG及救回误伤。只有pack净收益且解释误伤才作为开发候选，独立验证与生产接线另议。扩展实验脚本按显式首臂比较，不能把Dense baseline误标成.25权重。

- R02 Cloud CE/pack完成：193本地CE pair/最大564token，API0；pack Recall5 52.08→54.17%，1提高/1下降，MRR .5417→.5208/nDCG .5022→.4960。1be救回保留，e1b正确证据被新项挤出前5；web/语言重点miss仍在。不采用，不扩quota/付费生成/消耗heldout。5项检查通过，16视图原文一致；未覆盖Agent/答案。见cloud-parent-validation8报告。下一项回到剩余query目标与父内语义匹配归因，领域读回及跨集验收继续保留队列，微调暂停。

- R02语义来源审计预注册：仅复核语言支持/web chat两条已消费见证，固定原reference.input、两臂CE Top5与官方gold；Codex非盲来源核对，保留官方指标，不修改qrels、不由待测模型自评分。记录直接支持、仅相关、适用版本差异、用户意图歧义，来源引用绑定原文SHA。API0。目的判断指标损失是否代表语义质量损失，不生成新的整体准确率。

- R02语义复核完成：语言题为明确首轮，无query上下文缺失；parent_local第1未标注片段直接支持非英语创建dialog，第4讲Another language，目标漏gold反而讲分析笔记本语言。web两臂第1已有默认launcher入口信息，gold偏主页/建议，实际意图和经典/新版适用仍不定。纠正“官方Recall误伤=语义误伤”的过度解释；原指标/qrels全部保留。五处引文/源SHA核验、API0，Codex非盲诊断不作准确率。见miss-semantics2报告。停止针对两gold调配额/query；下一项固定证据的小规模答案支持/版本验证，领域读回及跨集验收仍开放。

- R06两题答案预注册：语言/web两条已消费诊断，冻结Cloud parent-validation8 pack的dense/parent_local两臂、原reference.input历史，真实ResponseAssembler/compose provider，Flash NONE输出800，最多4调用。无新query/retrieval/verifier，不改变prompt；主要检查非英语支持/默认入口条件/真实配置与版本未确定，来源逐项复核，不报统计显著或整体准确率。API失败/非空草稿与语义支持分列。

- R06两题答案完成：4次Flash/4非空/4引用合法，1项输入与引用审计通过。语言核心结论两臂均支持；web两臂均条件性默认入口+追问，不是用户故障已解决，经典/新版适用未核实；Dense的隐藏入口举例源于历史助手，未断言实际配置。Codex非盲复核，无准确率提升结论。parent_local不采用，停止两见证继续付费调参。见parent-answers2报告。活动下一项恢复领域Agent归档证据实际读回（既有27/96归档仅证明可恢复），之后其他公共集验收；微调暂停。

- R05领域读回预注册：复用已消费语言支持题的MTRAG32 .75长pack，默认14200可用context/2840工具阈值；真实TargetFrameworkAgent/general＋知识工具fixture（不重检索）＋InMemoryStore，Flash NONE最多12总调用、max_steps8。保留实际schema/prompt/归档/read_tool_result返回及模型请求，区分可恢复、自主读取、来源正文实际可见与答案支持。固定首轮问题，无gold输入；不假定最终完成或读全页，领域outcome review用同Flash计费纳入预算。工作区并行修改源码SHA留痕，不混入提交。

- 领域读回首轮3次Flash：2次搜索同冻结证据，主动读第一页2000字符后判发布日志并再次搜索，未翻页；实际max_steps误写4（位置参数8为timeout），导致工具5/4被限。该轮保留为fixture配置失败，不能算8步结果。修正为具名max_steps8/timeout60，新目录剩余API上限9，合计不超原12；不改生产阈值或prompt。

- R05领域读回完成诊断：首轮3次+修正5次Flash=8；修正8步下5search/2read执行，读0/2000两页均逐字进入模型请求，第二页Another language被模型识别；再申请read+search触发9/8工具上限无答案。1项请求审计通过。固定fixture不能证明真实重搜重复，InMemory非PG恢复验收。原文可恢复/自主读回/实际可见已在此例确认，任务完成未通过；未改生产预算。见domain-archive-probe报告。下一项零API知识证据导航/归档边界核查，再同预算验证，跨集验收仍开放。

- R05导航实现：归档owner支持可选evidence_id，按单条text分页附source/title，明确offset_basis；pointer列最多20条短目录且二次压缩保留。原模型视图及raw Fact复用model_evidence身份，generic读法兼容、未知ID/跨用户失败；原read_tool_result schema接入，不增工具/Agent/预算。31项零API检查通过，1项PG未运行；96视图目录低于2840且按ID原文/来源一致。见evidence-navigation报告。仅实现验证，模型收益待同8步任务复测，不关R05/全链任务。

- R05导航Flash复测预注册：复用steps8同fixture/query/system/model，max_steps8、Flash NONE1200输出、总API上限9；仅已提交导航schema/归档pointer变化，source hash对照留痕。记录search/read的evidence_id与页、实际输入、终态、答案支持/引用；旧5次失败为开发基线，单例成功不作泛化。生产步数/工具阈值不变。

- R05导航Flash复测完成：同8步领域任务由TERMINAL_FAILURE→SUCCEEDED，模型5→5/search5→3/read2→5，正文4000→8934字、输入token13564→26743、输出673→1294；不称降本或全链准确率。五条证据页均实际进入模型请求，核心非英语答案有来源；部分日志只读首段，未要求全包。11项检查通过，新API5。只有导航两文件源hash变化；固定fixture/已消费单例，泛化未验。见evidence-navigation-flash报告。下一项不同任务导航/预算泛化，其他公共集与RAG缺口保持开放。

- R05不同任务预注册：既有Cloud弱理解1be662与预标注ddbbbe两题，原reference.input历史及末轮问题，冻结.75 pack；各8步/9API上限，总18。真实领域Agent导航，无新检索/改写；这些是已消费开发题上的新归档轨迹，不叫新鲜数据验收。统计终态、读取原文可见及答案依据，失败保留，不调prompt。

- R05不同任务完成：弱理解4次Flash/1search/5read→SUCCEEDED，定义及classic范围受来源支持；预标注3次/2search/5read后申请两尾页，触发9/8工具限制无答案。10页原文/来源实际模型可见，3项累计轨迹审计通过。新API7；未做这两题旧分页配对、不报成功率提升。预标注继续读取是有理由补条件，不能当无效补搜。下一项零API目录参数/页预算核对，不直接增加步数，其他集和RAG缺口开放。见navigation-additional2报告。

- R05分页参数核对/实现：归档唯一MAX_RESULT_PAGE_CHARS=4000同时约束服务与工具Schema；目录按证据长度给出reference/evidence_id/offset0/limit=min(length,cap)可执行参数，默认2000及8步不变。96视图466次证据出现全读理论620→468页，非模型节省；预标注长度2059/1674/1785/2178/2038，8→5页。32检查通过（首次测试误读取含runtime的内部schema，改实际tool_call_schema后通过），1PG未跑。续预注册同预标注query/历史/fixture，max_steps8，Flash最多9次，只验证参数使用与来源/终态，不以单例签发可靠性。

- R05预算所有者复核：零API重放96视图，27份超过单工具2840阈值；三条已消费真实领域轨迹首次知识结果请求替换完整证据，SDK重建估算7367/5100/8314均低于14200。两份并行搜索证据仍保留两份。证明这些请求无需因总预算提前归档，不证明答案正确。报告 `docs/rag-evidence-budget-boundary-2026-09-08.zh-CN.md`，可复现脚本与逐条产物同名。下一步优先上下文准入/归档所有者的统一决策，包含并行批次与超预算异常；本轮未变生产逻辑。旧fixture缺case_id的首次脚本错误已修正。本次未新增API，未关闭R05或RAG。

- R05工具批次准入实现（整体仍开放）：ToolResultPersistence只保存完整原文/artifact并返回完整正文；ContextCompaction在已知整个批次、固定任务和overhead后，仅在受保护后缀确实超预算时把已归档大结果换为可读指针，保留最新tool调用/结果配对和最终预算错误。未截断证据句子。更新所有构造调用、stateful输出预算fixture及当前重放脚本，旧报告保持历史口径。45项相关测试通过、5项PG测试未跑；stateful工具输出fixture四断言通过。96视图安全检查零拦截、持久化后96份完整内联且可恢复（仅这一边界，不代表全链）。API0。历史verified_facts注入的固定比例归档仍待共同修复，实际模型答案未复测，因此不关闭预算根因项；最新未验证分页参数改动不计入本次交付。

- R05历史事实准入实现：移除verified_facts单条available//5归档；先构造完整任务，计入实际system/tools开销，再按总预算决定是否将事实换为可恢复指针。原Fact内容/来源不变；原有历史裁剪仍由ContextBudgetManager负责，工作消息由ContextCompaction负责。不可缩减任务仍抛ModelContextBudgetExceeded，归档失败沿既有typed路径处理。48项相关测试通过、5项PG未跑；首次新增fixture未使用canonical JSON导致3项失败，修正fixture后通过。新增API0。两处提前归档实现已处理，但尚未以新鲜模型轨迹验证最终质量，整体R05/RAG仍开放。下一项真实统一入口小批验收的输入冻结与链路记录，不恢复单例翻页付费调试。

- R01/R05/R06真实入口烟测预注册：先复用已消费中文电商opened-negation/historical-policy两条开发回归，当前HEAD f44e603工作区源码SHA留档，独立测试数据库。使用run_rag_tool_calibration的full-chain路径，真实Conversation Agent/PG历史/知识handler/本地BGE-M3检索/listwise Flash精排/生成核验发布；不固定query或候选。当前默认.25/.75、每路20/最终20/pack5/2600不变；Flash NONE，总API硬上限12，SDK重试0，首次终态不盲重试。仅校验入口接线、实际查询与来源/条件/回答；这两题原来已能完成，不能用它们证明归档修复收益。预算问题的长证据真实入口覆盖仍需后续案例，不回到固定pack单例翻页。报告完成和核验状态与独立答案支持分列，失败保留；API不做权重扫参。

- 真实入口两题烟测完成：8次Flash（每题4），两题Completed/verified，原文位置及引用审计均通过；历史政策十二元与拆封非质量不适用结论获测试政策支持。保存实际请求回查两题各5片段均在捕获请求逐字出现。拆封题末尾重复追问商品问题，属于已知否定后的冗余追问，单列质量问题；不能把verified=2说成全面正确率。真实query由Agent产生，首题省略显式“非质量”但查询无理由条件并取到对应政策，不按词缺失直接判检索失败。仅已消费中文模拟2题，没有隔离证明预算修复收益，不报Recall/nDCG或提升。独立测试库运行正常终止，产物rag-current-entry2-2026-09-08，输入/源码hash及运行脚本保存；下一项扩大开发入口案例覆盖长证据和多条件，预算预注册后执行，不针对本题追加付费prompt调参。

- R01/R06多条件开发4题预注册：已知合成政策的新问法，边界日期+加急运费、欧洲第十天、定制非质量、手册地址+仓库签收+审核到账。不是新鲜heldout。固定现有语料/默认.25融合/listwise Flash/真实入口，本地embedding；不预写query，API总硬上限24，SDK零重试。记录每条必要结论、来源、调用与实际走过路径，尤其长手册相关问题不自动等于领域归档路径被覆盖。当前runner增显式输出/案例/API预算参数，原默认可复现。过关仅允许进入更广开发集准备；失败保留并按query/candidate/可见证据/生成分类，不因单例直接换模型或改prompt。

- 多条件4题完成：13次Flash，前三题各4次Completed/verified；边界日十八元且加急不报销、欧洲第十天仍在十四日期限内、定制非质量例外核心结论符合模拟政策。三题各5条source区间精确核对通过。长手册题1次调用后Failed，尚未执行knowledge_search；发送Schema明确required result，捕获SDK原始AIMessage里的submit_turn_plan args就是{}，不是校验器丢字段。未捕获原HTTP，不能判定供应商与SDK哪端产生空参数，不能解释为query智力或召回不行。未自动重跑；完整失败轨迹保留。产物rag-entry-multicondition4-2026-09-08，整体3完成/1协议失败不是检索Recall，也没有证明预算修复收益。下一项先零API梳理已有空工具参数失败的共有调用合同/模型返回模式，再决定有界协议复测；不将后续检索调参建立在此未进入检索的案例上。

- 空参数边界诊断预注册：最近6条planner同schema SHA前缀5fd8f96f12、max_tokens800；五成功一空参数，失败output129/stop tool_use非长度终止。安装版extract_tool_calls直接复制block.input。允许仅一次冻结失败输入的Flash NONE调用，SDK重试0，httpx事件钩子只保存响应JSON的工具内容/usage/stop（不保存认证头或thinking），对照SDK args；不跑RAG/不执行业务/不自动重试。成功不能证明偶发问题消失；响应仍空才可将该次空参数定位到SDK上游接口响应。

- 空计划传输诊断完成：6条近期planner请求schema完全相同、max_tokens800，失败129输出/stop tool_use；本地SDK extract_tool_calls保持input。冻结失败输入单次HTTP复测返回合法result/general_qa/resolved_query，接口body与SDK args一致，四项问题保留。实际API1，无重试；首次诊断装配用了httpx而安装版Anthropic要求httpx2，网络前失败，换为SDK要求客户端后成功。原失败未有HTTPbody，不能追认原始供应商原因；此复测说明同上下文可成功，不证明问题修复或成功率。未改生产校验/模型/prompt。后续回到该长手册题的真实检索链路验收，偶发计划协议错误单列，不循环复测planner。

- 长手册真实入口续验预注册：仅manual-address-arrival原上下文，独立测试库，原模型/语料/预算/默认融合不变；不注入上轮成功query，真实Agent再次规划。API总上限8，SDK重试0，保存第一次终态，不循环跑至成功。原四题中的计划失败仍保留，不能以续验成功覆盖。核查地址/签收/审核/到账四项支持及实际经过的工具路径；若未走领域归档路径，明确不代表R05模型侧验收。完成后结束此单例，汇总当前开发证据并回到可量化召回主线。

- 长手册续验完成：4次Flash，Completed，实际DIRECT知识问答，一次knowledge_search取到所需四主题；不证明领域归档路径收益。5条来源区间核对通过，存在引用E38用“不被拒绝”支持“不通过”的不匹配，虽别的手册片段可支持部分结论也不能替换该引用。当前6个不同开发问题首轮5完成/1规划失败；加一次续验共7执行6完成，完整链25调用+独立传输诊断1，不写6/6首轮成功。汇总docs/rag-current-entry-status-2026-09-08.zh-CN.md。停止这六题追加付费单例调试；活动下一项统一每层候选/证据/qrel可计算性审计，补齐缺失评估捕获后回到固定候选方案同预算配对。引用语义误判保留R06，不靠重跑刷分。

- R10阶段可重放性审计完成（0API）：7次完整入口执行中6次完成，均可从listwise输入短ID顺序+trace source_ranks恢复20候选、完整精排排列和5条pack；逐项核对30条pack文本与精排输入相同且都出现在无tools的生成请求，特意排除“只在verifier输入出现”假阳性。1次规划失败无检索记录。新脚本audit_rag_stage_replay.py及rag-stage-replay-audit产物保留映射/hash。缺口是融合前两路完整池、被pack丢弃候选的完整来源定位、合成完整入口独立qrels；不能直接离线重放实际Agent查询的其他融合权重。生产source已有capture_source_rankings_async，不需要新生产框架；下一步在隔离评估入口复用该能力保存实际请求的分路全集，或在评估子类记录本次_collect_sources，优先避免重复模型调用。补齐捕获前不增加付费完整链路样本。已有MTRAG分路产物仍可直接复用，保持其官方指标定义。

- R10分路捕获实现/预注册：评估专用RecordedKnowledgeSource继承现有source，在同一次_collect_sources保存完整ranks/weights，返回原_search结果不变；额外来源投影读单列projection_ms，不能当生产延迟。thread-local隔离并发请求，失败保存typed结果，无第二次embedding/search。接入校准脚本finally持久化压缩captures。3项零API测试通过（并发隔离、重建融合、typed失败）。下一步隔离PG basic candidate-scope-probe，API预算0/不生成、不精排，检验每个成功捕获的两路全集能精确重放当前候选ID顺序及来源一致性，不据此报质量提升。

- R10捕获PG验证完成：basic40、applicability24、combined40次搜索均来源核对及融合ID顺序精确重放。前两套独立小语料没有被截候选，不能证明全集捕获；补用ecommerce-full组合语料后40/40有额外候选，共182次被截候选出现，全部保留。三轮API0；是搜索次数（含scoped/omitted），不是104个不同问题。3项并发/类型失败测试再次通过。校准CLI此前拒绝显式0预算，首次两次启动在网络前终止（第一次文本替换未命中已纠正），现在仅candidate-scope-probe允许0，其客户端原已硬限制0。新增来源投影读开销单列，不报生产延迟；生产检索策略不变。代码/三个隔离库产物待本次相干提交。下一步复用已有分路全集与开发标注做固定权重重放，按已选策略验证最终可见证据；不再次盲跑完整生成链。

- 固定权重重放预注册：组合语料20个合成开发问题，仅取每题真实scoped捕获（排除omitted重复），复用Dense/BM25各20及正文来源，最终chunk20、RRF k10，比较Dense权重0/.25/.5/.75/1。使用原synthetic_development证据span与已保存manifest正文校验；报告chunk预算下span Recall/完整覆盖，以及按首次出现去重source文档列表的MRR/nDCG@20，明确两种单位。零API/embedding/精排，不用此小语料选生产默认；仅验证重放与发现明显误伤。新的最终答案或精排收益不从这些指标推算。

- 固定权重重放完成：20个模拟开发scoped问题，Dense0/.25/.5/.75/1的完整span覆盖20/20、20/20、20/20、19/20、19/20；文档MRR1/.9667/.9300/.9167/.9167，nDCG1/.975/.946/.925/.925。`.75`救回0丢失1，另1题排名降；audio-defect的gold BM25第1而Dense前20无，偏向Dense时弱路独有候选被截。不能据此推断答案错或BM25全局最好。与MTRAG方向不同，生产默认不变，也不训练动态权重。原语料hash、所有源span及当前融合顺序复核通过；首次投影字段名不匹配修正后运行成功。API/embedding/精排0。报告docs/rag-recorded-weight-replay-2026-09-08.zh-CN.md及产物。后续保留该组为跨数据回归，下一项公共开发数据同预算候选→最终可见验证，禁止仅以候选指标选择生产策略。

- 公共证据→上下文准入预注册：复用MTRAG32三权重96份pack及官方input历史；通过当前TargetFrameworkAgent任务构造、实际工具Schema开销、持久化和ContextCompaction进行零LLM重放，14200可用预算不变。query/pack冻结，不让模型重新规划，空响应本地fake不允许假造summary成功。报告完整inline数及原qrel下可见Recall/MRR/nDCG；预算/summary失败单列，不能当答案错误。目的确认原pack收益在新上下文边界是否保留，不跑新策略/训练/API。

- MTRAG上下文重放完成：32题×3配置96份pack全部完整inline、0准入错误；含官方input历史、实际任务/工具schema开销，14200预算不变。原qrel可见Recall@5 .421875/.471354/.5，MRR .486979/.525521/.525521，nDCG .407716/.444363/.458315，和原pack结果完全一致。这是已有检索/精排收益穿过当前上下文边界的验证，不是新模型答案收益，也不是模型实际回答。初次at5需要set而传list的评分脚本错误已修正；无API/新模型评分。产物rag-mtrag-context-admission-2026-09-08。下一项固定均衡.5作为跨数据候选，在中文20开发集补同本地CE与pack重放，与已有MTRAG结果并列；生产默认保持.25，微调/动态权重/新chunk策略不启动，未通过本地筛选不增加Flash费用。

- 中文均衡权重CE/pack预注册：20个scoped开发query，已保存两路各20，最终20，比较.25与.5，k10不变。本地预训练bge-reranker-v2-m3当前LocalKnowledgeReranker输入格式（title+content、不截断、FP16 batch4），每题两臂候选并集仅打分一次，最多800pair，无微调/API。按同一分数表恢复各臂tie输入顺序；pack5/2600固定，报告CE与pack span完整覆盖、源文档MRR/nDCG及救回误伤，sourcehash/modelidentity保存。中文结果为开发跨数据回归，不与MTRAG分数直接混算，不代表Flash精排或最终答案收益。

- 中文均衡融合本地验证完成：20题，两臂各20候选位置上限800，实际唯一pair441；同分数恢复.25/.5精排，CE Top5及pack5/2600完整span均20/20，docMRR/nDCG均1，救回0误伤0。441分数有限、pack/CE/scored ID集合关系核对通过。无API/embedding/微调。不能外推Flash或答案；MTRAG旧输入是passage原text，本轮当前本地端口是title+content，跨数据绝对分数不混算。生产默认保持.25。报告docs/rag-balanced-local20-2026-09-08.zh-CN.md。下一项第三类WixQA本地快照/已消费分组审计，固定.25/.5与一致精排格式做公共补验；先核对可用标注再运行，不将旧数据包装fresh。

- WixQA冻结准备完成：刷新318份本地记录、0解析失败、21来源checksum通过；ExpertWritten/Simulated重合计数仍15/16题、37/17相关article。锁定6221篇完整语料与两类各200题（revision d662dc4）。将400题按共享article的传递连通关系分成257组，连通传播排除55题；确定性选dev20/heldout20，各10 ExpertWritten+10 Simulated、每连通组仅1题，两组无共享article。仅保留行索引/组/来源ID，heldout未评分；仍freshness_attested=false，不能证明其他机器或已删除记录未用。2项传递/去重分组测试通过，原始3文件SHA再次核对。方案已固定.25/.5、两路20、候选20、pack5/2600、本地title+content CE，WixQA只报article qrel指标，不伪造span召回。下一步用完整6221篇语料构建可缓存本地索引，先跑dev20，无API；heldout继续封存，不根据结果反选分组。

- WixQA全量索引预注册：锁定6221篇原文全部参与，单一fixed512/64（text源精确保留，当前阶段不扫chunk策略）；使用当前生产LocalBGEM3EmbeddingProvider的title+content、L2归一化，不换FlagEmbedding通道。逐片段源区间核对，模型实际tokenizer检查不截断，128条一分片、batch16，source/model/tokenizer/code/library hash绑定缓存，锁文件避免并发重复构建。先准备统计再encode；无API，不载入heldout问题或答案，索引可复用于dev/heldout。新脚本prepare_wixqa_local_index.py，缓存/tmp/dialogpilot-wixqa-full-index-20260908。

- WixQA索引构建进行中：6221/6221篇产生11167个fixed512/64片段；独立逐source复核全部区间精确，非空白内容无缺口。生产本地BGE向量化已启动，exec session 98191当前确认存活，32个原子分片/4096行已完成，已见最大模型输入719tokens（无截断），COMPLETE标记尚不存在。API0，验收问题未评分。脚本/来源检查先提交，向量缓存保留/tmp/dialogpilot-wixqa-full-index-20260908，模型/源码/library/input与输出hash绑定；后续续接同会话或核对COMPLETE，不因观察超时重新启动。完成后以冻结dev20运行本地Dense+BM25及.25/.5精排打包对照。

- WixQA固定开发20完成：全量11167向量/88分片COMPLETE存在，输入输出SHA、shape/有限/L2及模型与源码身份复核通过。官方query、本地Dense/BM25两路20、融合20、当前title+content本地CE/pack5/2600，API0、553唯一CE pair。Dense .25→.5：候选article Recall50.83→61.67%，CE/pack40.83→51.67%，pack完整article集合6→9/20，MRR .4042→.5083，nDCG .3622→.4623；4提高2下降。不是span或答案正确率，不是PG性能。均衡剩余gold出现次数：双路miss7、融合丢4、CE丢3、pack丢0；四种评分边界检查及实际指标[0,1]通过。报告docs/rag-wixqa-fixed-dev20-2026-09-08.zh-CN.md、产物wixqa-fixed-dev20-2026-09-08，脚本run_wixqa_fixed_comparison.py。封存未评分、默认不变；下一项来源复核两条误伤/候选遗漏后冻结同配置封存对照，最终真实Agent答案仍待验收。本次相干脚本/报告待commit push，不混入其他工作区文件。

- WixQA开发退步复核（API0）：地理语言题的Managing Languages只在BM25第13，均衡融合时被挤出；两臂都保留浏览器语言和Enterprise Routing，后者有地区路由但属于Enterprise范围，不能把gold损失直接叫答案错误。购物车/售出统计题gold Stores Reports两臂都进候选，新增候选将其挤出CE Top5；此为精排阶段排名竞争，非query变化或fusion漏。原指标不改，不追加针对性策略。dc2cfe3已push。
- WixQA封存20预注册：使用既有connected manifest的heldout20，不重选、不调参；原文问题、全量6221/11167、每路20/融合20/k10、.25对.5、同title+content本地CE/pack5/2600。最多800唯一CE pair、本地query embedding20、外部API0。对照article Recall/完整集合/MRR/nDCG、配对救回误伤；预设只有pack平均Recall正增且MRR/nDCG不下降才进入真实入口候选，不凭20题批准全局默认或动态权重。记录bootstrap区间，区间跨0则收益稳定性未证实。现有脚本仅增加split参数，排序/评分不改。封存运行后视为已消费，禁止以失败重选样本。

- WixQA封存20完成：同固定配置、本地query向量20/550CE pair，API0；候选article Recall55→75%，pack55→67.5%，完整集合10→12/20，MRR .4958→.5142、nDCG .4939→.5330；候选5改善0退步、pack4改善1退步。独立重算所有阶段指标和ID集合通过。配对bootstrap10000/seed20260908，pack Recall差95%区间[-5,32.5]pp，MRR/nDCG同跨0；平均正收益支持进入真实入口候选，稳定总体收益未证实，不改默认。封存20现已消费，不再称未见；未生成答案、不代表span覆盖或PG性能。报告docs/rag-wixqa-fixed-heldout20-2026-09-08.zh-CN.md，产物wixqa-fixed-heldout20-2026-09-08；开发退步源区间/正文也保存regression-source-audit.json.gz。下一项真实Conversation Agent查询→Flash精排/答案的小批固定预算对照准备，不新增检索策略或微调。本轮待相干提交推送。

- 真实入口权重配对预注册：既有中文模拟boundary-express/custom-not-quality两条多轮回归，固定原history/message、ecommerce-full全部语料，真实Conversation Agent自行query；.25与.5各独立测试数据库一次、每臂Flash NONE硬上限12/SDK零重试，合计不超24。查询若不同单列，不能将答案差值全归因融合。保持listwise精排、生成核验及预算；不重跑到成功。真实入口runner仅新增显式权重选项和记录，默认不变。检查实际注入policy、query/来源/条件/最终引用支持及API次数。此为接线与电商开发回归，不是WixQA端到端验收，也不证明总体收益。公共全语料接入当前PG评估入口仍需单独处理，禁止缩小公共语料冒充全库。

- 真实入口权重2题配对完成：两臂各8 Flash，共16（预算24），均2/2 Completed。源码hash完全相同，实际pack策略字段.25/.5正确，20条来源区间与checksum及答案引用ID通过；核心十八元/加急不报销/定制例外有来源。额外质量售后条件有依据但非必要。实际Agent query不同（均衡臂自加英文），因此只证接线及已消费中文开发回归无核心退步，不报融合因果收益或盲测准确率。产物rag-real-weight-pair2-2026-09-08、报告同名、审计脚本audit_rag_real_weight_pair.py；默认不变。下一项公共WixQA真实PG入口准备：现有校准仅模拟语料且默认structure-aware，与离线fixed512/64不同，须显式同切块/全文语料/检索文本身份+缓存向量复用，零API核对分路后才付费。停止用更多模拟单例替代公共端到端。本轮相干提交推送待收尾。

- WixQA全量PG预检（API/模型0，无DB写入）：生产SourceDocument/SourceRevision/_chunks显式fixed512/64，6221篇11167片段检索文本/区间全部匹配已有缓存；25个256篇批次均过来源及chunk预算，每批120～506。当前import_documents合并current后对全代使用max_chunks_per_batch=4096，累计校验可复现typed失败11167>4096。源码显示来源预算用incoming而chunk预算用全代，且embedding token预算只有定义未执行。不是实际PG导入失败；现阶段未写生产修复或绕过上限。报告docs/rag-wixqa-ingest-boundary-2026-09-08.zh-CN.md，预检脚本/产物wixqa-ingest-preflight-2026-09-08。活动项转为导入owner的批次/全代预算合同及原子失败验证，原因是其阻塞完整公共语料而非新策略实验。修复后缓存接线→PG分路等价→真实Agent/Flash；默认权重不变，微调暂停。5708816已push，本轮预检待相干交付。

- 导入预算owner修复：chunk检查集合改为本次imported(source_id,revision_id)投影，与来源输入预算对齐，累计generation保留历史；配置4096不变。模型身份变更/缓存缺失需要全代重建时，missing向量按既有chunk批大小调用provider，全部获得后才沿原注册/投影/激活路径发布。首轮真实隔离PG13测试通过；补充累计超限后的修订、typed embedding失败断言并连同source projection/budget测试复验中。未执行全量WixQA实际PG导入，未改token配置执行或全代构建性能合同；后续向量缓存接入+全量导入仍待做。报告docs/rag-ingest-batch-repair-2026-09-08.zh-CN.md，本轮API0，微调暂停。
- 导入修复复验完成：tests/test_postgres_knowledge_store.py、test_cost_budget.py、test_knowledge_source_postgres.py共16通过（20.80s），全部所需PG测试实际运行于新建隔离库，无skip；typed失败/旧代保留/累计增长/修订与缓存重建均覆盖。产物pytest.txt保存，准备提交推送本次owner改动与文档，不混入其他工作区变更。下一项复用WixQA缓存向量导入全量PG，不能把本次16测试称全库导入已完成。

- 用户明确要求实际全量导入：新建独立测试库dialogpilot_wixqa_eval_20260908（55432，保留供后续真实入口），生产store固定512/64，25批各最多256源。逐文本绑定已验证本地向量缓存，任意未命中文本失败，不调用模型或API；原文SHA/分片SHA/输入hash/shape/L2校验。仅导入正文与向量，不导入测试答案。完成校验6221源/11167投影及原位置文本一致，记录每批已激活代，现有生产默认和线上库不变。脚本import_wixqa_cached_postgres.py，新产物wixqa-postgres-import-2026-09-08。

- WixQA全量PG实际导入完成：session57184正常exit0，25批/6221源/11167片段，590.71秒，最终knowledge-generation-95a4fede15af901f0c3c20e2727070f0 ACTIVE。独立保留库dialogpilot_wixqa_eval_20260908@55432，tenant wixqa-eval；原位置+检索文本11167逐条一致，缓存向量服务恰11167，新增embedding/API0。累计4096以上继续成功，生产store/manifest/projector/HNSW/activation全部实际执行，不绕过预算。报告docs/rag-wixqa-postgres-import-2026-09-08.zh-CN.md，脚本import_wixqa_cached_postgres.py、database/batches/report产物。当前全代重建造成后期批次变慢，不当作在线延迟。下一步直接复用此库做PG分路核对→公共真实Agent/Flash，禁止重复建库/重新embedding；整体RAG未关闭。本次交付待commit/push。

- PG分路核对预注册：复用完整保留WixQA评测库与开发20原问题/已缓存query vectors；真实PostgresKnowledgeCandidateSource.capture_source_rankings_async每路20，统一en/public与既有generation，无metadata缩库、无模型/API。逐来源ID+原文区间映射离线chunk ID，比较两路Top20集合及顺序；排名差异先归因，不根据PG结果改变权重。产物wixqa-pg-routes-dev20-2026-09-08，脚本compare_wixqa_postgres_routes.py。后续使用实际PG候选分析遗漏与精排，不把离线结果直接当线上结果。

- PG分路20核对完成：完整第二轮9成功11 POSTGRES_UNAVAILABLE，9成功两路Top20集合/顺序均与离线相同；不写20/20等价、不把后端错误作为query或召回语义失败。Docker日志SQL statement timeout，对应BM25 scoped/unnest CTE；默认pool750ms/source3s。首次缺policy字段/embedding identity装配在搜索前失败，修正；首轮2成功后断言终止保留-interrupted，第二轮记录typed失败并遍历20，API/embedding0。报告docs/rag-wixqa-pg-routes-2026-09-08.zh-CN.md。优先项变为BM25执行计划/性能诊断与原预算可用率，原query/权重/语料不改；尚不能归因具体耗时节点，下一步EXPLAIN，不直接放宽预算或付费生成。

- BM25计划/相干SQL优化：诊断首个超时题原EXPLAIN1001ms/2149tempblocks；数组计数18031ms坏连接计划拒绝，局部聚合822/提前TopK783/窄scope+延后来源896ms，单次有波动不作稳定耗时提升。保留最终按片段聚合tf、原scope统计df/dl、TopK后按PK取来源；公式/过滤/排序语义不变，所有诊断变体ID/score一致。原750ms真实20题基线9成功→中间18→最终19，成功项两路排名全与离线同；仍1超时，不关闭性能/总体RAG。真实PG7测试通过（生成标量oracle/scope/顺序/重复词等），API0/新embedding0。报告docs/rag-wixqa-bm25-plan-2026-09-08.zh-CN.md及plan/两轮产物。下一项剩余执行成本诊断并扩验证，不刷重跑20/20、不改超时、不付费生成。

- BM25剩余见证诊断：family setting题当前EXPLAIN929ms、77025词频行、2489临时写块。改为scoped查询内部ordinal整数聚合，最终恢复candidate_id排序/来源，外部身份不变；EXPLAIN811ms/980块。开发20原750ms预算一次20成功，两路均与离线顺序相同；同步7项PG公式/隔离测试通过（该轮存在测试并发，不用于严谨延迟估计）。当前实验先口头说明后补记此条，未事先文件预注册，明确流程不足，不包装成预注册结论。
- 扩展验证预注册：已用于离线权重验收、尚未在PG本次SQL执行的heldout20，固定现有compact SQL/原query缓存/全库/750ms，不调权重或数据，API0/新embedding0。一次遍历，typed失败保留；全部成功且两路结果等价才进入公共真实入口准备，否则继续记录可用性缺口，不重跑凑分。
- compact扩验证完成：固定heldout20一次20成功、两路Top20顺序全与离线一致，开发20+扩20共40/40；默认750ms/3秒未改、API/新embedding0。此前失败均保留，不宣称上线可靠性/答案正确率/并发SLA。内部ordinal不外露且TopK按原candidate_id tie排序；见证原score/ID复核等价，真实PG7项通过。报告docs/rag-wixqa-bm25-compact-2026-09-08.zh-CN.md与compact两组产物。下一项恢复公共真实Agent/Flash小批准备，复用保留PG库，不重复导入或新增策略；性能代表性负载仍开放。本轮相干交付待commit/push。

- 公共真实入口接线根因修复：api._retrieve_knowledge硬编码zh-CN，与WixQA en manifest不符；store.collection_scope(generation)从tenant/backend/gen不可变manifest读取locale/product并验证hash/public，统一API/Agent/预检索用该范围，不让模型猜。清单冲突typed拒绝；空product依原合同规范None。run_full_chain仅增加tenant_id/scope_label参数，旧默认不变，供wixqa-eval真实链复用。47测试通过含PG（初次fixture误期望空字符串，修正既有规范化预期），API0；报告docs/rag-collection-scope-2026-09-08.zh-CN.md，产物pytest。公共实际答案仍待运行，下一项固定少量官方问题/Flash预算，不重复导入、不替换模拟。f82ffb2已push，本次相干交付待提交。

- 公共真实入口2题预注册：按冻结dev manifest顺序前2题，不按收益挑题，无历史原问题/不注入qrel或答案。复用wixqa-eval完整6221篇/11167库，真实Conversation Agent自行query，Flash NONE精排/生成/核验，.25/.5两臂各12 API硬上限合计24、SDK0重试。只初次终态，失败保留；不同conv ID隔离，collection_scope断言en，禁止导入或重新向量化文档。记录分路/来源/实际query/答案/引用与调用，query不同不做纯权重因果推断；2题仅接线和语义诊断，不作总体准确率。脚本run_wixqa_real_entry_pair.py，产物wixqa-real-entry-pair2-2026-09-08。

- 公共真实入口首臂完成但未进RAG：冻结前2题Flash各1调用，共2，两题OUT_OF_SCOPE/0knowledge_search，运行时Completed回复补充信息，非正确回答；第二臂暂停，未花满24。实际阶段在planner范围判断，默认电商能力目录与公共Wix适配待核；general_qa含通用FAQ，不能断言业务描述就是唯一根因，下一项读取实际payload零API定界。runner未到达精排的装配缺口也修正：复用ToolManagerRerankerAdapter而非直接ResultReranker，未产生该分支调用/费用，不称模型已验。报告docs/rag-wixqa-real-entry-scope-2026-09-08.zh-CN.md与scope-audit；不以强制plan代替真实Agent，不扩电商生产范围刷benchmark，公共答案/权重对照仍未完成。

- 范围输入零API复核：原planner实际domain general为“Resolve general ecommerce service objectives”，knowledge_filter_contract仅unconfigured sales channels，无Wix知识范围；原message完整、输出合法out_of_scope、47输出token，不是字段丢失。评测适配预注册：同2问题/全部语料/模型预算，只为wixqa-eval新bundle版本配置general能力描述为Wix Help Center支持（编辑/设置/计费/流程，无执行网站动作），不改生产默认、不写答案或强制plan。run_full_chain允许注入已校验tenant的registry，默认保持。新scoped目录两臂各12调用，原2次范围拒绝独立保留；本次是评测领域适配而非声称生产模型修复。
- scoped首轮在模型前失败：中文电商encoder artifact只绑定customer-service-v1，新Wix bundle触发stale version，API0，轨迹保留。公共评测显式使用现有TARGET_ENCODER_ENABLED=false配置跳过不适用的中文分类器，由同Conversation Agent规划，两臂一致；不修改encoder artifact或生产默认，不把此适配当模型优化。新v2输出/conv隔离，仍各12调用预算，原2次范围拒绝另计。

- WixQA真实入口scoped v2两臂完成：各2/2进入knowledge_search并回答，各8 Flash共16（另原范围拒绝2、encoder前失败0）。两臂候选article Recall均100%、pack均75%、all-articles均1/2，无本次提升。计费gold退款政策候选.25第3/.5第8和13，却均未进pack；损失位于候选之后，不能归因query或继续扩池。原文偏移/checksum与实际引用ID全通过，不等于语义正确；计费query两臂不同，不作权重纯因果结论。报告docs/rag-wixqa-real-entry-pair-2026-09-08.zh-CN.md、v2轨迹/audit；默认.25、微调暂停。下一活动项R02/R10：零API查看现有精排/打包及参考答案的必要信息，先确定丢失机制；未预设新策略。准备相干commit/push，整体未关闭。

- 94e17eb已push。后续零API因果复核完成：退款政策Flash排6与7/8，pack严格前5，完整20ID/正文一致，无预算/去重丢弃；audit_wixqa_rerank_loss.py及rerank-loss-audit.json可复现。但参考答案不含退款规则，所需内容可由已保留FAQ支持；自动续订条件实际ToolMessage可见，两臂答案均未提，属于相对参考的信息遗漏，不直接断言宽泛问题必须覆盖所有细节。修正诊断：article qrel损失不能替代答案支持/完整性判断，暂不采用强制文档多样性或扩大K。下一项R10评估口径：对已消费开发题区分明确需求、参考要点、可选扩展及来源支持，再做小量答案比较；本次新API0。诊断脚本/报告相干交付，整体仍开放。

- 1b3e533已push。R10答案口径开发诊断完成：按两题参考建立9点作者事后标注，核心5点两臂5/5；参考细节4点为0/4→3/4，非官方标签/盲评/准确率或权重因果收益。逐项绑定答案hash、片语及模型可见证据ID，零API脚本score_wixqa_reference_points.py审计通过。保留article指标，不用改口径美化召回；下一步后续小批题在生成前标注核心与参考要点，再做匿名答案比较，必要信息不可见才回溯检索。尚缺独立语义复核和代表性端到端规模，整体开放，默认.25/微调暂停。本轮相干提交推送。

- b771afa已push。R10下一2题预注册：冻结dev顺序3/4（section高度、booking类别管理），不是新heldout；先保存参考要点2+7及source hashes，再实际生成，两臂各12 Flash NONE/SDK0/全库/20候选5片段2600预算，同Wix bundle encoder关闭。按要点完整/部分/未覆盖、额外无依据内容和article Recall分别评分；宽泛类别管理允许合理范围答复，不把全部7项当用户硬要求。preregistered-points.json，runner仅新增offset/output，默认不变。不根据结果重跑，查询不同单列；2题不足采用新权重。

- 后续2题两臂完成，各8 Flash共16/各2Completed；候选与pack article均25%→75%，预注册要点高度1/2→2/2、类别7/7→6/7，总8/9均同。作者非盲评，引用ID/原文审计通过。高度题固定每个实际query重放.25/.5：正确向量1/5或1/2但BM25缺失，.25均融合丢掉，.5保留；原权重重放Top20与生产一致，候选融合归因成立，答案收益不外推。两组高度答案均有elements/strips适用到sections的无充分引用疑点，核验全放行；下一项回到实际compose/核验语义合同对象范围，不按词打补丁。产物wixqa-real-entry-next2-2026-09-08、报告rag-wixqa-next2，默认不变微调暂停，本轮待相干提交推送。

- 54e9ebe已push。R06/R10新见证合同核对：当前claim_verification为整体supported/answered/issues而非历史逐项；真实输入含完整知识pack，也重复在context facts，不能归因截断。既有通用semantic-focus已在MTRAG失败，禁止重复堆词。预注册4Flash冻结高度题均衡答案original/core-only×full/knowledge-only上下文；知识packs逐字不变，额外运行上下文移除仅实验，不适用混合业务生产。固定当前SYSTEM/schema/profile，零重试/总4调用；检查supported和answered分别，任何单例改善不直接采用。脚本run_wixqa_verifier_context4.py/同名产物。

- R06/R10核验上下文4调用完成：full/knowledge-only×original/core均supported/answered true；原答案输入8056→4510 tokens但疑似无支持附加结论仍放行。有依据首段不误拒；无改善不采用，生产上下文不删。请求hash/知识pack原样/相同system/4次调用审计通过（用.venv；系统python缺依赖仅影响首次离线审计，未重跑模型）。报告rag-wixqa-verifier-context4、同名产物，下一项转生成端受依据范围约束的完整性/附加结论对照，先预注册跨例，不增加核验调用或恢复旧协议。整体未关闭，本轮相干提交推送。

- 主线对照预注册续：Doc2Dial已暴露300开发题，原raw query/structure512-64/本地缓存两路各20/融合20/精排BGE/pack完整排序按5片段2600预算。仅比较.25/.5，重用已校验旧6000和membership新增CE分数；仅缺失pair本地打分，API0。不称新封存；与Wix文章/MTRAG片段口径分列。旧脚本的先切前5再打包在新权重模式改为生产的全排序扫描，旧模式不改；两臂同预算。

- 三套同预算.25/.5筛选完成：Doc2Dial300完整pack199→208（11救回2误伤），MTRAG35片段Recall .3319→.4414（10好2差），Wix20文章Recall .55→.675（4好1差）；中文模拟20既有两臂全部完整。采用均衡作为代码bootstrap默认，显式环境/Bundle策略覆盖保留；不宣称部署生效或答案准确率提升。core/rag_policy唯一默认owner和.env.example同步，缓存策略指纹随值变化。测试与总报告进行中；真实三套小量最终验收仍后续唯一活动项，不恢复核验支线。

- 本轮检索对照/采用决定已落地，59相关测试通过，新增API0/本地CE48pair；统一报告rag-three-dataset-decision-2026-09-08.zh-CN.md。当前唯一下一项三套有限真实链路答案验收及最终统一交付，不以本轮对照宣告整体结束。代码bootstrap默认已改，显式部署配置未改。准备相干commit/push。

- a8045ac已push。最终有限验收预注册：先Doc2Dial现有100文档开发库（与300题检索范围相同，不冒充488全文库），顺序取前两个有>=2历史turn且不同group问题；原history角色交替注入统一turn store，Agent自行query，无参考答案注入。原query为含否定的模糊投诉问题/Yes省略检验问题；期望前者合理澄清，后者保留外州检验到期或注册后一年取早。真实PG隔离tenant/同runtime/.5/Flash NONE最多12调用，SDK0。复用现有PG评测库基础设施，Doc来源独立tenant导入，local embedding只对缺失文档，禁止缩gold-only库。先当前最终方案两题，不为所有题重复旧系统。随后MTRAG全库入口和Wix已有真实结果汇总，语义失败照录不另开核验实验。

- Doc2Dial真实多轮2题完成，5 Flash：模糊投诉1规划后中文通用澄清失败（不把Completed算通过）；Yes题4调用自主query，gold两span实际可见，回答保留到期/注册一年取早。history在实际planner中逐条出现，source/checksum/引用ID审计通过。前两轮模型前KeyError均API0，v2栈定位旧库selected_failure迁移缺失；v3独立DB运行现有迁移成功，不下游兼容、不清旧任务。100文档开发范围显式，非488全库/非封存，原失败保留。主线下一唯一项MTRAG完整语料真实query入口与三套答案汇总，不修单例澄清或核验。报告rag-final-doc2dial2，准备相干commit/push。

- MTRAG真实入口预注册：顺序选Cloud dev前两个turn>=3不同group，原reference.input历史而非适配数据空history；参考targets仅评分。完整Cloud72439片段（官方Collection边界，与离线按domain检索一致），全量缓存shard/hash/原文校验；Agent实际query本地BGE+BM25重新检索，不复用固定query排名。同API handler/KnowledgeRetriever/Flash rerank/compose/verifier/publication，候选后端为本地精确参考实现，明确不是PG/ANN验收；运行状态独立PG库。最多12 Flash NONE/0SDK retry，.5/20/5/2600，无新文档embedding。源码port做来源offset/hash验证，未知适用过滤typed拒绝。先按冻结标准输出失败，不进入单例核验修复。

- MTRAG预检FP16缓存误用1e-4范数断言失败，API0；原shard/content hash有效，改为保持原FP16向量、不重新归一化。v2两题实际到工具，但generation适配器缺lexical_ranker/embedding_profile，检索前失败共6调用。补完整frozen generation身份并用真实api._knowledge_policy合同测试，9项来源/身份测试通过。保留v2失败；v3新隔离DB/conv再验一次最多8调用，本阶段累计上限14（此前12预算因接线失败明确修订），不为语义结果重跑。

- MTRAG v3完成：完整Cloud72439原文/缓存，2实际Agent query、8Flash、两题都调用知识工具，source/history/citation审计通过。weak understanding错误改为前轮配置alerts，qrel与回答均失败；Discovery改进回答主要步骤有其他来源，官方qrel0，不据此全判错，Dashboard适用限制及primary用词支持边界保留。原v2适配失败6API另计，预检0；未重跑语义结果。33项source/API身份/retriever/default测试通过。
- 三套最终臂8题29调用（Wix4题16复用、Doc2题5、MTRAG2题8），MTRAG候选为完整Collection本地精确后端而非PG，报告不混淆。统一报告docs/rag-three-dataset-final-2026-09-08.zh-CN.md及machine report已生成；本轮执行交付完成但质量失败未关闭，原解析/metadata/性能未验范围明确保留。后续真实query话题覆盖为已证实优先缺口，禁止重开微调或无限单例核验支线。本次相干commit/push进行中。

- R01 当前轮预注册：原MTRAG轨迹完整包含当前message和4条历史，错误已出现在submit_turn_plan.resolved_query，工具未改变query。共享机制假设为规划输入未明确最新message与历史的目标优先关系；不是上下文缺失。owner为planning provider的模型输入投影/指令；application raw_text保持权威，schema/执行器不生成替代query。正向合同：本轮message决定请求，历史用于消解引用与否定；命名术语保持原词，无法识别可检索原词或澄清，不擅自替换为历史问题。输入投影保留全部字段/原文且确定性将message置末。
  验证预注册：原MTRAG2开发见证+4条中文电商模拟（话题切换、否定、省略、假设），冻结payload及预期语义后旧/新provider同Flash NONE各6次共12上限、0SDK retry；只测规划，无检索/答案调用，不宣称Recall提升。采用门槛：旧失败不再转成alerts、对照无新增语义误伤，协议合法；否则不采用候选。程序性质测试覆盖序列化可逆、不变更payload/历史、最新输入保留、预算与输出协议。真实模型不能由字符匹配证明语义，逐例人工记录。微调/检索权重不变。

- R01第一候选12Flash完成：旧/新均把weak understanding改成alerts，拒绝采用。零API66测试只证明投影/协议不变量不证明语义。追加诊断预注册4Flash：原失败/原system固定，分别原生current消息、原生历史+current、仅知识schema、移除历史；后三者信息布局或范围变化仅用于定界，不直接采用。累计预算16；不重跑检索，不把历史删掉上线。

- R01 源码核对（2026-09-08，HEAD `a34102c`，含既有未提交工作区）：按用户要求核实上下文注入实现，未运行新模型实验。ConversationAgent.plan 持有原始 message／历史来源与预算裁剪；provider._complete 将全部字段压为单条 user，core.structured_model.structured_call 再固定构造 SystemMessage+HumanMessage，原生消息边界尚未接入。原MTRAG捕获的 submit_turn_plan.resolved_query、knowledge_search.params.query 和 evidence_pack.query 相同，错误已在规划输出形成。isolation4原始结果核对：native_current/native_history保留术语但额外解释尚未验证；knowledge_only_schema仍回到alerts；no_history返回out_of_scope。工作区v16移末尾+提示候选仍存在，但前轮实验已拒绝采用，不等同已修复或部署。领域执行器已有原生工作消息、按任务工具与压缩/归档，不能把规划器问题泛化成全系统无上下文管理。后续若迁移规划消息，需同步structured_call、实际消息预算、capture/replay单JSON读取假设及多轮合同测试；本次仅源码诊断，API0，未改生产代码，未提交/推送，R01继续开放，微调暂停。

- R01 用户授权主/子 Agent 上下文注入统一收敛（2026-09-08）：实施现有消息边界迁移，无新路由/编排旁路。预注册真实核对：既有pair6全部6个开发见证+新措辞2例主规划各1调用；领域政策2例真实TargetFrameworkAgent，合成只读知识工具，每例含评审最多4调用，总预算16、Flash NONE、SDK0重试，不重跑语义失败。任务范围、否定、指代与原话题逐项判定，缓存usage单列。脚本scripts/verify_context_injection.py先写完整manifest及源码hash再调用；不作封存/生产总体准确率。实现计划plans/context-injection-convergence-2026-09-08.md。
- R01真实核对首批13调用完成：主8例均保留当前话题，weak术语转为澄清；当前并行工作区新增respond合同，本批多例直接回复/过度补问，不能与旧6例作纯呈现因果收益。子2例共5调用因本评测夹具缺source offsets/scope/checksum，模型仅见KNOWLEDGE_INVALID_EVIDENCE后耗尽工具步数；不是子任务内容丢失的证据。生产证据合同不放宽。补全fixture并用真实model_evidence离线预检后，仅补子2例，最多8调用，累计上限从16修订为21；原失败保存，不重跑主语义结果。新输出context-injection-domain-valid-2026-09-08，原任务/原文/证据文字不变。
- 子任务有效夹具补验的首例在结果落盘时失败：AgentResult事实时间为datetime，评测JSON保存未转换；该进程未执行第二例，首例调用数未落盘，按上限4计入预算，不能声称完整语义通过。修复仅评测序列化，并逐次模型完成立即保存原始capture，防止后续报告异常丢证据。最后补子2例最多8，累计上限29（首批13+丢失最多4+本次8，实际可计上限25；29保留原预注册上限21+8）。不改模型、prompt、任务或证据文本，目录context-injection-domain-durable-2026-09-08。保留两次夹具/记录失败，不重跑主规划。

- R01本轮实现与核对完成：原生产provider/structured_call接入原生历史与分区内容块；领域pinned task区分来源/运行状态/委派目标。预算/SDK HTTP/capture/replay/审核调用者同步，无新路由旁路。主回归452通过/21跳过，隔离PostgreSQL94通过（重叠），增补分区pinned压缩后61通过/1跳过。主8例保留当前话题，weak不再转alerts；但并行respond合同带来未取证直接建议/过度补问，不能做旧版纯因果比较。有效子2例保修3调用完成、退货2调用保持范围但工具步数超限，原语义失败不重跑。18次完整明细cache_read31744/input58982，另落盘失败最多4调用，不能隐去；报告docs/context-injection-convergence-2026-09-08.zh-CN.md。改动未提交/推送/部署，结构实现完成不等于R01整体关闭；下一项冻结最终输出合同后验证对话与取证选择及代表性多轮，微调暂停。
- R01用户追问“不查/不停”源码复核（HEAD 83a7c3c，2026-09-08，新增API0）：实际规划使用respond分支；compiler在该分支检查字段形状并产生无commands的回应，取证必要性仍由模型按prompt判断。不能把未检索建议解释成工具漏执行。有效退货子例只有2轮模型输出，各2个检索调用；第二批触及本评测max_steps=3工具上限，不能称已证明无限循环。原政策仅说明“不适用七天无理由”，第二轮查询质量例外/其他条件，扩查是否必要未被此窄夹具判定。进度检测另有确切边界：ToolResultPersistence对包含query的完整data生成observation hash，AgentProgress比较该hash；相同证据不同query可被算作新观察，不能等同新增证据或目标覆盖。但本例硬预算先触发，未证明该机制造成无限循环。前述“查得停不下来”表述过强，修正为“追加检索超出预注册小预算”；输入分层不能自动替代取证与停止语义合同。

- 用户再次强调既有瓶颈：重复确认，以及单个问题找到/找不到污染其他问题的状态。已将其写入现有context-injection收敛计划的待验证验收条件；复用填槽/审批/任务图/结果板，不另建RAG状态机。知识缺口与用户字段缺口分开，授权按动作参数版本复用，各目标证据与结果独立归属，仅沿真实依赖传播阻塞，保留独立已完成项；新增问句/纠正不能重建整轮并重复执行。本次仅记录约束，未改生产逻辑、无模型调用，不声称旧瓶颈已修复。

- 2026-09-08 用户继续授权修复任务/证据进展（基点HEAD a8cea45）：沿现有填槽、WorkItem DAG、审批和ResultBoard实施，未新建RAG旁路。修复全字段齐交导致的部分补答丢失、响应投影丢逐任务覆盖归属，以及query/排序/旧证据重组被误计为进展。主回归1104通过/25无DB跳过；隔离Postgres91通过（与主集重叠）。API0、微调仍暂停；工作区未提交/推送/部署。详见[同一收敛计划](context-injection-convergence-2026-09-08.md)和[运行合同](../docs/conversation-turn-contract.md)。R01/R06保持语义待验证：本次未证明模型应查必查或答案充分性判断稳定，也未用确定性通过覆盖之前有限真实模型失败。
- 2026-09-08 用户授权继续冲突影响范围修复（HEAD a8cea45）：ResultBoard v3按事实及硬依赖推导逐项影响，部分交付由可交付独立结果聚合；ConversationAgent.compose、核验、普通模板及知识失败回退同步使用原板。新增迟到冲突和120种任务排列等测试。主套件792通过/8无DB跳过，隔离Postgres178通过（重叠），diff检查通过。API0，未提交/推送/部署；仍限已检测结构化冲突/显式依赖，未声称自然语言矛盾检测或总体语义闭合。见[同一收敛计划](context-injection-convergence-2026-09-08.md)。
- 提交推送确认：`7169d02` 已推送至 `origin/feat/customer-service-target-architecture`，包含原生上下文、分次填槽、证据进展及逐任务冲突交付修复和相应证据。精确暂存树隔离验证511通过/25无DB跳过，另隔离数据库91通过（重叠不相加）。其他归档分页、检索实验和文档工作区改动保留未纳入。未部署，R01/R06语义待验证及微调暂停状态不变。

- 用户授权真实简历数据补测，R01校准预注册（HEAD a8cea45+当前工作区）：第一批Doc2Dial冻结100篇语料的dev顺序前12个有历史的独立conversation；官方archive复验历史角色/原文，真实ConversationAgent.plan、政府知识范围registry，Flash NONE每题1调用、12预算（此前校准上限24保留未用）；无生成/核验。固定512/64结构切块、两路各20、.5/.5、候选20、本地BGE精排、5片段/2600tokens、实际wire序列化；原query与Agent单条实际工具query配对，无query/多query单列且不从分母删除。MTRAG/Wix后续单列，不以本批代表三套。语义失败不自动重跑。产物rag-query-calibration12-2026-09-08；脚本记录源码hash与样本后再调用。采用需实际可见覆盖净增且语义无新增误伤，本轮不改生产。

- 首批12Flash+本地检索完成：只有1题实际knowledge query，11 RESPOND；直接raw后端可见4/12，Agent可见1/12（无检索计零证据，非答案准确率）。输入/历史/源码hash全部复验无变化。失败集中在规划RESPOND对持续信息需求的解释：确认条件被当成新一轮澄清，已有上下文被重复索取，个别直接声称政策。预注册剩余12调用：开发专用决策合同候选，明确用户补答继续原问题、缺政策依据先检索一般规则、仅个人适用详情缺失不强制阻止查询；same12/sameFlash2048/same所有后端预算，不修改生产。必须先比较救回/误伤；即便改善也不跳过非知识对话回归和独立验收。累计校准24上限，不增加隐式重试。

- R01校准完成，当前HEAD9cf8fa5；两次运行源码hash一致，24Flash全部保留。Agent实际查询1/12→6/12，候选/最终wire完整证据1/12→6/12，配对救回5误伤0；wire MRR .0833→.4583、nDCG .0833→.4692。开发候选仍5 RESPOND/1空参数协议失败，未采用生产；模糊投诉等合理澄清不自动判错。原raw后端4/12只作诊断，不充当旧Agent基线。DMV集中12开发/100篇范围，不代替三套最终验收或简历66→79等目标。audit验证原始history、payload/schema、原文offset/checksum、raw排名两臂一致；报告docs/rag-query-calibration12-2026-09-08.zh-CN.md，输入132924/输出3037tokens，无生成API。下一步冻结respond/取证合同并补代表性对话及独立分组验收，未通过前不扩大付费检索；微调暂停。本轮仅实验/报告，相干提交推送中。

- 本轮交付检查：78项规划/原生上下文合同回归通过，两个audit与配对汇总可直接读取已提交gzip原始轨迹零API复现；生产提示未修改、未部署。

- 固定候选零API结果：MTRAG CE排名.75/召回排名.25(k10)可见Recall .4414→.4724，3改善0退步；Doc持平，Wix下降，不统一采用。Doc候选完整上限222/300(74%)。追加预注册Doc/Wix两路各20取并集至最多40候选，原query/corpus/模型/最终5与2600不变；原评分身份复验并仅本地补缺，最多6500pair、API0。比较CE、CE.75排名融合与Wix文章首片段；全部保留费用/误伤，不声称相同精排计算量。微调与Agent冻结。

- 检索表本轮完成（8d96122）：零API排名融合对照＋两路并集补1902本地CE。开发胜出：Doc66.33→69.33→70.00（本轮3救回1误伤，MRR微降）；MTRAG33.19→44.14→47.24（本轮3改善0差，原20池无新评分）；Wix55→67.5→72.5（1改善0差，候选最多40）。固定最终5/2600，Doc/Wix精排成本增加，非全计算同预算。Doc候选上限77.67%，不声称79%。全部旧baseline IDs/指标复现，零模型audit重建全部方案及新评分input身份，全部通过。报告docs/rag-retrieval-selection-three-2026-09-08.zh-CN.md；原始rank/union产物保留全部失败策略。开发胜出不修改生产、不冒充新heldout，下一项冻结分库候选后独立分组验收；不再转Agent。相干提交推送进行中。

- 用户要求各100新样本，已在评分前冻结rag-fresh100-2026-09-08/selection.json。387本地case/query/prediction文件及21来源校验，parse错误0：Doc100新test会话/官方488篇（与旧100篇开发范围不同，两臂重测）；MTRAG100题/42本地未命中会话，按会话聚类统计；Wix100题/100未使用文章关联组。保守本地未出现不宣称全球未见。策略严格沿用3ba6683胜出配置，对照.5原20池，最终5/2600；只本地query embedding与CE，Wix/MTRAG复用全库向量，Doc按相同切块/embedding重新建488篇本地投影，API0/CE总上限14000。不看结果后调参或换样本。

- 各100新样本验收完成：Doc58→58(1好1差)，MTRAG45.002→44.643(3好5差)，Wix70.833→69.833(2好4差)。三项候选不采用，原.5/20/CE生产配置保留；开发70/47.2/72.5不再表述为独立泛化提升。本地排除/来源/9077对评分/所有wire IDs零模型复算通过，分组bootstrap区间均跨0。API0，Doc488篇1469chunk新编码，其他文档向量复用；MTRAG100题42新会话须按组统计。报告docs/rag-fresh100-acceptance-2026-09-08.zh-CN.md；本批即刻标为已消费，不据结果调参换题。相干提交推送进行中，无生产策略变更。

- 2026-09-08 分层诊断完成（454e2f2）：Doc包含100%，并集66→融合65→CE/wire58；34题并集不完整，仅5题正确文档已全出现。MTRAG并集66.49→58.59→45.34→45.00；Wix90→79.67→70.83，pack不变。新增模型/API0，全部基线与评分身份重放通过。见docs/rag-fresh100-stage-diagnosis-2026-09-08.zh-CN.md及rag-fresh100-stages产物。下一项只做Doc历史完整query对照（编写时不见gold），冻结检索配置；不再三库统一扫权重。诊断完成非质量关闭；本轮按相干路径提交并推送，提交号以Git日志为准。

- R01 query诊断预注册24a1b4a：已按id/history/query编写100条单次完整query并hash冻结，不看gold重写、不按结果修改。先前上下文已暴露少数案例，不能称全盲；54/96无新问题，保留分母单列。同488篇/512-64/.5/各20/融合20/CE/5-2600，最多2000新本地CE、API0；原raw基线复验。只测上下文消解，不同时同义扩展或改Agent。

- 2026-09-08 query100完成：原raw两路排名完全复现；历史完整query并集66→100、融合65→98、wire58→96，MRR .4313→.7697、nDCG .4711→.8205，39救回1误伤。2000本地CE/API0，人工编写由本会话完成非零LLM成本；查询先hash冻结，无按结果修改。少数案例此前已暴露，已消费诊断非盲验/Agent/答案准确率。独立零模型audit输入/评分/排序/pack/wire全部通过，配对区间+28至48点。报告docs/rag-resolved-query100-2026-09-08.zh-CN.md，产物rag-resolved-query100-2026-09-08。相干提交推送；下一步真实Agent少量query对照，不再盲扫权重，微调暂停。

- Agent上下文20预注册：固定原100前20，Flash/NONE/2048各一次20调用0重试。真实MemoryManager写读及summary阈值、TargetTurnContextLoader默认8消息、真实ConversationAgent/provider；隔离Redis，PG投影watermark适配为夹具，无跨会话episode，无手工摘要/知识。达到摘要阈值停止避免隐式付费。实际payload/工具query落盘；无query计零、多query单列，不挑样本。检索同488篇/20/5-2600，对照同20人工query。

- Agent context20完成：真实MemoryManager+隔离Redis+Loader默认8+当前Agent，PG水位/空episode夹具明确；20Flash无协议错误，8直接query、9RESPOND、3委派未执行。有效摘要chunks0、memory触发0；直出query完整候选8/8、wire7/8，全20首次直接证据7/20，人工同20为20/20；非E2E。审计20实际窗口与payload/160评分/wire通过。报告docs/rag-agent-context20-2026-09-08.zh-CN.md，输入124965/输出1757。下一项取证与委派，不做盲目检索调参；相干提交推送，无生产变更。

- 本轮预注册557bca2：Doc同前20/完整原history/Flash NONE512各1次预算20，隔离纯query不工具选择，不见gold，后端同20/5-2600。MTRAG复用已消费100分路20，比较Dense/BM25/.5/双路各10补齐20候选的覆盖；无新模型。Wix已消费100同union40分数，文章级RRF（各路先折叠article，再映射回bestCE片段，最多20候选）、对照原chunk融合20/最终5-2600；只重放缓存。均为开发诊断，不改生产或称新验收；比较最终指标与救回误伤，失败保留。

- 本轮阶段结果：纯query20文本检索19/20，但2条输出答案及条件污染需语义单列，不自动采用。MTRAG候选Dense60.47高于融合58.59，双路各10仅持平；补预注册本地CE最多2000新pair，沿已固定dense20/reserved10_each/union40三臂复用缓存，最终5/2600不变，比较最终覆盖与误伤，不调新权重。Wix文章RRF初步70.83→71.17但MRR/nDCG下降，不采用。

- 三项完成557bca2后：Doc纯query20 Flash文本检索9→19/20，人工20/20，但4明确语义问题/4待判，非95%query正确率，未采用；MTRAG纯Dense最终45→46.03但MRR/nDCG下降，union45.67也下降；Wix文章RRF70.83→71.17但排序下降，均不采用。新Flash20、CE2053，全部输入/评分/wire重放通过。报告docs/rag-three-followups-2026-09-08.zh-CN.md，失败产物全保留；相干提交推送，不改生产/不恢复微调。

- 用户授权100题完整纯query检索链：b35fc07起点，同100官方历史/原问题→Flash NONE512→各路20/.5/CE/5-2600。前20输入/prompt逐项一致复用，补80次无重试；非空错误答案仍保留检索并语义单列，不删样本不以高Recall判query正确。不走电商Agent路由/最近8loader/最终生成。最多2000本地CE。记录全过程、逐层Recall/MRR/nDCG、协议失败和语义案例，不调prompt。

- 本轮100题完成：新增Flash80/复用20，输入27007输出2235；本地CE2000。并集/融合96，最终92，MRR .7187/nDCG .7698，对比raw58及人工96，救回36误伤2。20条答案型输出均能检索成功，92不代表query正确率。4候选缺失/4前5损失。全上下文、复用identity、评分、排序、pack/wire审计通过。报告docs/rag-pure-query100-2026-09-08.zh-CN.md，产物rag-pure-query100；相干提交推送，未改生产。

- 用户授权另两套各100补齐：95f42f1起点，MTRAG reference.input历史与当前问句（不输入官方rewrite/targets/contexts），Wix原问句无伪造历史；同Flash NONE512原prompt每题一次预算200无重试。语料/向量/各20/.5/CE/5-2600冻结，最多4000新CE。复用官方rewrite/原问句baseline，不以不合法输出获得Recall冒充query准确率；所有失败保留。

- 另两套各100补测完成：Flash200次（输入73686/输出5365），CE4000，向量缓存复用。MTRAG官方input历史生成文本最终45→48.31，19改善15退步，MRR略降，42组区间跨0；Wix70.83→66.83，3改善9退步，区间跨0。全部输入/评分/pack/wire审计通过。3条Wix复述system、其他答案型输出保留，不能称100个合格query。报告docs/rag-query-other200-2026-09-08.zh-CN.md，产物rag-query-other200；相干提交推送，不采用生产、不调后验权重。

- 用户要求回到原联合路径：3ac5fd0核对HISTORY双路存在，RESOLVED中raw等于Agent完整query并非原用户句；子需求planner调用仅见evaluation，step-back未找到。预注册三套已消费100固定生成文本：原句/standalone/联合.25-.75（当前.2-.6归一），每文本Dense/BM2520、合并20/CE完整query/5-2600，同文本去重复票。MTRAG此前官方rewrite不等于raw，额外本地检索原最后句。API0，新CE全轮最多8000，不重新生成query，不引入独立改写器；救回误伤/语义风险单列。

- 2026-09-08 raw＋standalone300完成：API0、新CE2168，固定.25/.75联合相对standalone改善/退步Doc0/0、MTRAG1/1、Wix0/1，不采用。300条融合/评分hash/pack/wire审计通过，MTRAG新增raw排名仅下游重放。现有否定词guard反例证实作用域/双否定/条件合取可漏检，非模型错误率。报告及产物rag-raw-standalone300-2026-09-08；本轮相干路径commit/push，生产未改、微调暂停、R01未关闭。

- 2026-09-08 4f6f87b后实施R01通用取证/查询来源合同：主Agent通用证据缺口规则，主/领域query说明共用；运行时原句独立进入handler/request/trace，不接受模型伪造，不改检索权重。198测试通过/3跳过；12Flash原生规划工具选择符合人工预期，中文追问英语1条；假设查询条件保留但显式假设词省略，不据此宣称事实污染或语义全对。输入70065/输出728；未跑Memory/检索/最终答案。报告docs/rag-evidence-query-contract-2026-09-08.zh-CN.md，R01保持开放。相干commit/push，微调暂停。

- d6a2f38后预注册：R01同案例真实链路2条（hypothesis/switch），已有模拟语料支持的原题，真实Memory/runtime/PG/Agent/检索/compose/verifier；重新规划而非强塞旧query。固定Dense/lexical .5/.5、候选20、final5/2600，不扫参数。最多16Flash、0自动重试；身份与输出落盘，检查假设条件、取证与最终回答，不能用verified当语义正确或称大样本收益。

- R01全链路2条完成：31模拟文档，8Flash，两例均查中/引用有效/运行时verified；人工复核发票步骤缺失且未说明来源限制，质量不关闭。d6a2f38 Trace序列化遗漏本轮修复，38测试通过，8调用原始产物不回填字段。commit/push相干交付，下一项答案需求覆盖。

- b851f76后预注册：R01答案覆盖，冻结原发票失败输入加5对照（缺步骤/明示缺口/完整步骤/只问条件/多需求遗漏），baseline与共享覆盖语义候选，同Flash12调用上限。仅verifier，不跑检索业务；同证据answered对照、支持性误拒和协议错误单列。采用须原失败拦截、正例不误拒，未见表达仍需另验，不称普遍保证。

- 覆盖verifier候选6题对照失败：3不完整仅检出1，原发票未拦截，不采用。补注册生成端同输入两例：最多4Flash（compose+现有verifier），冻结原证据与原问题，检查主动说明缺口与耳机条件误伤；无检索调用，不声明语义闭环。

- 生成实验输入审计发现首次4调用未应用候选（compose不经过_complete）；保留composition.jsonl为无效干预/额外baseline，不作候选收益。改在真实compose系统输入处注入，新增上限4调用，composition-v2逐次assert实际system含合同。总本轮上限20调用，失败费用计入。

- 生成候选v2实际system确认但发票仍未说明缺口，生产compose候选回退，不采用。追加6调用诊断：同6题结构化列出需求/证据提供/答案提供/缺项，再聚合covered；仅coverage能力试验，不替代支持性核验或接生产。总本轮上限26调用，不继续prompt扫参。

- 覆盖诊断交付：26调用全部保留，实际system/统计审计通过，119相关测试通过。生成候选未改善原例，生产回退；逐需诊断暴露缺项文字与covered标签矛盾，但也有扩大用户要求风险。未据此上线新门禁，R01未关闭。相干commit/push。

- abcde00后预注册R01覆盖协议：旧6开发＋新12验证已同时冻结（manifest），两臂Flash最多36调用。新协议分需ANSWERED/LIMITATION_EXPLAINED/OMITTED与证据充分性、原问题/答案引用，服务端聚合，无总answered覆盖。先开发再冻结不改动地执行新12，无检索。已知遗漏须检出、有效不误拒、无依据不放行，否则不迁生产；协议失败单列不能算语义检出。

- 新协议Flash开发仍漏原例、并出现将“先关机”误标LIMITATION_EXPLAINED，不能据此继续协议堆叠。追加模型单因素诊断：同旧6输入/同schema/system/NONE，仅Flash换Pro，6调用上限，不切生产，不用开发胜出宣称泛化。已先用Flash满足用户偏好；总本轮最多42调用。

- 三状态候选完成：冻结旧6新12，Flash36＋Pro6。未通过采用门槛，无生产修改；schema合法≠语义正确被实际反例验证。新12失败保留，78代数测试通过，审计42调用与输入/聚合。相干commit/push，R01开放。

- 2ea025d起点MTRAG预注册：冻结Flash100查询、原领域全库/向量、Dense与BM2520、CE20、final5/2600。先记录gold在两路/融合/CE/pack的集合变化及分域指标。候选假设：Dense20覆盖高于融合20，检验不经过词法融合是否改善最终；排序假设：固定融合20中CE排序可能损失，比较固定CE .75＋召回rank .25的RRF k10。不是扫参，新增本地CE最多2000、API0；基线逐题重放，不删答案型query。采用须最终Recall/MRR/nDCG无退步并另做新验收，此批仅开发诊断。

- 固定query100初步：FiQA最终29.94为最低，融合58.38→CE29.94。Dense20最终45.91、rank混合48.03均未胜出。追加有界精排诊断：已有顺序前6 FiQA，同query/原20候选/5-2600，只换Flash listwise（最多12API含格式重试），基线缓存复用，非新验收/不改生产；不将人工发现的非gold相关答案改标。

- Flash6首题已调用后因SDK Omit哨兵JSON序列化失败，结果未持久化；单列capture失败（1至2调用边界），不重跑该题不补零当语义错误。修复实验落盘后续跑剩余5，比较只用5完整配对，分母与费用缺口明确。

- 继续候选前归因：固定同100 query，用缓存全域向量/原BM25重算每个gold精确排名，断言前20与旧结果完全一致。仅重编码100 query，无文档向量化/新CE/API；统计83未入并集的近截断与深层遗漏，不采用扩大TopK。

- MTRAG诊断交付：100基线全重放、2718评分身份/pack通过；全库gold排名前20完全复现。新增CE718/query embedding100/文档embedding0；Flash5有效配对＋1首题capture失败（1至2调用边界），未重跑或计零伪造。Dense45.91、rank混合48.03均无采用；Flash5 Recall30持平、MRR.2667→.4。相干commit/push，生产未改。

2026-09-08 R04/R05 depth40预注册（基点8e47415）：同已消费MTRAG100、冻结Flash完整query与全域语料/模型，唯一改变Dense/BM25各20→40，RRF .5/.5 k10仍融合20，CE20、wire5/2600不变。API0、文档embedding0、本地query embedding100、新CE最多2000且复用已有分数。报告候选/融合/CE/可见证据、Recall/MRR/nDCG、救回误伤、分域及分组区间；旧20排名必须精确复现。仅当最终指标无退化才保留开发候选，采用仍需独立验收；粗召回成本增加，不称总计算预算完全相同。同步核对三套官方benchmark协议，不混用榜单分数。

2026-09-08 depth40完成：100题/42组，独立来源/查询/分路前20/CE/pack/wire重放通过；三指标略升但区间跨0，未宣称稳定收益或整体RAG关闭。官方协议核对已交付文档；开发抽样分数不视为官方榜单。交付：本轮精确路径提交与push，生产配置未改。
