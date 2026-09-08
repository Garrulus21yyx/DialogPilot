# RAG 优化主线：持续维护的状态入口

最后核对：2026-09-08；本轮起点 HEAD `6828dba`，微调实验提交 `228a90b`。本文维护当前优先级与验收状态，历史报告维护当时的实验事实。更新时填写实际核对版本，不能把工作区实现等同于已提交／已部署。

**整体状态：未完成全链路量化验收。G0代码／测试／报告已通过 `5018f83` 提交并推送；G1诊断已完成；G3阶段证据已交付、融合未采用；当前推进G4数据审计与封存准备，G2泛化仍待验证。微调暂停，生产默认不因小样本结果切换。**

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

- G4 heldout35完成：候选Recall20 41.33→61.58%，pack/序列化Recall5 33.19→44.62%（+11.43pp，11提高/4降低，bootstrap95%[+0.95,+22.86]pp）；MRR .4619→.5700区间跨0，nDCG .3365→.4347。新增35 query向量/1212本地CE pair、文档向量0/API0。5项原回归＋1项heldout审计通过。精排对.75自身Recall有小幅下降，未据验收改策略。见[报告](../docs/rag-g4-heldout35-2026-09-08.zh-CN.md)。35组已消费；不称答案提升，不切生产。下一项汇总开发/独立检索证据并推进电商实际入口与领域读回验收，核验风险保持开放。交付待本轮提交。
