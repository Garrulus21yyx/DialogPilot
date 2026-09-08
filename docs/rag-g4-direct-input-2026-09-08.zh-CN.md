# DIRECT 知识事实到合成输入的证据保真

冻结32题三组共96份上一轮知识视图。以显式重建的Knowledge Fact/ResultBoard fixture输入生产ResponseAssembler._assemble_candidate，经过真实AnthropicConversationPlanningProvider.compose和provider预算校验，在ainvoke传输处捕获消息，以固定占位响应结束，不调用API。

结果：96/96完整视图（query、每条原文、来源和证据ID）逐对象相等，全部到达模型调用边界；没有正文截断或预算拒绝。1项测试重新执行全部96条转换，不仅检查报告数字。占位响应不是答案，也没有进入核验或发布。

该结果限定为Fact→候选合成输入。fixture重建了检索结果外壳，未测试真实ToolResult→Fact、HTTP/ConversationAgent、实际模型、答案正确性、verifier或发布；没有多任务/长历史额外开销。首次fixture使用非canonical JSON，被FactRecord正确拒绝；改为规范序列化后通过，生产合同不变。

与领域Agent归档实验的关系：上轮27/96转读取引用发生在ToolResultPersistence，这轮DIRECT不经过该中间件，不能套用27/96作为DIRECT证据损失。相反，DIRECT的完整输入也不能证明领域Agent会读回归档原文。

本轮API0，生产权重未改变，微调仍暂停。下一个有意义的质量实验是冻结少量案例与当前/候选两臂，让实际Flash使用这些证据生成答案；需要保持历史/问题身份，保存输入和输出，按证据支持与需求覆盖独立评分。领域Agent归档读取另保留待验，不增加模型调用层来绕过它。

脚本 `scripts/replay_mtrag_direct_input.py`；产物 `artifacts/eval/rag-g4-mtrag-direct-input-2026-09-08/`。源码路径依赖当前工作区，未声称整个分支部署验收。
