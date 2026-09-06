# 统一 ConversationAgent 规划输出对照

本轮是六条已消费混合任务的冻结输入回放，使用当前业务 goal 描述、真实 provider 解析和 ConversationAgent 编译器。没有执行订单工具、检索、回答生成或 Publication，不属于端到端验收。生产 planner 仍使用文本 JSON，默认模型未改。

## 实验变量与结果

相对上一轮相同模型、相同六条输入、4096 输出上限的文本输出，本轮增加评测侧 `submit_turn_plan` schema，并把输出指令改为提交工具参数。Flash/low 使用 auto tool choice，Pro/none 强制指定输出工具。因此这是输出接口组合对照，不能把差异归因于某一个参数；每个输入仅运行一次，包含模型随机波动。

| 模型 | 双工具规划：文本 → 结构化 | 救回／误伤 | 本轮编译通过 | 输出 tokens：文本 → 结构化 |
|---|---|---|---|---|
| Flash/low | 4/6 → 5/6 | 1／0 | 6/6 | 9283 → 3611 |
| Pro/none | 4/6 → 6/6 | 2／0 | 6/6 | 737 → 901 |

“双工具”只检查编译命令包含 order_lookup 和 knowledge_search，不代表工具集合最小、query 完整、检索成功或答案正确。12 次 API 调用，没有重试；输入与缓存用量见各目录 summary.json。

## 逐例语义检查

- Flash 的 B20 问题不再截断，输出订单查询与安装前提知识查询；但 B20 查询和赠品查询改用英文，真实检索影响尚未测量。
- Flash 的省略问题恢复了耳机、拆封、非质量条件。质量审核问题仍只调用订单／退款状态，没有检索审核与到账的规则。
- Pro 的拆封问题不再生成非法 null 适用过滤。质量审核问题新增政策查询，但仍保留退款状态读取，需检查用户是否实际要求这次读取及其失败影响。
- Pro 的六条知识查询包含对应主要条件；预售问题额外令知识查询依赖订单读取。当前查询参数不使用订单结果，这个依赖可能增加延迟，并使订单失败阻断独立政策证据。

上述判断来自已提交的工具参数，不使用模型思考内容作为解释或依据。

## 边界与下一步

评测侧 schema 只是受限的 empty-state 工具输出格式；语义仍由真实编译器判定。不能直接复制到生产替代应用层规划契约，尤其不覆盖 active workstreams、控制、缺字段目标与部分执行。

后续先在规划契约 owner 定义与现有支持范围一致的输出格式，再接 provider 传输层。验证需覆盖状态分支、依赖合法性、引用绑定与类型错误，之后运行相同混合入口。业务状态固定用于降低对照成本，生产订单与知识路径仍由同一 ConversationAgent 协调。六条开发回放不足以支持更换默认模型或宣布 RAG 优化完成。

原始捕获、manifest 和配对汇总分别位于 `artifacts/eval/rag-planner-native6-flash-2026-09-06/` 与 `artifacts/eval/rag-planner-native6-pro-2026-09-06/`。捕获使用确定性 gzip，summary 记录解压原文 SHA256。执行入口为 `scripts/run_conversation_plan_replay.py --modes structured_native --current-goal-descriptions --max-tokens 4096`；输入为 mixed-segments20 捕获，通过六个 case IDs 选择，不读取正确答案生成规划。

验证：规划回放与规划 schema 相关测试 34 项通过。此验证不覆盖生产结构化规划迁移；该迁移尚未实施。
