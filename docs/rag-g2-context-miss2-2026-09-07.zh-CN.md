# G2：真实 Context／Agent 查询与已知召回失败

2026-09-07，起点 `1f7ac5e`。本轮实际4次Flash规划调用：2条已知公共数据失败＋2条中文电商模拟。无答案生成；候选复验新增API 0。生产架构、融合权重和业务范围均未改。

## 观测结果

| 案例 | 实际可见历史 | Agent行为 | 后续证据 |
|---|---|---|---|
| 车辆检验标签不能使用 | 8/9，含最近agent问题及回答 | 生成完整知识查询 | 原raw候选未完整覆盖→Agent查询后融合第1完整覆盖 |
| yes：为儿童申请残障福利 | 2/2 | OUT_OF_SCOPE，无知识查询 | 不能当query生成失败或改写后漏召回 |
| 模拟：耳机拆封，退货不是质量原因 | 2/2 | 生成包含拆封／非质量条件的知识查询 | 仅规划已验证，尚未检索／生成 |
| 模拟：假设审核通过是否等于到账，不查订单 | 2/2 | 生成一般退款流程知识查询，没有订单工具任务 | 仅规划已验证，尚未检索／生成 |

公共数据yes例的完整上下文是：用户想了解18岁以下儿童的残障福利，客服问是否要为儿童申请，用户回答yes。模型确实看到了这两条历史。电商能力目录下返回OUT_OF_SCOPE不应被评测强迫转成知识工具调用。这暴露的是公共数据开放检索任务与电商Agent范围判断之间的评测边界，不能据此修改生产范围或声称模型不理解上下文。

车辆标签例虽成功进入知识查询，也不能证明政府服务问题都应属于电商业务。Doc2Dial仍用于公共检索／上下文诊断；真实电商Agent的范围和回答验收需使用对应场景。

## 同预算实际PG复验

Agent生成的原始查询：

> If a vehicle inspection tag cannot be used, what are the replacement or re-inspection options and requirements?

| 输入 | Dense完整gold位置 | BM25完整gold位置 | 生产融合Top20 |
|---|---:|---:|---|
| If the tag cannot be used? | 16 | 46 | 缺完整证据 |
| 实际Agent查询 | 1 | 1 | 第1完整覆盖 |

数据100文档，固定.25/.75、k10、每路20、候选20、默认750ms。正常PG候选调用均OK。深排名使用既有G1诊断入口另取1000，未进入生产候选；10s诊断连接池不是生产超时改动。两条raw＋一条实际Agent query共三条输入。OUT_OF_SCOPE例没有生成query，不补造一条完整query冒充模型输出。

本例表明真实统一上下文能产生有用的检索表达；同时原raw的Dense第16证据仍遭现有融合排斥，R04机制没有因此被修复。不能把单例召回恢复写成整体提升。

## 真实上下文入口

原始历史按官方speaker角色写入隔离PostgreSQL会话表，经`build_target_runtime`、生产Context加载、`manager.prepare`、Conversation Agent生成plan；没有手工拼`TargetTurnContext`。保存prepared context、实际模型消息及plan，逐项核对角色、内容、当前消息、工具参数。

四例均为冷投影，状态DEGRADED、reason为CURRENT_CONTEXT_PROJECTION_LAGGING。车辆例首条历史未出现在最近消息窗口，其余最近历史进入模型；其余三例历史完整。DEGRADED不等于“模型没有历史”，也不能据本轮说投影时序已经完整验收。本轮未修投影或改变窗口。

中文模拟实际查询分别为：

- 已拆封的耳机是否可以无理由退货？非质量问题的退货政策是什么？
- 如果退款申请审核通过了，是否就代表退款已经到账？解释退款审核通过与款项实际到账之间的区别和流程阶段含义。

两例work item均为READ／knowledge_search。此处只证明模型选择了政策查询并保留关键意思，不证明答案支持性或完整退款流程正确。

## 校验、下一步与复现

2项产物审计测试通过：4例实际模型上下文／原历史角色／计划工具参数对应，实际Agent query逐字进入PG、gold源区间及候选排名可复算。数据库和临时Redis已清理。首轮公共数据2次Flash，电商模拟2次Flash；没有Pro、微调或新增检索策略。

G2保持活动：下一步把这两条电商模拟的真实Agent查询接入实际知识工具与答案链，独立评估条件保留、证据可见、假设与订单事实边界，保留本轮作为小样本开发证据。尚缺固定分组规模的真实Agent整体量化与封存验收。

- [公共数据context manifest](../artifacts/eval/rag-g2-context-miss2-2026-09-07/manifest.json)
- [电商模拟context manifest](../artifacts/eval/rag-g2-context-ecommerce2-2026-09-07/manifest.json)
- [实际PG对照](../artifacts/eval/rag-g2-candidate-replay-2026-09-07/summary.json)

各context目录的queries.jsonl.gz保存模型输入输出；PG目录cases.jsonl.gz保存源证据与分路位置。复现脚本：`scripts/run_rag_g2_context_misses.py`（可加`--ecommerce`，会调用Flash）、`scripts/run_rag_g2_candidate_replay.py`（仅本地检索，需TEST_DATABASE_URL及embedding路径）。前者使用本地官方Doc2Dial zip恢复speaker，并在本地Docker测试PostgreSQL上创建隔离库。输出目录必须不存在。
