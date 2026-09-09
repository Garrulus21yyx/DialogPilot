# 复杂电商纯RAG：适用范围修复与固定方案验收

## 合同与实验范围

用户要求完成复杂电商自建RAG的Metadata接线和量化验收。沿用6287文档、11590个结构512/64片段、120题/30规则族；40开发和80留存分别评分。语料正文、gold、分组不变，新增来源作者Metadata；WixQA6221篇通用英文背景保持global，不根据问题相关性或gold过滤。当前题明确指定中国大陆官网，适配器据此传CN/web，不评估Agent抽取该字段的能力。

两臂均使用一次QueryTransformer完整查询＋raw，Dense/FTS .5/.5、RRF k10、候选20、最终5/2600、本地BGE embedding与reranker。只比较有无CN/web硬过滤；每题两臂时间一致，为导入完成后的同一评测快照时刻。来源时间两臂都生效，故本表不能直接当上一轮无Metadata基线的严格配对。策略在查看留存结果前冻结，留存结果不用于调参。无业务Flow、答案生成、核验或微调。

资料采用模拟业务时区UTC+08:00解释原文日期；这属于资料作者提供的约定，不是让模型猜UTC。官网现行版2026-06-01起，2025-01旧版到新版本起点止。香港、经销商、线下门店与海外直营按正文声明标注。外部文档没有作者时间声明，保留系统原有导入可见时间语义。

## 根因与修复

评测来源及请求未传适用字段：SourceDocument、运行时渠道目录及检索过滤已实现，修复在资料编制和纯RAG评测输入边界，不重造生产过滤器。来源作者脚本只读文档，不读gold；同一文档ID变化不影响其适用元数据。

实际分批导入另暴露SourceRevision时间身份问题：原先用+08:00字面值计算指纹，PG读回UTC后同一来源指纹变化。修复在SourceRevision统一规范化有时区时刻，在构造revision ID前以及重建版本对象时转换为UTC；不接受无时区日期。等价时刻同ID/同指纹、跨批次导入、重复导入通过真实PG测试。

兼容边界：既有UTC来源身份不变；本轮全新隔离评测库无需迁移。未对外部部署的数据做历史修复；若已有旧代码写入非UTC指纹的来源，需要单独审计并迁移，不能自动覆盖不可变记录或宣称本轮已修复所有历史库。

## 可复现执行

```bash
PYTHONPATH=. .venv/bin/python scripts/build_ecommerce_scoped_corpus.py \
 --corpus artifacts/eval/ecommerce-complex-v2-external-corpus/corpus.json \
 --output /tmp/ecommerce-scoped
PYTHONPATH=. .venv/bin/python scripts/run_ecommerce_complex_dev.py \
 --corpus /tmp/ecommerce-scoped/corpus.json --catalog /tmp/ecommerce-scoped/catalog.json \
 --scope-pair --all-splits \
 --rewrite-seed artifacts/eval/ecommerce-complex-v2-dev/runtime/pure-cases.jsonl.gz \
 --output /tmp/ecommerce-scope-pair
PYTHONPATH=. .venv/bin/python scripts/report_ecommerce_complex.py \
 --root /tmp/ecommerce-scope-pair --corpus /tmp/ecommerce-scoped/corpus.json --scope-pair --split dev
PYTHONPATH=. .venv/bin/python scripts/report_ecommerce_complex.py \
 --root /tmp/ecommerce-scope-pair --corpus /tmp/ecommerce-scoped/corpus.json --scope-pair --split heldout
```

运行需要已有隔离PG测试容器、本地CUDA模型和Flash凭据。API预算90，两臂共用每题改写，直接问题无历史时不调用改写。结果只代表检索证据覆盖；gold为模型作者编制，独立人工语义审核尚待完成。

## 失败保留

scope-pair120：导入前发现旧版缺effective_from，零检索/零API。scope-pair120-v2：第二批导入发现时区指纹冲突，零检索/零API。scope-pair120-v3全库导入完成，在第二开发题因范围预检只看当前句而中止，首题两臂已执行；该范围实际上在用户历史中。补充整批上下文预检后运行v4，复用开发30条改写缓存。v3在断言前已尝试改写，API记录不完整，不记零；未更换问题/模型/候选预算，未进入留存调参。失败记录与最终执行分目录保留。

