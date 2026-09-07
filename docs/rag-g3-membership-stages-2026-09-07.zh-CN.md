# G3：候选误伤归因与精排／打包收益

2026-09-07；起点 `a49633b`。本轮不修改生产融合策略。固定上轮300条已暴露开发样本、每路20、候选20、.25/.75、k10、512/64结构切块；使用同一本地BGE精排与2600-token／5片段打包。

## 配对结果

| 阶段 | 原候选方案 | 两路交替取候选 | 救回／误伤 |
|---|---:|---:|---:|
| 完整候选证据 | 207/300 | 218/300 | 21／10 |
| 精排Top5完整证据 | 199/300 | 205/300 | 11／5 |
| 打包完整证据 | 199/300 | 205/300 | 11／5 |
| 精排MRR@5 | 0.5644 | 0.5793 | — |
| 精排nDCG@5 | 0.5895 | 0.6057 | — |

候选提升3.67个百分点，经过精排和打包后保留2.00个百分点。该打包输入仅使用Top5锚定片段，重放生产ContextPacker；不是线上EvidencePack→ToolMessage链路。没有调用Conversation Agent、Flash、答案生成或核验，不能称答案准确率提高。

## 10条候选误伤的共同机制

两路候选在固定20槽位中竞争。交替取候选恢复Dense贡献，同时挤出BM25中后段证据；10条均丢失BM25第11—19名的必要片段。其中7条必要片段不在Dense前20，另3条虽两路都命中但都不够靠前。这是预算分配的取舍，原文切块未变化，不能归因于新的解析或证据截断。

| 案例 | 被删除gold的BM25名次 | Dense前20名次 | 原／新打包完整 |
|---|---|---|---|
| doc2dial-dev-ad94c0dc42f749c2b0e2349d830b4827-12 | [18] | [13] | True／False |
| doc2dial-dev-6977cbb3bed8f0e8480018cb3cac6716-9 | [11] | 未命中 | False／False |
| doc2dial-dev-b55dafba91b47f076435e58d02a0659b-3 | [11] | 未命中 | False／False |
| doc2dial-dev-69a1ae6bc0b246b8725a70ae49745fe8-5 | [13] | 未命中 | True／False |
| doc2dial-dev-85a07edcac90068ee15c3769034af6f2-13 | [19] | 未命中 | False／False |
| doc2dial-dev-28c759aa607520e8e07e080847e19054-5 | [11] | 未命中 | False／False |
| doc2dial-dev-dd8eb49fdbf955282d36e8d39d10e452-1 | [15] | [18] | True／False |
| doc2dial-dev-00a4a05f229e5cee83e7b10f2e20873d-3 | [18] | 未命中 | False／False |
| doc2dial-dev-abba59c477b0cf795c20d5b0b83e4634-7 | [19] | 未命中 | False／False |
| doc2dial-dev-cb7b4dd9f52ce66b315e7709dbbfd2ff-12 | [12] | [11] | True／False |

这10条中4条在原方案打包成功、新方案失败；其余6条原方案虽然候选完整，精排Top5本来就未完整保留。另有1条在两组候选都完整时被新增候选挤出精排Top5，构成排序误伤。最终11救回／5误伤不能直接沿用候选阶段21／10的计数。

## 缓存与成本

原评分缓存6000对；本次候选并集7896对，复用其中5536对，补算2360对。恢复旧数据manifest和全部6000个候选位置，并重建原.5/k10/source40候选顺序；验证模型SHA和完整query/child输入生成方式。历史缓存没有保存token张量，故只能声称评分配方及候选顺序复验，不能补称历史输入字节审计。

新增评分使用本地 `bge-reranker-v2-m3`、FP16、batch4、完整输入不截断；无训练、外部API调用0。补算加打包约20.5秒，不含模型加载与初始数据准备，不是在线p95。报告记录新输入hash、模型SHA和新增评分；之后可完全离线重放。

## 决策与下一步

维持未采用：开发集存在净收益，但仍有已定位误伤，且生产默认Flash精排、真实ToolMessage和封存数据未验证。不能根据这31条案例继续手调配额并把同批成绩称为泛化。

下一项仍属G3：冻结本轮两方案，优先用已有生产精排输入／缓存评估可复用范围；必要时只对小规模、预先固定的配对案例补Flash调用，验证生产工具可见覆盖。保留本轮失败案例，不新增微调支线。G2真实Agent查询与G4答案验收仍待完成。

## 证据与复现

- [汇总及缓存审计](../artifacts/eval/rag-g3-membership-stages-2026-09-07/report.json)
- 同目录`cases.jsonl.gz`保存300条阶段顺序、覆盖、被删除／新增gold及分路名次；`added-scores.json.gz`保存2360对新增评分。
- 3项审计／候选性质测试通过：逐例回到源字符范围复算600组覆盖、核对精排分数排序与打包子集、复算汇总。无模型重放逐字节复现300条结果。

```bash
PYTHONPATH=. .venv/bin/python scripts/replay_rag_membership_stages.py --output /tmp/g3-stages --added-scores artifacts/eval/rag-g3-membership-stages-2026-09-07/added-scores.json.gz
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_rag_membership_stage_evidence.py tests/test_rag_candidate_membership.py
```

省略`--added-scores`将补算缺失本地评分。脚本仍校验本地原模型文件SHA；模型路径记录在历史manifest中。
