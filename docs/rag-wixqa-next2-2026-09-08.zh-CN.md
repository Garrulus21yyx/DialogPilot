# WixQA 后续两题真实入口（2026-09-08）

冻结开发集位置3/4，在生成前记录9个参考要点、原参考答案和来源hash；不作为新heldout。完整6221文档11167片段，Conversation Agent自行query，既有Wix评测bundle，Flash NONE，两臂各8次，共16（预算24）；各2/2 Completed。默认权重未改。

| 指标 | 向量0.25 | 向量0.5 |
|---|---:|---:|
| 候选文章Recall平均 | 25% | 75% |
| 打包文章Recall平均 | 25% | 75% |
| 高度题参考要点 | 1/2 | 2/2 |
| 类别题参考要点 | 7/7 | 6/7 |

要点评分为作者语义评估，不是独立盲评；片段来源/checksum/引用ID程序审计通过。两组总参考覆盖均8/9，不能将文章Recall提升写成答案准确率提升。类别题宽泛，漏掉服务排序不必然让整个答案不合格。

## 已分离的因果机制

高度题：两组query不同，但固定各自query的分路排名重放两权重，均重现0.25丢掉正确文章、0.5保留。第一query正确chunk向量排名1/5，BM25未召回；第二query向量1/2，BM25未召回。均衡融合分别保留在4/12与1/4。重放原权重Top20与真实生产候选完全一致，说明这个候选损失可归因融合权重，无需猜query不完整。重放仅验证候选，不复用来推算Flash排序/最终答案。

高度题0.5的主要步骤正确，但添加H字段设置区块高度、区块viewport行为时，引用分别只谈elements和strips，尚未建立对sections的适用关系；0.25也把element stretch handles用于section。应标为来源不足/对象适用范围疑点，不凭外部常识判定产品事实必然相反。两组运行时核验均返回supported=true、answered=true、issues=[]，因此不能用verified当答案正确率。

本次没有新增生产修复，未采取按案例特殊路由、增大K或强制文档多样性。后续优先核查实际compose核验合同如何检查主体/对象适用关系；结合已有冻结案例验证，避免只给prompt增加section/strip关键词。权重.5仍为候选，跨数据默认采用待更多证据。

## 复现

scripts/run_wixqa_real_entry_pair.py --weight 0.25（或0.5） --offset 2 --output <新目录>。原目录拒绝覆盖。依赖已有隔离PG及本地模型。

scripts/audit_wixqa_real_entry.py --root artifacts/eval/wixqa-real-entry-next2-2026-09-08

scripts/audit_wixqa_next2_fusion.py

产物同名目录含预注册、全部模型轨迹、分路、来源审计、融合重放及作者逐点评估。本轮16 API之外的审计均零API，文档未重新向量化。整体RAG仍开放，微调继续暂停。
