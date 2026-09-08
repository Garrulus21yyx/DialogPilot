# 电商RAG业务验收

用户授权：40条开发，80条封存；先本地检查与开发费用校准，再冻结候选做80条两版本配对。Flash优先，微调暂停。外部集合调参暂停。

- done 资料/业务合同盘点、分组案例初版（语义独立复核仍待办）
- done 40开发/80封存数据结构验证（模型编写模拟数据，不冒称人工gold）
- done 当前生产durable入口接线：输入与评分隔离、只读业务fixture、统一context及工具捕获
- in_progress 40开发运行及成本校准完成；27通过/10失败/3需独立语义复核
- pending 基于开发证据确定最小候选；没有候选时不伪造前后提升
- pending 冻结版本和80题配对，报告不确定性与失败

分组修正：采用60个独立规则族，每族2种表达共120条；20族40开发、40族80封存。不能称120个独立场景，置信区间以规则族聚类。两个变体保持同split。作者创建测试题不可避免看到内容，封存仅表示不用于调参、模型测试前锁定，非外部盲测或人工独立审查。

资料盘点：evaluation/rag_ecommerce_dev.py为已消费合成政策；data/product-catalog.v1.json为项目目录；services/customer_operations.py和mcp/customer_operations_tools.py拥有业务状态语义。新资料为隔离模拟店铺，不代替真实法律/店铺承诺。现有full_chain_probe只注册knowledge_search；mixed入口有只读业务工具但不是同一durable入口，须在测试fixture边界补齐，不能偷偷把混合题当纯RAG题。

评测合同：Agent仅收到history/message以及运行时context/只读业务数据；gold、预期工具、必答事实存独立评分文件。正确query允许同义简化；假设与事实区分。评分区分协议完成、证据覆盖、事实支持、需求覆盖，模型verified字段不算业务正确。缺少人工/独立语义复核则状态REVIEW_REQUIRED。检索指标仅有gold的子集，合理未知/业务工具单独分母。候选/pack/wire必须保留来源和原文位置。

预算预注册：数据构建与合同检查API0。先4条开发校准最多40调用，再决定40条总调用上限；封存不在候选冻结前运行。不得重复执行有副作用的退款/取消动作。基线git版本和全部有效源码hash固定；共享工作区变更须记录，配对采用隔离版本。

开发校准：4条已通过真实durable入口执行，2条提供完整答复；路由器追问遗漏型号并用英文泛化检索，答复降级；混合题实际调用order_lookup与knowledge_search，但引用验证失败，政策部分降级。不是接口断路或全库无数据。继续当前版本剩余36条建立开发基线，复用4条不再收费重跑；预算320调用上限（4条实际16调用，36条预计约144，允许迭代但受总上限控制）。不修一条就宣布完成，不提前动80封存。

开发执行期间发现fixture缺口：product_technical领域代理要求catalog_search/media_observe/media_read/service_episode_search，旧full入口未注册，安装问题领域委派触发INVALID_AGENT_CAPABILITY_ENVELOPE。归为HARNESS_FAILURE，不能计生产RAG错误。以API相同工具工厂接入目录/媒体；目录目前沿项目原目录，service episode使用owner定义UNAVAILABLE（本集合不测跨会话召回）。当前批保留为诊断，不作为封存对照基线；接线修复须重跑开发确认，避免在不完整入口上选策略。

有效基线40题：来源Recall/nDCG .925；155 Flash调用；作者确认27通过/10失败/3复核。源码hash运行前后一致，数据4 tests通过。80封存未运行。主线下一项仅证据合同候选验证（排序字段不应改变正文身份、精确引用输出），不重新调外部集合权重。见docs/ecommerce-rag-acceptance-2026-09-08.zh-CN.md。
