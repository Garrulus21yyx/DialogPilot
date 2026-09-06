# 统一 Agent 的订单与政策混合开发校准

本轮不是隔离生产业务路径，而是在隔离数据库中冻结订单事实，运行统一 ConversationAgent、真实只读业务工具、knowledge_search 和答复发布。五条中文模拟开发案例覆盖发货、支付、签收、历史指代以及订单不存在；不代表正式电商准确率，也不是 HTTP、中间件、持久调度器、领域 Worker 或跨会话 Memory 验收。

## 已观察的根因与修复

当前消息 `订单DP9301现在`、历史消息 `我说的是订单DP9303。` 中的订单号没有进入实体绑定。提取器使用 Unicode `\b`，中文与英文字母均属于 word 字符，因此中文相邻处不构成词边界。规划器同时收到含订单号的消息和空的可信实体绑定，基线五条均在 2048 token 输出预算内未完成规划。

在实体提取所有者修复边界，并同步修复答复验证器的编号识别，避免中文相邻的虚构编号绕过验证。保留各自原有前缀、分隔符、数字和长度范围；禁止从较长 ASCII 标识符内部截取部分编号。覆盖当前消息、历史、摘要和不同边界组合。相关 126 项测试通过。

保持 Flash、low reasoning、2048 输出下限及原生产提示词，修复后的五条均实际执行 order_lookup 和 knowledge_search。因为模型调用存在随机性，这个五例结果仅支持根因校准，不能证明一般化提升幅度。

| 结果 | 修复前 | 修复后 |
|---|---:|---:|
| 实际执行两种工具 | 0/5 | 5/5 |
| 最终知识支持检查通过 | 0/5 | 3/5 |
| 知识安全弃答 | 0/5（规划未完成） | 2/5 |

## 尚未修复的答案阶段问题

- 发货案例：规划与两种工具均成功，混合答案合成使用 INTENT 的 Flash/low 配置，2048 输出预算耗尽，JSON 不完整。统一 Agent 的规划与答复阶段共用了一个模型角色配置；应在角色与上下文预算所有者处核查 SYNTHESIS 接线，不能从此案例推断查询需要更强模型。
- 不存在订单案例：业务查询失败后没有业务 fact，`only_knowledge` 对剩余 facts 的全称判断为真，把混合请求误归为纯知识生成。政策回答有依据，但没有表达订单查询失败，被完整性检查拦下。判断应考虑整个工作结果，而非仅存活的 facts。
- 安全弃答模板直接呈现内部 owner 名、工具 JSON 或 TOOL_ERROR，尚未满足面向用户的结果表达要求。需要在 ResponseAssembler 统一处理有依据的业务结果与部分失败。

这些属于答案合成和部分成功语义，不应靠召回权重或扩大 Top-K 修复。本轮不声明混合问答闭环。

## 复现与证据

使用项目 `.venv`，设置仅用于测试的 `TEST_DATABASE_URL`、本地 BGE 模型路径及正常 API 配置：

```bash
MODEL_INTENT_REASONING=low MODEL_INTENT_MIN_COMPLETION_TOKENS=2048 \
.venv/bin/python scripts/run_rag_tool_calibration.py --mixed-business \
  --model /path/to/pinned/bge-m3 --output /path/to/new-output
```

脚本新建独立数据库和临时 Redis，最终清理；不修改生产订单，仅注册只读业务工具。知识语料为 19 篇模拟政策，没有加入公共干扰文档。历史指代通过真实 Memory 接口预置对话，未验证跨会话长期记忆。

原始请求、输出与工具记录在 `artifacts/eval/rag-mixed-business-2026-09-06{,-v2}/mixed-cases.jsonl.gz`。`mixed-manifest.json` 是实际五例的定义；早期 `manifest.json` 保留的是语料准备阶段的二十例定义，已在 summary 中注明，不作为运行数量依据。未来脚本已修正这一歧义。API 调用、停止原因、原文校验值见各自 summary.json。

## 第二轮：阶段配置与部分结果修复

