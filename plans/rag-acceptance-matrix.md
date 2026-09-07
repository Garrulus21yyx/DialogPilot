# RAG 验收矩阵

2026-09-07；以 [主线状态](rag-optimization-status.md) 为唯一活动队列。

| 范围 | 当前证据 | 仍需验证 |
|---|---|---|
| 解析/切块 | Doc2Dial固定、结构、parent开发对照 | 声明支持格式；PDF、表格、条件/例外保真 |
| Context/query | 真实上下文诊断、两条电商完整链 | 冻结分组的稳定性；范围外不当query失败 |
| 候选 | dev300完整候选207→218，救回21误伤10 | 新分组同预算Recall/MRR/nDCG；当前未采用 |
| 精排/打包 | 本地dev300打包199→205；Flash12工具可见10→10 | 生产稳定收益，不能外推答案改善 |
| Metadata/时间 | 地区、历史日期、例外回归；目录修复 | 增量更新/撤回/缓存/日期跨版本集成；固定夹具生效时间 |
| 答案/发布 | 两条开发答案核对；六条回归发现畸形引用漏检 | 干净版本六条来源/引用已通过；独立答案评分和新分组验收仍缺 |
| 性能 | BM25三表达同库约5.8—13.8倍加速 | 并发吞吐、池等待、p95及ANN损失 |
| 交付 | 逐轮commit/push与原始产物 | 部署版本和验收版本一致；工作区改动明确 |

数据不能混算：Doc2Dial有字符span；WixQA本地long8000集manifest为36问题/39文档、article级标签，不能称字符span gold。MTRAG已有110对话的原始文件和75/35自定义分组锁，现有RagDataset已适配366,438非空passage/777query/2,128qrel；生产PG检索与成绩尚缺。中文电商集明确标记模拟。

文件名含fresh/heldout/unconsumed不证明尚未消费；rag-d-acceptance的200条已经有run/report。新封存集须先审计group ID消耗。

完成要求：冻结配置的分层配对表、逐例失败、工具/答案支持审计、调用/token/延迟、复现脚本及未参与调参的分组验收。各数据集独立报告；6/6 Completed不能替代6/6答案正确。整体RAG当前不满足关闭条件。

G4本地暴露审计：278文件、21来源checksum通过；Doc2Dial应排除516/661对话；WixQA问题重复15/200、16/200，按相关文章保守排除37/200、17/200。MTRAG本次无匹配不等于证明未使用。见[范围及复现](../docs/rag-g4-data-exposure-2026-09-07.zh-CN.md)。下一项为MTRAG数据适配/来源对齐，不启动新检索策略。

MTRAG适配与Unicode JSONL修复见[报告](../docs/rag-g4-mtrag-adapter-2026-09-07.zh-CN.md)。数据加载成功与真实检索已接通分开记录；官方PARTIAL标签保留。

MTRAG已有首轮无API词法dev32成绩：官方完整改写R@20 33.07→50.26%，MRR .2130→.3168，仍11/32无相关片段。仅正文/单路/官方query，非真实Agent链。详见[同预算报告](../docs/rag-g4-mtrag-lexical32-2026-09-07.zh-CN.md)；下一项固定query补Dense分路。
