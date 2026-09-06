# 订单状态与政策查询边界、答案引用关联修复

整体状态：持续优化。组件修复与实验结果不能视为端到端闭环。

## 证据链

原 audio-defect 输入完整包含用户“质量审核通过了，是不是退款到账了？”及目标描述，统一 ConversationAgent 只选 order_status 和 refund_status。后者在没有退款申请记录时抛 BusinessObjectNotFoundError，经 ToolManager 的通用异常路径成为 error，再被 TargetToolExecutor 当作 RETRYABLE_FAILURE。这同时涉及意图选择和业务读取结果语义，不能全部归因于 RAG 召回。

为区分问题，编写四条明确要求的模拟开发输入：只问政策、只查进度、订单+进度+政策、订单+政策。沿用真实捕获的当前空状态输入结构及标识引用；预期目标不传给模型。它们是人工构造的对照，不是新的真实用户或 heldout。

| 模型（现有 text 规划传输） | 所需目标存在且排除目标未选 | 编译成功 | 严格输出 schema 合法 |
|---|---:|---:|---:|
| Flash low / 4096 | 4/4 | 4/4 | 3/4 |
| Pro none / 4096 | 4/4 | 4/4 | 4/4 |

Flash 一条 goal_id 为数字，被现有编译器接受但不符合 owner wire schema；两条政策 query 为英文。两模型共 8 次 API 调用。不能用这些明确措辞要求真实用户照写，也不能据此删除原省略/混合问题的验收。

## 真实混合入口暴露的引用问题

将“查订单当前状态和实际退款进度，同时解释审核是否意味着到账”通过 TargetChatApplication 运行、统一 ConversationAgent、真实 PostgreSQL 业务及知识工具、回答合成与发布边界。语料与业务为原模拟夹具；没有退款申请。此执行仍不包含 HTTP、持久 worker 或业务写入。

三项工具全部调用，目标政策在模型可见 Evidence 中；共 3 次 API 调用。但是合成结果的政策 EID 关联到 WORK_ITEM_OUTCOME（成功执行）而非 KNOWLEDGE_FACT。渲染器正确拒绝，最终保守回复没有政策引用。此失败位于引用关联，不是候选召回。

## 所有者修复

application/composition_output.py 既拥有模型输出 schema，也拥有最终引用渲染。旧 schema 只给 claim IDs 和 evidence IDs 各自的集合，允许任意组合；渲染器要求它们正确关联。这是生产者和消费者契约表达不一致。

现在输出 schema 也表达原有双向条件：

- 每个被引用的 EID 至少归属于本段选中的一个知识 claim。
- 每个本段选中的知识 claim 至少有一条所属证据被引用。
- 共享 EID、多知识 claim、业务与知识共同支撑一段继续有效。
- 空证据的知识 claim 不可用于段落支撑；业务或执行结果不能单独支撑政策引用。

使用 JSON Schema if/then/contains，不修改现有渲染器，不自动改写模型生成的 attribution。Schema 是模型请求约束而非模型必然遵守的保证，确定性渲染和支持性验证继续执行。它证明引用归属，不证明文本语义正确。

生成测试枚举多个知识/执行 claim 子集与证据子集，包含共享、未知及空证据，比较 schema 与 renderer 的接受集合。模型正文、内部 ID 或格式的其他渲染要求仍由各自检查承担；这里只证明归属代数一致。

## 修复后组件重放

使用刚才真实失败的同一份回答合成输入，当前 provider 接受扩充的 schema，输出正确的知识 claim/EID 关联。2 次 API 调用（合成+支持性检查）后，引用渲染成功，支持性 PASS。原工具查询失败仍被明确呈现，回答未承诺退款到账。

仍存在重复政策陈述；此次没有重跑业务工具、来源复验或 Publication，不是完整入口提升证明，也没有重复采样或统计显著性。累计本轮 13 次 API 调用：规划 8、实际混合 3、合成重放 2。

本轮 54 个相关测试通过；独立审查 19 个测试通过，无归属契约阻断。额外断言确认新 schema 拒绝原始错误输出、接受重放输出。

## 未关闭的业务语义

不能在工具适配器把 BusinessObjectNotFoundError 直接改成“退款不存在”的成功事实。当前查询同时按 tenant/user/order 过滤，缺行可能表示无退款，也可能表示无权访问或订单不存在。必须由业务所有者先确认访问范围内的订单，再定义无当前申请的有限查询结论；按 operation_key 查询的结果还涉及写入后对账，不可与普通查询混用。该修复需要覆盖业务 owner、工具版本、事实适配和执行/对账消费者，本次未实施。

## 产物与复现

输入及明确预期：artifacts/eval/rag-policy-state-boundary4-input-2026-09-06。
规划原始捕获与摘要：rag-policy-state-boundary4-flash-2026-09-06、rag-policy-state-boundary4-pro-2026-09-06。
实际混合入口：rag-policy-state-mixed1-2026-09-06。
修复后组件重放：rag-composition-linkage1-2026-09-06。
这些目录均位于 artifacts/eval，gzip 为原始捕获，summary 保存原文 SHA256。原失败结果不重写。

```bash
MODEL_INTENT=deepseek-v4-flash MODEL_INTENT_REASONING=low MODEL_INTENT_MIN_COMPLETION_TOKENS=4096 \
.venv/bin/python -m scripts.run_conversation_plan_replay \
  --capture artifacts/eval/rag-policy-state-boundary4-input-2026-09-06/inputs.jsonl.gz \
  --output artifacts/eval/policy-state-reproduction \
  --modes text --max-tokens 4096 --current-goal-descriptions --current-output-schema

.venv/bin/python -m scripts.run_conversation_compose_replay \
  --capture artifacts/eval/rag-policy-state-mixed1-2026-09-06/mixed-cases.jsonl.gz \
  --output artifacts/eval/composition-linkage-reproduction --verify
```
