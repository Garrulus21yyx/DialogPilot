# 四条双路漏召回：查询与原会话核查

2026-09-08，用户要求优先核查query，暂停原计划的打包重放。只读取reference.input会话编写四条表达，先冻结再评分；随后才读取标注原文和实际返回正文。历史排名与source ID此前已暴露，因此不是独立盲测。API0，新编码12条query，文档向量重算0。

固定原完整领域语料、BGE-M3正文向量、BM25、每路20/融合20/k10。三种表达：原官方query、只去掉开头`|user|:`的控制、基于会话的手工表达。原始四题均为双路Top20无命中，复测重现。

| 问题 | 原官方Dense最佳gold排名 | 会话补全后 | 查明的情况 |
|---|---:|---:|---|
| 其他著名诗人 | 55 | 56 | 仅去role标签可到18；加入诗集名后前排回到旧诗集。补全引入检索偏移 |
| 找不到web chat | 1220 | 277 | 缺产品与挂件上下文；补后同一来源页面排第1，但标注段落仍深，且目标含糊 |
| Enterprise | 79 | **3** | 原改写回到早先Lite套餐；最新上下文是账户类型，gold正文确为“什么是企业账户” |
| 两个银行账户的体验 | 27 | 36 | 第二gold由184到56；业务/个人背景把前排拉回“是否应分账户”，未进入Top20 |

这只是四条失败诊断，不是整体Recall提升。手工补全仅1/4题在Dense Top20恢复一条gold（该题两条gold中的另一条排79）；BM25仍0/4。原role标签控制单独恢复诗人题一条gold，手工补全却未保留这一收益；不能合并挑选各题最优后冒充一个统一策略。

## 1. Enterprise：查询目标错位，而且融合还能再次丢掉证据

最新用户先问账户有多少类型，接着发“Enterprise”。原官方rewrite是询问Lite plan是否适用于Enterprise；它回到了更早的话题。

冻结的替代表达是：What is an Enterprise account in IBM Cloud, and how does it differ from the other IBM Cloud account types?

复测后gold `ibmcld_12623-7-2328` Dense79→3，正文标题确为What is an enterprise，内容是集中管理多账户账单与资源，支持该解释。用户短句本身仍有歧义，不能说上下文逻辑唯一。

**当前.25权重仍把这条Dense独有证据排除在融合20外**；.5/.75才保留它。这证明本例同时涉及query目标与融合候选资格，改query不能绕过后一个问题。

## 2. Web chat：查询缺背景，文档命中后仍漏段落，标注意图还需保留疑问

会话明确提过Watson Assistant、网站web chat widget、launcher以及打开/关闭方式；原query只剩“having hard time finding web chat”。补入这些信息后Dense最佳gold1220→277，Top1是Adding the web chat to your website。

Top1 `ibmcld_03166-4-2012` 与gold `ibmcld_03166-14421-16009` 的原始url完全相同：`https://cloud.ibm.com/docs/assistant?topic=assistant-deploy-web-chat`。所以已有**正确来源页面命中、具体标注passage漏掉**的证据。

但两条gold集中在home screen/conversation starters、自定义主页和suggestions；用户究竟是找不到挂件入口、还是不知道如何开始使用，不够明确。不能读取gold后把“conversation starters”塞回query再称盲测救回。本轮不重标，也不把全部错误归因于embedding；保留意图歧义与段落定位两个诊断。

## 3. 诗人：历史实体不一定该作为正向检索词

用户从Robert Louis Stevenson和A Child's Garden of Verses转向“其他著名诗人”。我补入旧书名虽让句子独立可读，却让Dense前两名回到这本书；排除了谁的语义没有自动变成检索排序约束。

gold覆盖Cummings、Lowell、Blake等不同材料，包括部分并不直接介绍诗人总体的片段。这是开放式推荐，标注不是所有可接受推荐的穷举。不能把没命中这五段就等同于无法推荐，也不能据此私自扩大qrels。

更重要的是不要把“完整理解”与“所有历史词都进入检索”混为一谈。应保留推荐新诗人的目标，并把历史对象作为排除/对照背景；具体搜索表达还需独立开发验证。本轮没有在读gold后追加第二次改写实验。

## 4. 银行：旧业务背景压过了最新的体验诉求

原会话从自由职业者分开业务和个人账户，转向同一家还是不同银行，最后问人们使用两个账户的体验。补全后的前排是“应否分开自由职业和个人账户”“开一个独立business account”，仍在回答更早的问题。

gold则是用户分享两个账户分配日常账单、消费与储蓄的做法；这些体验未必要求freelancer背景。第一gold原Dense27，补后36；第二gold184→56，仍都不在20内。BM25一条从859→35，但另一条变差。不能称补背景稳定有益。

## 结论与后续动作

四条不是同一个根因：Enterprise是明确的目标错位；web chat有来源内定位与意图歧义；诗人/银行展示了**我这次手工补全也可能过度注入旧背景**。角色序列化标记还影响了结果，需单独控制，不能把去标签收益算成语义消解收益。

后续查询合同应区分最新信息需求、必要指代背景、历史但非当前约束；已知条件并非一律变成检索正向关键词。这是待验证的策略原则，当前没有修改生产Agent提示词。

下一步先把这四类判断接回实际Context→Agent查询诊断，并用未参与当前分析的开发案例验证；web chat保留source URL作为父级定位线索。不能根据这四条gold做case-specific生产分支。原打包可见性工作仍在队列中，整体RAG未完成。

产物：[冻结会话与query](../artifacts/eval/rag-g4-query-miss4-2026-09-08/queries.json)、[分路完整gold排名与融合](../artifacts/eval/rag-g4-query-miss4-replay-2026-09-08/cases.json.gz)、[成本和身份](../artifacts/eval/rag-g4-query-miss4-replay-2026-09-08/report.json)。原正文可按既有zip checksum和source ID复验；不重复向Git导入全语料。

检查：1项产物审计通过，覆盖12表达身份、原基线重现、相同gold、预算和指标重算。脚本 `scripts/replay_mtrag_query_misses.py` 只重算query向量并读取旧文档向量；未运行精排、生成或最终答案评分。
