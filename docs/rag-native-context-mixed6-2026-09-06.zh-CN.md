# 固定业务条件的混合入口六例回归

本轮沿用 rag-reference-mixed6-input-2026-09-06/cases.json，不改订单快照、问题、政策来源和预期工具。统一 TargetChatApplication / ConversationAgent 真实运行，Flash low、规划最低完成预算4096。使用当前生产原生规划、上下文传递和 support_id 证据组合。六例为已消费模拟开发案例，不是封存验收。

| 指标 | 旧 owner-repairs-mixed6 | 本轮 |
|---|---:|---:|
| 预期工具覆盖 | 5/6 | 5/6 |
| 预期来源实际可见 | 5/6 | 5/6 |
| 预期来源被引用 | 4/6 | 5/6 |
| 知识支持检查通过 | 4/6 | 5/6 |
| API调用 | 22 | 23 |

引用及支持检查救回 expanded-elliptic 一条、误伤零条，样本内净增16.7个百分点。旧案例存在APIConnectionError；本轮同时包含上下文、support_id和原生规划变更。因此这一差值不是纯代码改善的因果估计，更不能称为检索召回率或最终答案准确率提高16.7点。

## 逐例发现

- 拆封耳机与省略式“不是”：实际查询、答案保留非质量原因；仍附加大量质量问题等旁支说明。
- 型号未知：未将B20误绑定订单，正确建议核对铭牌并拒绝试装。
- 预售尾款：正确区分支付与发货。
- 赠品已使用：正确保留“核实活动规则、不能承诺全额退款”。额外调用只读refund_eligibility_check，并说明暂时无法提交退款，超出用户本轮必要信息；没有执行写操作。
- “质量审核通过是否代表退款到账”：订单和退款状态读取合理，答案没有虚构到账状态；但没有明确解释审核状态与到账状态不等价，关系问题覆盖不足。不能只因未调用knowledge_search便判为检索失败。

下一步关注统一计划对多项信息需求的覆盖，以及生成对相关证据的选择。保持真实业务读取用于业务事实，政策依据用于关系解释；不硬编码某个问法必须调用某工具。

## 可复现产物

artifacts/eval/rag-native-context-mixed6-2026-09-06 包含原始压缩捕获、输入运行manifest、阶段summary、配对comparison与逐例answer-review。人工复核是本次编码Agent的开发检查，不是独立答案标注。

重新评分无需API：

    .venv/bin/python -m evaluation.rag_mixed_report --capture artifacts/eval/rag-native-context-mixed6-2026-09-06/mixed-cases.jsonl.gz --definitions artifacts/eval/rag-reference-mixed6-input-2026-09-06/cases.json --output /tmp/mixed6-summary.json
    .venv/bin/python -m evaluation.rag_mixed_comparison --before artifacts/eval/rag-owner-repairs-mixed6-2026-09-06/summary.json --after /tmp/mixed6-summary.json --output /tmp/mixed6-comparison.json

比较器拒绝定义hash不同、案例不完整、ID重复、不一致或缺少明确布尔阶段信号的输入；7项测试包括全部两案例状态组合的配对计数守恒。比较器本身不验证答案语义，不从成功个案中排除失败分母。

独立复核核对两份summary及捕获hash、配对计数和7项测试通过。另指出新省略式答案把“已送达”解释为“物流签收状态”，不能由阶段PASS推断这类状态语义完全正确；后续需对照业务字段含义复核。
