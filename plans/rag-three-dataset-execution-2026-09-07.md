# 三数据集 RAG 量化执行

in_progress。延续既定Doc2Dial/MTRAG/WixQA分工，不以核验器实验替代RAG指标。

1. Doc2Dial mini-dev 300题/100文档，四种现行PG chunk配置，固定raw查询、dense/lexical=.25/.75、候选20，本地BGE-M3。此阶段只证明candidate召回，不代表Agent多轮或最终答案。
2. 从官方revision锁定MTRAG human conversation与retrieval tasks；按既定域内conversation哈希70/30划分。检查qrel粒度与原文映射，heldout不用于调参。
3. WixQA固定HF revision及ExpertWritten/Simulated完整官方test，标注article-level。检查本地文件身份，不将旧子集充当完整外测。
4. 索引/metadata/召回真实性先验证，然后开发组缓存分路排名做融合与K对照；后续query/rerank/packing逐项推进。外测在冻结策略后运行。

用户收缩API规模：本地无API评测维持300题，后续真实API开发先20题，仅baseline与本地胜出候选。锁定全外测不代表立即全量调用。

MTRAG锁定完成：官方reference任务110conversation/842tasks（检索有qrel777）；75conversation Dev、35heldout。检索Dev519、heldout258。WixQA锁定6221文章、ExpertWritten200、Simulated200，尚未评测。旧转换脚本author_timestamp与官方taskID不一致，已用官方conversation_id/task_id，未依赖猜测映射。首次两次锁定失败无评分产物。

Doc2Dial前三组完成：structure256完整证据189/300、384为203/300、512为207/300；固定512待完成。第四次同进程重复加载模型显存不足，前三组结果保留，第四组独立进程重跑；这是运行环境生命周期问题，不计成模型/检索失败。

四组PG基础切块完成：固定512与结构512均207/300完整证据，无质量净提升。按用户提醒追加既有parent内重检索本地同分数对照：flat207、parent203，救回3误伤7，0API；不推广该策略。Child后扩上下文/同token打包仍pending。MTRAG/WixQA锁定完成，不等于外测执行。当前仍in_progress。

继续逐层本地筛选：现有314child/300query exactscore缓存，source40→candidate20，固定RRF权重/平滑15组；按conversation五折比较最佳固定与query bucket自适应（identifier/short/natural，训练bucket<20回退固定）。再比较anchor/相邻child span/有界全文parent，当前TokenEstimator2600预算、最多5片段、过大扩展退回anchor，完整source offsets评分。最后当前分数最优候选池做本地BGE-reranker-v2-m3全文精排，0API。参考LlamaIndex sentence-window/auto-merging思路但不冒称等同实现，比较各策略收益/误伤/成本。

本地分层结果：source40候选20中(.5,k10)223/300 vs当前(.25,k10)211/300；group五折fixed218 vs query-bucket adaptive218（救1伤1），动态未胜出。同估算2600token/5片段：基线182→调权192→本地CE208；总救29伤3，nDCG5 .5078→.6117。adjacent/parent扩展低于anchor。API阶段按固定hash选20conversation，baseline/candidate各一次生产compose模型接口，max40Flashcalls，全部保留，不选胜例。不是HTTP/Agent routing或自动正确率。

Flash20对照完成40calls：packed14→16，合法草稿18→18；4次失败捕获空工具参数，不是token截断。逐例诊断显示贷款/听证问题改善，但遗属问题证据找回后协议失败、历史年份泛化仍存在。未声称答案正确率提升。独立重算2700来源/预算/覆盖组合、配对和会话隔离通过；数据锁测试2通过。结果报告 docs/rag-local-selection-results-2026-09-07.zh-CN.md。整体保持in_progress，尚未运行MTRAG/WixQA外测和真正Agent端到端。
