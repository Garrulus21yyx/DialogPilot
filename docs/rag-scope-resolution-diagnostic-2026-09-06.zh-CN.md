# 适用范围解析边界诊断

## 本轮结论

scope SQL 正在正确执行精确过滤，但应用没有验证模型给出的 product/region/channel 是来源体系里的哪个适用范围，以及它是否对应问题的适用主体。编号类型修复不等于完成了此映射。

实际混合六条中的 B20 请求由模型提供 `applicable_product=B20`。当前合成语料的通用来源仍可被召回，所以这一条最终答对不能证明该过滤参数有依据。配件 B20、机器型号和来源的 product scope 是不同概念。

## 零 API PostgreSQL 实验

固定三份合成来源、相同 query、generation 与请求时点，使用测试 embedding（恒定向量，只检查适用谓词，不评排序）。来源为：B20 专用（scope=`catalog:part:B20`）、其他配件专用、通用。

| 传入 product 约束 | 候选来源 | 说明 |
|---|---|---|
| `catalog:part:B20` | B20 专用、通用 | 精确范围保留通用来源 |
| `B20` | 通用 | 显示型号与 scope 不一致，专用证据被排除 |
| 未指定 | 三份全部 | 找回专用证据，同时放入其他商品范围 |

证据重读也用同一适用谓词：正确 scope 下的候选重读通过，改为显示型号约束后拒绝专用候选。这证明约束一致执行，不能靠打包或重读挽回最初错误的范围选择。

实现为 `tests/test_knowledge_applicability.py::test_display_product_label_is_not_a_canonical_source_scope`。三项真实 PG 适用性测试通过，推理 API 为 0。此实验是构造的机制反例，不是生产命中率或答案收益。

## 责任与最小迁移范围（尚未实施）

- SourceDocument/SourceRevision 的导入 owner 拥有来源 facet 标识。当前 product 是 opaque 字符串，没有显示名/别名/主体角色映射合同。映射必须由来源或业务目录 owner 提供并随 manifest/generation 固定，不能由模型临时造一个 ID。
- ConversationAgent 和领域 Agent 可提出检索条件；应用转换边界需要区分对象提及与适用主体，保存可核验的选择依据。仅向模型给 ID enum 只能证明 ID 存在，不能证明它适用于这次问题。
- 两条 Agent 路径共用的 knowledge_search handler 必须校验已解析范围。明确但无依据的硬过滤应产生 typed invalid/unresolved；未知条件可保持未指定范围。不要静默把无效过滤改成无过滤后宣称请求成功。
- 检索缓存、trace、候选重读与 Publication 应使用同一最终约束及其解析依据。SQL 精确匹配和通用来源规则保持；region/channel 的同类转换边界需要一起覆盖。身份/权限仍由系统注入，与业务适用范围分开。

替代方案：仅改提示无法验证来源关系；仅列枚举无法验证主体；取消过滤引入其他范围；将 SQL 改为模糊匹配破坏适用合同。因此下一步应在共享工具边界贯通由 owner 提供的范围选择，而不是继续调 ranking 权重掩盖问题。

独立只读审查确认 ConversationAgent options、领域工具 schema、共享 handler 都只有形状校验；检索与重读使用 exact/global 谓词，缺口在上游转换。此轮只完成诊断与可复现反例，没有实施 scope 解析迁移，没有关闭整体 RAG 目标。
