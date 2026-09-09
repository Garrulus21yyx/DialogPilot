# E12 40开发题真实入口切块对照

用户授权对比。固定6287篇scoped corpus v2、40开发题、已有原始/standalone查询、BGE-M3/local BGE CE、Dense/BM25 .5/.5 k10，候选20、final5/2600、CN/web及固定as_of=2026-09-10T00:00:00Z。仅structure_aware vs markdown_headers；两臂使用当前GIN修复SQL。隔离库/同真实retrieve与wire入口，API上限0，无答案生成。保留全库6221英文背景与60中文近似负例，不用小库替代。

精确文本＋模型身份缓存复用，输入变更才本地embedding；CE最多1600对。评分独立读取dev gold，各策略按自己完整语料chunk计算相关性，完整source evidence覆盖为主，MRR/nDCG辅（chunk粒度改变须注明）。验证输入query/scope及来源offset，统计救回/误伤、错误范围、失败与额外成本。只保留完整可见证据不退步且无范围泄漏的开发候选；不称新heldout/生产采用。实际模型输入和引用相同之外不推算答案提升。

首次运行在导入HNSW构建遇到64MB共享内存不足（DiskFull），尚无检索题目结果，保留failure.log；本地embedding工作量未落盘不能称0。v2沿用项目既有PGOPTIONS max_parallel_maintenance_workers=0，两臂相同，不改查询timeout；重新隔离库执行。

运行修订：v2原策略40/40词法SQL超时，第二组在导入中止，未形成质量对照。仅查询改写状态日志OK并不表示检索成功。partial实际3650chunk执行计划3257scope rows，原SQL725.894ms，逐主键回读词项633ms。试验term_matches物化反而2400ms，已撤回，不能称修复。v3专门量化切块质量：两组统一SQL超时5000ms（生产750不变），其余原预算保持；原失败完整保留，不与v3混合评分、不作为超时修复。性能问题仍开放，采用门槛增加生产超时预算验收；本轮仅筛选切块开发候选。

完成：v3两组40题均OK，候选40→40，最终完整33→37，条款113→116/120，MRR .9583不变、nDCG .8965→.9521；救回cx22同族4题、整题误伤0，cx01-1部分覆盖退步。剩余3题有未声明商品却指定商品gold的歧义，保留分母与失败不改标。默认不切换，开发候选保留。API0、CE1600对、18测试通过，query/scope/budget/offset/hash审计通过；报告docs/rag-header-retrieval-pair-2026-09-09.zh-CN.md。后续优先解决真实语料750ms性能和输入/gold一致性，再新鲜验收，不开启新策略。交付相关路径commit/push；未部署。
