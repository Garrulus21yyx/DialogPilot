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