保留同一 ConversationAgent，provider 显式接收 INTENT 与 SYNTHESIS 两个配置，最终请求校验使用相应角色。生产组装为两阶段分别预留实际输出预算；所有 provider 构造入口同步迁移。没有更改规划提示词，也没有提高规划预算。合成阶段使用已有 SYNTHESIS 配置（本轮 Pro、关闭 thinking），请求上限 800，而不是沿用 INTENT 的 Flash/low/2048。

ResponseAssembler 的纯知识路径现在要求每个结果成功、有知识 facts、无其他 facts 或动作凭证，并且没有缺失要求与冲突。业务失败没有 facts 仍须进入混合结果合成。全部合法 AgentResultStatus 的无 facts 结果均验证进入组合路径，同时保留纯知识成功生成的正例。

v3 实际运行五条全部执行两种工具，四条通过知识支持检查。相对 v2 救回 shipping、missing，paid 由成功转为弃答，净增 1/5。这是两项修复共同作用下的小样本开发结果，不能分别归因或声称统计稳定。paid 的合成内容包含政策与订单事实，但返回普通文本而非 JSON，解析失败；未调用语义 verifier。missing 现在如实说明无法获取订单状态，同时给出有证据的政策，支持检查通过。已确认：阶段配置正确也不保证结构化输出契约可靠。

59 项相关测试通过，独立审查未发现上述两项修复的提交阻断问题。下一步仍需修复生成输出契约、内部编号/模块名呈现、fallback 的原始工具 JSON。v3 捕获与摘要位于 `artifacts/eval/rag-mixed-business-2026-09-06-v3/`。其中部分回答展示了内部 claim ID；支持检查通过不代表展示质量通过。

## 第三轮：结构化合成与固定输入重放

SYNTHESIS 请求改为指定 `submit_composed_response` 工具，schema 声明非空 response、非空且唯一的 used_claim_ids。适配器只接受一次完整、正确工具调用，拒绝截断、错误工具、重复输出及非法值；schema 计入完整请求预算。原规划路径不变。65 项相关测试与独立复核通过。真实 provider 校准仅覆盖当前 Pro 非思考模式；其他推理配置兼容性未由真实调用验证。

完整 v4 重跑五条：三条在规划阶段失败，paid 的结构正确但缺政策引用，被发布门禁拦截，missing 通过支持检查。最终 1/5，通过数低于 v3，不能宣称端到端提升。该结果说明五例重复规划的波动会遮蔽合成组件的变化；保留全部失败捕获，不挑成功案例。

为节约并隔离变量，新增 `scripts/run_conversation_compose_replay.py`，从 v3 原始捕获提取五条实际合成 payload，仅执行五次合成 API，无数据库、订单操作、规划、召回或重排。冻结 payload 包括完整问题、allowed_claims、工作结果状态。可复现命令：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_conversation_compose_replay.py \
  --capture artifacts/eval/rag-mixed-business-2026-09-06-v3/mixed-cases.jsonl.gz \
  --output /path/to/new-compose-replay
