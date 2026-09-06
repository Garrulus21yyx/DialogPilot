# 回答引用：单支持项选择合同

状态：生产迁移已实施并完成限定验证；整体 RAG 语义和表达质量未关闭。

## 根因与正向合同

旧输出让模型分别生成 claim_ids 和 evidence_ids，而这两者的合法关联已经由服务端持有。真实 quality_yes 捕获中模型选了业务 fact，却附上知识 EID；即使 schema 加入关联条件，模型仍可能违反，应用只能拒绝。

现在 composition_output 所有者枚举合法支持项，模型每段只返回 text + support_ids。每个支持项绑定一个原始 claim，以及可选的知识 EID。服务端恢复 used_claim_ids 并渲染引用，不猜测或修补模型的关联。

- FACT、RECEIPT、WORK_ITEM_OUTCOME 每个 claim 对应一个无 EID 的支持项。
- KNOWLEDGE_FACT 的每条 (claim_id,evidence_id) 关联对应一个支持项；空证据不生成支持项。
- 共享 EID 可属于多个 claim，各自保持支持项；同一段显示引用按 EID 去重。
- 模型可同时选择多个业务和知识支持项。选择 outcome 不会自动获得政策引用。
- 未知、重复、内容已变更导致失效的支持 ID 被拒绝，旧双数组输出也被生产解析器拒绝。

支持 ID 由完整 claim 内容及 evidence_id 的稳定哈希生成；来源和内容均参与身份。请求附 support_catalog 并与原始输入一起捕获。传入既有 catalog 时必须与原始 claims 重算一致，否则拒绝，避免重放重新编号造成错配。

## 迁移边界

ResponseAssembler 在调用 composer 前准备 v3-supports payload，预算看到包含映射的完整输入。provider v8-support-selection 复用 owner 校验和 schema，只负责传输与输出形状检查；renderer 按同一 owner 映射恢复归属。ResponseAssembler v4-support-selection 继续执行失败提示、业务标识检查、语义 verifier 和来源复验。

纯知识 GroundedAnswerGenerator 保留既有独立 EID 合同。用户上下文仍与权威事实分开，未改变召回策略或工具选择。

旧 schema/renderer 移到 evaluation/legacy_composition_output.py，仅用于历史评测与夹具显式迁移。convert_valid_legacy_composition 先运行完整旧归属校验；原来错误的关联无法转换为成功。旧捕获不改写。历史成功输出转换可能调整引用顺序，但保留归属集合和引用集合。

replay 当前重新调用模型时生成并保存新映射，不将旧模型输出当成新格式。旧 field-guidance 内容布局实验因支持项已绑定原始 claim 而明确拒绝在新路径运行，应在 4a032e3 复现；说明已更新，无静默改写。

## 验证及收益边界

139 个相关测试通过，包括生产新支持项测试、历史兼容评测、规划、预算、上下文、checkpoint、渲染和支持性验证。独立审查77tests通过；额外枚举47个合法旧归属组合，转换后均保留 claim/引用集合。

生成性质覆盖支持目录所有非空子集，验证共享 EID、多知识 claim、业务与失败 outcome 的组合只能恢复合法边；同时验证内容变化后的旧 ID、未知 ID、错误既有 catalog、空证据及错误历史关联均拒绝。

固定两个已消费输入做组件对照，共4次API调用：

| 输入 | 原归属渲染 | 新归属渲染与支持性检查 |
|---|---|---|
| quality_yes（旧失败） | 拒绝 | 通过 |
| correction（旧成功） | 通过 | 通过 |

观察到1条救回、0条误伤；样本太小，没有稳定提升或显著性结论，组件重放没有执行工具或来源复验。

随后真实统一 ConversationAgent 混合入口运行 quality_yes，共4次API调用：预期工具、目标政策可见、最终引用和knowledge支持性检查均通过。程序断言模型实际请求使用 v3-supports，捕获映射与原始 claims 重算一致。本轮共8次API调用。

答案仍较长，包含快照解释和不必要的政策分支。新契约保证选择的归属关系合法，不证明模型选对依据或回答中的每句话有充分支持；现有语义与来源检查不能省略。本轮没有解决原生规划输出迁移及所有多轮语义问题。

## 产物

- artifacts/eval/rag-support-selection-replay2-2026-09-06：两条固定输入、完整映射、原始gzip与SHA256摘要。
- artifacts/eval/rag-support-selection-input-2026-09-06 和 rag-support-selection-mixed1-2026-09-06：真实入口输入、manifest、completion、原始gzip及分阶段summary。

组件 replay manifest 中历史 input_migration 说明来自启动时旧说明字符串；summary.actual_input_schema_versions 和实际捕获明确记录 v3-supports。后续脚本说明已同步，原始历史捕获未改写。
