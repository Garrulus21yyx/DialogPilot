# 三数据集第一阶段：切块与数据锁定

## 实际范围与成本

Doc2Dial 已消费 mini-dev：100篇文档、300条开发问题、488个证据span。四种切块均通过当前生产PostgreSQL Knowledge candidate入口执行，本地BGE-M3、raw query、Dense/Lexical=.25/.75、RRF k=10、候选20。外部推理API调用0。不是完整Conversation Agent入口，也没有改写、精排、打包或生成。

## 四组切块结果

| 配置 | chunks | span完整包含 | 完整证据R@20 | MRR@20 | nDCG@20 | 对固定512救回/误伤 |
|---|---:|---:|---:|---:|---:|---:|
| structure256/32 | 589 | 99.39% | 189/300，63.0% | .4498 | .4947 | 1/19 |
| structure384/48 | 399 | 99.80% | 203/300，67.7% | .4475 | .5017 | 5/9 |
| structure512/64 | 314 | 100% | 207/300，69.0% | .4831 | .5322 | 0/0 |
| fixed512/64 | 314 | 100% | 207/300，69.0% | .4792 | .5292 | 基线 |

没有证明新切块优于既有512/64。较小切块降低候选完整证据覆盖；结构化与固定512的保留指标一致，但排序略有差异，不代表最终答案提升。不同chunk大小下相同K并非相同上下文token预算；这些结果仅用于候选诊断。多轮原始短句尚未消解，也不能把69%解释为整条系统准确率。

下一阶段纳入父子：全局child保留＋最多3个parent内child重检索，候选预算20；另需child命中后有限上下文扩展及同token预算打包。不能只比较固定窗口，也不能通过无限扩大parent正文制造收益。父子现有实现是从child排名聚合parent，并非parent embedding；本地重放与PG指标分开报告。

第四组同进程重复加载本地模型出现CUDA OOM，在评分前失败；前三组保留，固定512独立进程重跑成功。可复现sweep脚本每配置使用独立进程，防止模型生命周期累积。相关测试3 passed、1 skipped；所有4组实际数据库评测300条均正常返回。

## 数据锁定

- MTRAG revision `2c618bb98db3c8526433e22d8a2f7320f10a7470`。110 conversations、842 generation tasks；检索777个有qrel任务。按预定域内conversation哈希划分：Dev75组/519检索任务，heldout35组/258检索任务。不是官方Dev/Test。官方generation_tasks中的conversation_id/task_id与检索ID交叉验证；旧转换脚本author_timestamp不匹配，未用于正式划分。
- WixQA revision `d662dc42479c14e202eccd832f8c4b66a035c4cc`。6221文章，ExpertWritten200、Simulated200官方test逐行hash锁定。只表示身份锁定，不表示400次API已运行。Synthetic没有纳入外测。
- MTRAG qrel带原文切块偏移，后续仍要验证与passage corpus的映射。当前没有将qrel直接冒充文档级标注，也未跑heldout。

用户调整成本要求：本地可以较多；API开发对照先20条代表性问题，只比较baseline与本地胜出候选。封存外测规模与是否扩大将在开发结果后决定，不默认全量调用。

## 复现

锁定数据：`python -m scripts.lock_rag_external_datasets --cache <下载缓存> --output <新目录>`，脚本固定revision，结果保存URL、SHA256及分组ID。

切块四组：设置独立EVAL_DATABASE_URL和明确BGE_M3模型路径/revision/sha/device，运行：

```bash
.venv/bin/python -m scripts.run_postgres_rag_chunk_sweep \
  --dataset artifacts/eval/doc2dial-rag-mini-dev-v1 \
  --output <新目录> --locale en
```

环境身份检查由现有run_postgres_rag_eval执行。只读历史开发集；输出包含逐例候选、排名、source span、generation和模型身份。实现/阶段结果与最终质量验收分开，不宣称最优方案或整体关闭。

## 父文档内重检索补充对照

300条相同raw查询、314个512/64 child，本地exact dense＋Python BM25；两组共享相同分路分数，权重.25/.75，候选20。父子方案保留全局前10，再从最多3个命中parent内重检索补足。Flat完整证据207/300（69.0%），parent-child203/300（67.7%），救回3、误伤7，净减少4条。不能据此上线替换flat；也不能把这组本地结果当作PG运行结果，即使flat计数恰好相同。

本实验没有独立parent embedding；parent由child排名聚合。证明范围仅是这一种有界重检索策略。Child命中后补章节/邻接上下文、同token预算packing以及MTRAG长多轮检索仍未运行。首次脚本遗漏评分top_k参数，失败前向量已缓存；补参后重放，没有新增向量化或API。失败记录保留，未挑选多次模型输出。