```

重放输出 5/5 结构合法、声明的 claim ID 均来自输入、5/5 至少有政策引用；这只是语法与成员关系指标，没有运行语义 verifier。人工检查：delivered/reference 仍各展示内部 claim ID；missing 的 used_claim_ids 虽包含失败 outcome，正文却没有表达订单查询失败。paid 的“尚未显示发货或物流信息”也需要结合实际工具覆盖核查，不能从订单状态查询推断已查询物流。故结构正确不能替代必要事实覆盖和来源支持。

证据：`artifacts/eval/rag-mixed-business-2026-09-06-v4/` 与 `artifacts/eval/rag-compose-replay-2026-09-06-v1/`。下一步在回答契约和发布所有者处处理逐项结果覆盖与面向用户的引用呈现，同时修复失败模板泄漏工具 JSON。完整 RAG 目标仍未完成。

## 第四轮：工具事实与用户答复的转换边界

本轮没有调用推理 API。根因位于 TargetToolExecutor：把 `output_for_model` 作为 `candidate_response` 交给发布层，后者遂将工具 JSON 当成已经撰写的用户回复。修复后直接工具执行只产出完整 FactRecord 与依据引用，不产出用户答复；ResponseAssembler 对历史持久化的同类直接结果也不再直出其 candidate。单一直接事实结果有 composer 时走合成，纯知识仍走已有 grounded generator。

失败模板不再 JSON 序列化事实，也不展示内部 owner 名和 reason code。受支持的 `order.current_state` 四种状态（paid/shipped/delivered/cancelled）由已验证事实生成简短中文状态；全部已提交动作凭证保留，其他非成功状态逐项表达。未支持模板的退款、账户等复杂事实在模型不可用时明确提示无法整理可靠答复。这是有意的降级行为变化；正常合成仍收到完整原始事实，不能据此宣称这些领域的降级答案功能已齐全。

独立审查发现并修复同一因果链上的两个既有缺口：后续工具失败时丢弃前面累积 facts/evidence_refs；模板只展示首张凭证或在结果失败时完全忽略已提交凭证/已验证状态。现在失败不能抹掉此前已取得的事实和引用，模板同时保留受支持的状态、所有已提交凭证与未完成状态。

验证为 63 项通过、1 项依赖外部 PostgreSQL 的测试未运行，包括直接执行器→事实→合成/降级、旧直接结果、各非成功状态、后续 error/timeout/rejected、多凭证与部分失败。另以 v3 保存的五条真实工具返回进行无 API 重放：四条已知订单均保留编号和正确状态；不存在订单呈现失败；强制知识弃答时没有原始工具 JSON、内部 owner 或 TOOL_ERROR 泄漏。旧 cutover 测试中的 SHIPPED 已改为真实 OrderStatus 的小写 shipped；知识工具测试由断言直接答复改为断言事实保留而 candidate 为空。

独立复核未发现本轮新增阻断。尚未解决正常合成中的内部 claim ID 引用、必要结果的语义覆盖以及规划不稳定；不将本轮降级表达修复计为答案准确率提升。

## 第五轮：内部归因呈现与确定性结果提示

ResponseAssembler 在最终语义核验前处理合成文本：仅移除同时存在于允许 claim 集合和 used_claim_ids 的精确内部方括号引用，保留原 used_claim_ids 供审计，保留政策 evidence 引用；未知内部引用或裸露内部 claim ID 拒绝发布。非成功工作结果由实际运行结果生成中文提示，补入最终文字，同时将相应 outcome 记入最终归因。提示的状态、owner 必须与对应权威 outcome claim 一致，不允许投影自相矛盾。模型正文仍须通过原来的语义检查；确定性提示不能替正文的矛盾结论提供豁免。

验证 114 项通过、1 项依赖 PostgreSQL 的测试未运行，包括未知引用拒绝、结果投影不一致拒绝、语义 verifier 确实收到清理和补充提示后的最终文本。对新保护条件加入后的当前实现做零 API 重放，五条呈现文本与评测捕获完全一致。

新增 `scripts/verify_conversation_presentation_replay.py`，冻结上轮五份模型输出，不重新规划、召回或生成，只分别核验原始和呈现后文本，共 10 次 API。原文四条 PASS、一条 UNKNOWN（missing 漏掉订单查询失败）；呈现后五条 PASS。该单次小样本模型核验只支持“展示修复后的核验结果”，不是端到端准确率或五例语义正确率 100%。脚本未重新检查实时来源，生产入口的来源复验保持原有调用。

独立审查未发现新增代码阻断，同时指出 paid 的“尚未显示发货或物流信息”只能解释为本次返回未包含这些信息，不能证明后台没有物流记录；核验器理由把它进一步解释成“未发货”，不应当成来源证据。本轮不改变这段业务正文，也不把核验器 PASS 当作无条件正确。下一步仍需验证正常完整入口、规划稳定性和更大规模领域验收。

```bash
PYTHONPATH=. .venv/bin/python scripts/verify_conversation_presentation_replay.py \
  --capture artifacts/eval/rag-compose-replay-2026-09-06-v1/cases.jsonl.gz \
  --output /path/to/new-presentation-replay
```

证据保存在 `artifacts/eval/rag-presentation-replay-2026-09-06-v1/`，包含原文、最终文本、两次核验理由、调用与 checksum。完整 RAG 优化仍在进行。
