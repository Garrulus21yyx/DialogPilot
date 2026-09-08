# 复杂电商纯RAG：建集与开发基线

本轮完成120题/30规则族自建复杂模拟集，以及40开发题的80次纯RAG对照。没有运行订单Flow、答案生成、发布核验或微调。80留存题锁定，未用于模型实验和调参。

## 与原短文集的差别

原集合一条短资料常包含整题答案，只需找到一个来源。新集合每题必须跨三份手册找齐基础条件、例外/兼容限制、步骤/费用，三处来源原文同时覆盖才算完整。干扰资料为不同渠道与旧版本。每族四种提问覆盖历史追问、假设与尚未确定的前提。

例如折叠餐桌题要求分别找到：无理由退货的期限/未组装条件；部件开裂的质量例外；不同原因的运费承担。只检索到“七日内可退”的片段不能计完整成功。假设题不代表用户实际发生故障。

产品主题部分沿用旧集、体裁模板跨split共享。规则由模型作者编写，独立人工语义审核待办；不称真实客户、真实店铺政策或完全未见主题。文档长度1405—2897字符，以Markdown章节和小表格为主，没有PDF/OCR/合并表格验收。

## 固定预算结果

配置：本地BGE-M3、Dense/FTS各0.5、RRF k10、结构切块512/64、本地bge-reranker-v2-m3、候选20、最终5、2600tokens。只改变查询：历史直接拼接，对比QueryTransformer完整查询＋原句融合（查询权重0.75/0.25）。双臂均提供相同历史，不拿丢历史作低基线。

| 指标（40开发） | 历史拼接 | 完整查询＋原句 |
|---|---:|---:|
| 融合Top5完整证据率 | 42.5% | 47.5% |
| 候选Top20完整证据率 | 100% | 100% |
| 精排Top5完整证据率 | 62.5%（25/40） | 77.5%（31/40） |
| 最终可见完整证据率 | 62.5% | 77.5% |
| 最终Top5证据单元Recall | 87.5% | 92.5% |
| 精排nDCG@5 | 0.8554 | 0.8831 |

同题配对救回7、误伤1，净增6题/15个百分点；10规则族开发观察，不是留存统计显著性或线上准确率。只有一次改写采样，没有稳定性重复，不宣称已选出最优策略。原文与标注未随检索结果修改。

完整证据率要求三个标注条款全部覆盖，单元Recall为三项覆盖比例；nDCG使用“含任一完整gold条款”的二元chunk相关性，理想排序来自全库当前切块。MRR不等于完整性，首个相关片段正确仍可能遗漏另外两项。结果JSON保留各指标，不混用来源级旧分数。

## 当前瓶颈与数据边界

6文档仅切成17片段，候选预算20覆盖全库，因此候选Top20满分没有大库召回意义。当前失败发生在精排后Top5缺少一处或多处证据，打包没有额外损失。主要测到的是多证据集合排序，而不是全库候选遗漏，也不能据此证明父子检索或某个chunk最优。

下一策略若在本集比较，应保持最终5/2600预算，围绕跨来源证据覆盖做对照；不能无限扩大Top-K制造提升。更大规模、同主题但适用条件不同的困难负例语料仍需另版构建，不能偷偷改锁定语料让数据更好看。当前渠道/版本写在文本中，不等于数据库适用范围硬过滤或版本撤回流程已验收。

## 成本与验证

30次Flash查询改写（10直接问题无历史不调用），两臂复用同一改写缓存；本地召回与精排，不调用生成/核验API。新增MemoTransformer只在评测捕获边界避免捕获与实际检索对同输入重复付费，生产算法未变。6项数据/指标/回放测试通过，全部120题的360条标注区间及同族split隔离检查通过；开发30独立证据条款的当前切块包含率100%。字符包含率不是语义完整性人工审定。

```bash
PYTHONPATH=. .venv/bin/python scripts/run_ecommerce_complex_dev.py \
  --output artifacts/eval/ecommerce-complex-v2-dev-new
PYTHONPATH=. .venv/bin/python scripts/report_ecommerce_complex.py \
  --root artifacts/eval/ecommerce-complex-v2-dev-new
```

运行需既有隔离测试PG容器、本地CUDA模型缓存和Flash凭据。第二步仅离线评分、不调用API。输入与gold分开；运行入口只读dev.inputs及corpus，不打开heldout问题。构建脚本遇到manifest拒绝覆盖。

资料位于data/eval/ecommerce-complex-v2，开发记录位于artifacts/eval/ecommerce-complex-v2-dev。下一项为开发策略固定后的留存验收；不恢复业务核验支线。本次完成数据/开发实验交付，复杂电商整体能力未关闭。

## 按用户要求扩入外部语料

