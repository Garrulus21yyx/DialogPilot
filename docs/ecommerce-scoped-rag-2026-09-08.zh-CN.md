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
