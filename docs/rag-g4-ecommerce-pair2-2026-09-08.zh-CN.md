# 电商实际入口两案例回归

固定既有模拟开发案例：已拆封耳机、非质量原因七天无理由退货；2026-03-01普通质量退货标准运费上限。两臂独立测试数据库，当前0.25/0.75与候选0.75/0.25，实际coordinator→ConversationAgent→knowledge_search→PG检索→Flash精排→合成→核验→发布结果，全模型统一Flash。未修改生产权重。

| 验收项 | 当前 | 候选 |
|---|---:|---:|
| Completed / runtime verified | 2/2 | 2/2 |
| 仅知识工具调用 | 2/2 | 2/2 |
| 原文来源/工具可见/引用ID核对 | 2/2 | 2/2 |
| 两项核心业务结论与模拟政策一致 | 2/2 | 2/2 |
| Flash调用 | 8 | 8 |

实际EvidencePack确认两权重分别生效。历史查询两边均携带policy_date=2026-03-01，均回答12元，而不是当前18元；拆封非质量原因两边均给不适用七天无理由退货结论。额外质量审核建议未当作新的已执行动作；核心结论评分不代表每句额外建议均有独立语义认证。

真实Agent query措辞两臂不完全相同。此为同案例端到端回归，不是只改变权重的严格因果实验；资料规模较小，也不提供新的Recall收益证据。两例是已用开发模拟，不是新鲜电商验收。35对话MTRAG的增益不能直接套用到中文线上业务。

来源审计必须用source_id+checksum关联版本。首次新测试只按source_id构造字典，新版覆盖旧版导致历史checksum不匹配；改为版本联合身份后通过，结果和生产代码未改。1项产物测试覆盖真实权重、来源区间、引用、历史日期。它不把runtime verified等同于独立答案正确率。

旧calibration manifest写入通用case列表和fixed_query_override，实际full-chain分支未使用该覆盖。本轮保留原manifest并明确解释：实际参数以full-cases工具调用为准。已在manifest producer修正full-chain分支的案例数/定义/范围及空override，后续输出不再误导；未为元数据修正重复调用API。

运行源码含其他任务工作区改动，source-identity.json留痕，未将其混入本次提交。每臂数据库清理完成，API共16次，未超24上限。本轮重建的是小模拟知识库，不是重算MTRAG366k向量。

下一项仍需验证领域Agent归档读回（本轮实际为DIRECT知识路径）；总体量化还缺另一公共客服数据集的独立验收。保持当前默认，优先完成边界证据，不再重跑相同两例刷成功数。

产物 `artifacts/eval/rag-g4-ecommerce-pair2-2026-09-08/`；复现入口 `scripts/run_rag_ecommerce_pair2.py`（涉及API，现目录存在会拒绝覆盖）。
