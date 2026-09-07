# 候选池损失与完整 query 实测

300条既有开发集缓存，raw query，每路40、Dense/BM25=.5/.5、RRF10。零API重算：Dense40完整230、BM2540完整218、两路并集244；融合Top20为223，Top40为237，Top60为243，全部并集244。77条Top20失败分成56条并集缺失、21条融合截断损失。扩大候选只证明潜在可见性，不证明最终排序/答案改善；增加精排池必须另计成本。

完整query实验沿用预选20个会话，使用真正ConversationAgent.plan、现有prompt/schema/默认电商registry、双方TargetTurnContext。Flash明确provider=deepseek、reasoning=none，规划20次。17条产生knowledge_search.query；1条缺历史追问，2条公共政府问题判领域外。没有执行工具、业务操作或生成答案。

| 同样17条成功产生query的样本 | raw | resolved |
|---|---:|---:|
| 候选20完整证据 | 14/17 | 17/17 |
| 精排Top5完整证据 | 13/17 | 16/17 |
| MRR@5（现有项目口径） | .7206 | .7843 |

两项完整证据均救回3条、误伤0。该条件子集并非全部20条成功：按原20分母计，无检索query记0，raw/resolved的Top20均17，Top5均16，MRR为.6958/.6667。这揭示领域路由与查询改写是两个不同验收对象，不能省略那3条后宣称整体通过。

首次运行发生评测配置错误：新ModelProfile默认provider=anthropic，手工固定Flash模型名却没固定provider，20次请求均AnthropicInvalidRequestError；没有模型响应、没有有效query。修正评测脚本provider=deepseek后才得到上述20次有效规划。保留失败artifact，不计入模型质量；本轮累计40次请求尝试，20次成功返回规划。新增在首个传输错误后停止批次，避免重复无效请求。此前默认模型断言失败发生在调用前。

## 查证的方法与适用位置（2026-09-07）

- [Dialogue-RAG，ACL2025](https://aclanthology.org/2025.acl-long.1191/)：补全省略、指代的查询以改善对话检索。这支持当前优先做完整query。
- [ConvSearch-R1，EMNLP2025](https://arxiv.org/abs/2505.15776)：使用检索反馈训练改写模型。其榜单优势限定论文测试集，不是中文电商保证；当前先用已获授权的Flash验证收益，不先开展RL训练。
- [ContextualRetriever，EMNLP2025](https://aclanthology.org/2025.emnlp-main.602/)：训练检索器直接结合会话上下文，是替代独立改写的研究路线，需要相应模型与训练，并非把全部历史塞入普通BGE就等同实现。
- [Anthropic Contextual Retrieval，2024](https://www.anthropic.com/engineering/contextual-retrieval)：在索引前为chunk加入文档语境，同时用于embedding和BM25。与检索后扩parent不同。先比较无需API的标题/章节路径，再决定小规模生成上下文实验；生成内容不替换可引用原文。
- [Hybrid fusion分析](https://arxiv.org/abs/2210.11934)：论文发现调好的分数凸组合可超过RRF；不是所有数据上都成立。可重放缓存比较，避免只扫RRF权重。

下一项优先比较：完整query、raw+resolved多表达；同query的RRF与归一化分数融合；再分别测20/40精排池并固定最终上下文预算。保留并集召回、截断损失、精排后完整证据、计算成本四列。若完整query后两路仍缺失，再测试文档语境索引及更强检索表示。以上为有依据的实验顺序，不宣布所有方法已实施。
