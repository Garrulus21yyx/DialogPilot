# RAG 主线：生产入口接入与同预算对照

状态核对：2026-09-07，实验已完成、工程交付未完成。当前优先级以 [RAG主线状态](rag-optimization-status.md) 的 G0 为准，本文件只维护子实验记录。范围为KnowledgeRetriever、真实PG、固定已编写20条开发查询，不是三数据集封存验收。

发现：现行生产每路candidate_k=20，离线胜出来源40；线上LLM listwise精排与离线CE不同。提交版本的精排缓存身份缺实际模型身份，工作区已有接入修复。正向合同：精排实现有稳定模型/预处理身份；缓存和运行时使用同身份；候选不截正文、返回完整ID排列；本地失败明确fallback而不伪装胜出。

1. implemented_uncommitted：本地BGE CE适配现有KnowledgeReranker，配置选择与模型身份贯通；相关测试已跑，交付时复核受影响调用者。
2. measured：同20完整query/同PG/每路20/候选20/2600tokens/最多5条，已完成固定权重和CE比较；候选完整19/20，本地CE Top5完整18/20。
3. measured：Flash listwise20次调用已完成，Top5完整19/20。原结果将工具安全隔离误算为pack损失；工作区修复claim名词的误拦截后，使用保存排序做0API重放，Flash pack与实际可见均19/20，本地CE均18/20。重放延迟不含真实模型精排。尚未补跑本阶段的少量真实Agent回答，不标为完成。
4. pending_delivery：整理修复、测试、原始与修正统计和commit/push；保留Flash默认。已有6条电商链路报告属于其他批次，不能代替本阶段最终答案验收。

产物：`artifacts/eval/rag-production-reranker-pair20-2026-09-07` 与 `artifacts/eval/rag-production-reranker-replay20-2026-09-07`。当前数据为已暴露开发诊断，19/20不是端到端准确率，也不代表旧60条heldout中的召回遗漏已经恢复。
