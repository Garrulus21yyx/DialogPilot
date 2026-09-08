# 领域Agent真实归档读回：可见性成立，任务完成仍失败

复用已消费MTRAG语言题及11403字符证据视图，真实TargetFrameworkAgent/general、ToolResultPersistence、read_tool_result、默认工具阈值2840、InMemoryStore；知识工具是固定证据fixture，不重新检索。Flash NONE，首轮3次+修正轮5次共8次API，无Pro。

首轮配置误把WorkItem位置参数8当max_steps，实际max_steps4，触发5/4工具限制；保留原产物，不能充当8步验收。改具名timeout_seconds60/max_steps8后重跑，API上限剩余9，实际5。

修正轮：5次知识搜索执行，2次read_tool_result执行；依次读取0—2000、2000—4000字符，原文逐字一致，实际后续模型请求包含两页。第二页含Another language，Flash下一轮明确识别这项支持证据。随后请求继续读取offset4000及再次搜索，整批触发9/8工具上限，返回AGENT_STEP_BUDGET_EXCEEDED，没有最终答案。没有进入outcome review，不把终止当完成。

这验证“原文可恢复→自主读取→实际模型可见”在此例成立，但不验证所有必要条件被读到、完整回答或通用可靠性。第一轮只有第一页时，模型将整包称changelog并重搜；第二轮有第二页时，找到了直接答案仍想补搜。通用按字符截JSON分页缺少证据条目/章节导航，可能增加理解和读取成本；这是基于行为的机制假设，尚未通过对照证明为唯一根因。

固定fixture对不同query返回相同证据，所以不能推断真实检索必然重复或故障。相同内容因工具调用artifact不同可生成不同归档引用，不据此擅自改变内容身份合同。工具上限也是既有资源合同，本轮不通过无限增大预算规避失败。

下一步先做零API证据导航边界设计核查：明确知识结果条目与来源信息如何随归档保留、按来源/片段读取是否能避免跨JSON截断，保留权限、原文和页预算。再用相同任务/预算对照，不先堆prompt或换Pro。其他数据集验收、召回缺口和微调暂停状态不变。

1项实际请求审计通过：两页与冻结原文一致、进入后续请求、第二页含目标表达、终态和调用数一致。存储使用内存，未测Postgres重启/跨请求恢复；工作区真实执行源码SHA保存在fixture，不与其他任务改动混提交。产物为`rag-g4-domain-archive1-2026-09-08`及`rag-g4-domain-archive1-steps8-2026-09-08`。复现脚本`run_rag_domain_archive_probe.py`默认拒绝已有输出目录，可用RAG_ARCHIVE_PROBE_OUTPUT指定新目录；每次新增调用均应有预算预注册。