已复用本地WixQA全量6221篇英文帮助文章（[官方数据卡](https://huggingface.co/datasets/Wix/WixQA)，2026-09-08核对MIT），加60份同商品异渠道中文近似规则。总6287篇、结构512/64共11590片段；外部11513、近似干扰60、原库17。英文背景与中文近似干扰作用不同，不以文档数代替同领域难度。原120题和gold不变，80留存未执行。

构建器scripts/build_ecommerce_external_corpus.py保存原文来源、ID、hash；完整外部语料生成在本地，不复制入Git，按manifest下载地址及hash复现。新增API0。上表仍是17片段小库开发数据，不能当扩库成绩。第一次扩库导入在HNSW构建因容器共享内存不足失败，未检索；评测进程关闭索引并行构建后重跑，失败产物保留。

扩库复现（使用新的输出目录）：

```bash
PYTHONPATH=. .venv/bin/python scripts/build_ecommerce_external_corpus.py \
  --wix-corpus /path/to/wix_kb_corpus.jsonl --output /tmp/ecommerce-expanded
PYTHONPATH=. .venv/bin/python scripts/run_ecommerce_complex_dev.py \
  --corpus /tmp/ecommerce-expanded/corpus.json \
  --rewrite-cache artifacts/eval/ecommerce-complex-v2-dev/runtime/pure-cases.jsonl.gz \
  --output /tmp/ecommerce-expanded-dev
PYTHONPATH=. .venv/bin/python scripts/report_ecommerce_complex.py \
  --corpus /tmp/ecommerce-expanded/corpus.json --root /tmp/ecommerce-expanded-dev
```

扩库对照固定的是语料、输入历史、模型与最终候选/输出预算；完整query＋raw有额外搜索表达，不表示两臂总计算量相同。本轮缓存重放隔离查询采样变化，不能当成重新测得的在线改写延迟。


## 扩库开发对照实测（6287文档／11590片段）

40题／10规则族，两臂80次纯RAG已完成；30条旧改写原样复用，本轮新增API **0**，本地精排fallback **0**。初次导入失败与成功重跑分目录保留，没有重采样查询。当前30个独立gold条款切块完整包含率100%。

| 指标（全部40题，失败保留） | 历史拼接 | 完整query＋raw |
|---|---:|---:|
| 候选Top20完整证据覆盖 | 70.0%（28/40） | 75.0%（30/40） |
| 精排／最终可见Top5完整证据覆盖 | 30.0%（12/40） | 32.5%（13/40） |
| 最终Top5证据单元Recall | 71.67% | 75.83% |
| 最终MRR@5 | 0.6083 | 0.7125 |
| 最终nDCG@5 | 0.5713 | 0.6211 |
| 最终含错误适用范围来源的题数 | 38/40 | 38/40 |
| 最终错误适用范围片段总数 | 80 | 72 |

全40题救回4、误伤3；其中cx-19-1历史拼接返回POSTGRES_UNAVAILABLE，而另一臂成功。两臂均OK的39题中完整覆盖均为12/39，救回3、误伤3。主表没有剔除失败；敏感性诊断说明净增1题不能归因为改写的语义收益。数据库不可用的底层原因未被现有trace确定，不通过选择性重跑改写主成绩。

扩库后确实暴露候选缺失和Top5多证据排序不足：完整query组30题候选齐全，仅13题最终齐全；打包没有额外损失。最终没有Wix英文背景片段，主要可观察干扰来自中文异渠道／旧规则。不能仅凭这个实验断言新增近似负例解释了全部下降，需要另行同预算消融才可定量分摊。

评测边界也已查明：本轮来源适用范围主要写在正文，实际检索request_scope中的region/channel/product均为null。它测的是文本检索辨别适用范围，并未验收已有metadata硬过滤能力，也不能据此断言生产过滤失效。下一项应先把自建语料的适用范围作为结构化metadata贯通到导入和已知请求条件，再对照原40开发题；须记录通用适用来源的保留规则，不由gold文档ID直接过滤。80留存继续锁定，候选方案确认后再验收。

扩库报告：artifacts/eval/ecommerce-complex-v2-large-dev-serialbuild/report.json；逐例scored-cases.json；runtime/pure-completion.json确认80次运行/API0；artifact-index.json记录本地完整trace的hash；execution-inputs.json固定实际扩库及改写缓存hash。失败目录ecommerce-complex-v2-large-dev保留failure.json。外部完整原文与trace留本地，提交来源manifest、复现脚本和汇总，不将全部外部语料复制入Git。

扩库交付验证：7项相关测试通过，包含证据来源身份、全部必要条款覆盖、排名指标边界与查询历史缓存一致性。未将本次结果写成复杂电商答案准确率。