范围选择的边界：当前三份有效手册各覆盖多个商品，未给它们臆造单一SKU，也未把问题型号硬过滤成商品字段。商品型号仍由query匹配；本轮只提供题目明确的CN/web。未知或通用来源保留global，不把“没有Metadata”解释成“不适用”。本轮不包含增量撤回、PDF/OCR或真实业务状态验收。


## 已完成的固定方案结果

最终运行：`artifacts/eval/ecommerce-complex-v2-scope-pair120-v4`。120题／240次检索全部执行，正式运行新增60次Flash改写，开发30条改写复用缓存；无改写错误，本地精排fallback为0。未用留存结果选择新参数。

| 指标 | 开发40：不传范围 | 开发40：传CN/web | 留存80：不传范围 | 留存80：传CN/web |
|---|---:|---:|---:|---:|
| 候选完整证据R@20 | 72.5% | 95.0% | 82.5% | 97.5% |
| 最终完整证据覆盖@5 | 32.5%（13） | 77.5%（31） | 32.5%（26） | **77.5%（62）** |
| 最终证据单元Recall@5 | 75.83% | 89.17% | 72.08% | **90.42%** |
| 最终MRR@5 | 0.7125 | 0.9083 | 0.7771 | 0.9313 |
| 最终nDCG@5 | 0.6211 | 0.8489 | 0.6454 | **0.8560** |
| 含错误适用来源的题数 | 38 | 0 | 73 | **0** |

表中数字保留失败请求。留存配对救回37、误伤1，净增36/80＝45个百分点。按20个规则族配对bootstrap，描述性95%区间为31.25—58.75个百分点；它不代表真实企业流量置信保证。两臂均正常的76题中，完整覆盖25→60，救回35、误伤0，说明提升不是只由后端可用性差异产生。

本次可采用结论：自建知识源的已知适用范围应贯通到检索过滤；现有生产过滤器可完成这个任务。没有证据要求更换embedding、动态权重或恢复微调。这一比较仅量化范围接线，不能把45个百分点归给query rewrite、chunk或精排模型升级。

## 审计、成本和剩余失败

实际240次运行234次OK、6次UNAVAILABLE。数据库日志PID与本次隔离库对应，6次均为词法SQL触发既有750ms statement timeout；未放宽阈值、未补跑。开发2次均在过滤组；留存两组各2次。详见database-timeouts.json及scope-audit.json。

范围审计检查2320个过滤组实际候选，地区／渠道／时间均满足；其中1392个为通用Wix背景候选，全部6221个通用外部文档仍具备适用资格。来源正文和偏移一致，没有通过只保留gold来源制造高分。66项相关检查通过，包含真实PG的范围过滤、通用来源保留及跨时区分批导入。

正式运行provider usage原始字段：input_tokens=8964、output_tokens=2609、cache_read_input_tokens=640；不根据这些字段猜美元金额。检索计时排除改写API：不传范围p50/p95约2115/2222ms，传范围约2084/2187ms；属于单次串行评测观察，非并发性能验收。此前v3启动失败的API核算不完整，未冒充总项目成本为这60次。

过滤组留存尚有18题未完整：2题SQL超时、16题候选齐全但精排Top5没保住全部证据，打包没有新增损失。候选召回缺口在这批正常请求中已消除，完整R@20未到100%来自超时；最终答案正确率没有测。本轮结束既定接线与验收，不宣称整体RAG已无缺口。

80题已消费为本次验收集，今后只能作回归。若继续优化多证据Top5排序，应在开发集做固定预算实验，并另留新规则族验收，不再把这80题反复调成“新鲜heldout”。人工语义审定、真实中文企业语料代表性及历史非UTC数据迁移仍不在本次完成声明内。

证据：dev-report.json、heldout-report.json、逐例scored-cases、heldout-failures.json、scope-audit.json、pure-completion.json、artifact-index.json及输入/源码hash。外部完整原文与完整trace留本地，可按构建器和锁定来源复现；本提交保存指标与合成问题逐例结果。
