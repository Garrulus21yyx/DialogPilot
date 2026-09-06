# 来源范围目录：共享工具接线准备

已在 PostgreSQL 知识来源 owner 增加 `applicability_catalog(as_of, expected_generation=None)`。它从可检索来源的 metadata 生成范围目录，返回 product/region/channel 标识、关联来源 ID/版本/标题、生效区间、租户/backend/generation/manifest、查询时点及目录内容 hash。标题只供理解来源，不能作为模型指令或适用主体证明。

查询不读取正文或计算 embedding，复用检索的时序/撤回谓词，因此过期、撤回的新版本不会让已被替代的旧版本重新生效。调用方可要求匹配已经固定的 generation，读前不匹配或读期间 active generation 改变时返回 GenerationConflict。目录中只保留来源实际登记的 opaque scope ID，不从显示型号猜 ID。

## 验证

五项真实 PostgreSQL 测试通过，外部推理调用为零。新增验收覆盖：

- 型号显示名 B20 与 canonical scope `catalog:part:B20` 保持区别；目录关联实际来源版本。
- 历史生效版本、新版本生效、过期、撤回及禁止旧版本复活。
- 同一状态/时点重复读取结果稳定；内容或时点变化反映在 hash 中。
- 固定 generation 不匹配、读取期间 generation 切换均明确失败。
- 不执行 query embedding；不接受无时区时点。

## 未完成的边界

这只是来源目录接口，生产 Agent/handler 尚未接入。它证明某份来源登记了一个范围，不证明这个范围适用于用户问题中的机器、配件或其他主体。别名映射、主体选择依据和共享工具校验还未实现。

独立审查提醒：withdraw_revision 可以在不改变 manifest 的情况下使目录过时。目录是 metadata SELECT 时点的观察；generation 检查不是使用时有效性证明。后续 handler 必须匹配固定 generation 和 as_of，并重新验证最终范围及其来源。不能只因缓存目录的 ID 存在便认定允许使用该约束。

下一阶段将把来源目录、模型条件选择和共享知识工具连接起来，覆盖 ConversationAgent、领域 Agent 及直接工具调用。未知条件与无效的显式过滤必须区分，不能把无效过滤静默删掉。这一阶段未完成前，不宣称 scope 问题已经修复，也不将目录测试计作 RAG 准确率提升。
