# 两条残余miss的语义复核：官方Recall不等于可回答性

本轮固定原始会话、官方query、两臂CE排名与原文，Codex非盲核对五处来源引文；API0，不修改qrels、查询或生产策略，不把此次复核当独立人工准确率。

## 语言支持：查询明确，低标注Recall不能直接归因模型排错

原始会话只有首轮问题：是否能用英语以外的语言创建dialog skill。没有历史需要注入或指代需要补全，官方query保留了完整问题。

parent_local第1名`ibmcld_03329-1102-2607`明确讲创建dialog skill，在非英语情况下从列表选择相应语言；同候选第4名`ibmcld_03120-3469-5331`说明选择Another language创建dialog/actions skill，并保留优先使用已列语言模型的条件。两者不属于该题官方gold，但对问题提供直接支持。

相反，目标miss `ibmcld_03369-64600-66787`讲dialog分析笔记本新增支持的语言，这不是创建对话技能所用语言的同一能力。它局部排23不直接证明检索语义差。另一gold具有创建时Language字段，仍有相关性；不据此次观察删除任何官方标签。

**纠正前轮表达：官方Recall下降是真实指标损失；“新增片段造成语义误伤”尚未被证明。** 新增第一名文本实际上更直接回答当前问题。检索到全部gold也不是回答此单一问题的必要条件。

## Web chat：有可用入口信息，具体诉求与版本仍不确定

原会话讨论部署、定制外观及打开关闭后，用户说找不到web chat。两臂第1名`ibmcld_16365-7-1700`解释launcher打开方式及默认右下角入口，支持“默认在哪里找”的答复；但不能由此断言用户实际页面仍用默认设置。

官方gold侧重主页、conversation starters和建议展示，是另一种可能诉求。最新问题不足以唯一确定用户指这些设置。该第1名属watson-assistant文档，部分gold属assistant/assistant-data；原历史涉及经典体验，版本适用需要保留，不把新文档一律当等价来源。

因此可以有依据说明默认入口，并在实际配置/版本不明时追问；不能为命中gold把home screen/conversation starters塞进query，也不能以当前R@5=0断言模型手里没有帮助用户的证据。

## 对主线的影响

官方Recall/MRR/nDCG继续原样作为基准可比指标；另外评估最终答案的直接支持、条件完整、需求覆盖和合理追问。两项分列，不能临时扩qrels制造提升。

暂停针对这两条gold排名继续调parent配额或query。parent_local尚不采用，原因是开发证据有限、最终答案与适用版本未验证；不能单凭此语言题的qrel下降宣称答案恶化。下一步沿已冻结候选做小规模答案支持性验证，包含对照和适用版本检查，保持费用受控，领域读回与跨集验收不被永久挤出队列。

产物`artifacts/eval/rag-g4-miss-semantics2-2026-09-08/review.json`保存会话input、五处原文引用/偏移/SHA、两臂位置及官方标签。脚本`PYTHONPATH=. .venv/bin/python scripts/audit_mtrag_miss_semantics.py`验证源archive SHA、引文真实存在；这些机械检查不证明语义标签必然正确。
