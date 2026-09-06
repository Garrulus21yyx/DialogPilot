# 引用词表与支持性核验合同的开发验证

前一轮真实混合入口中，audio-defect 与 gift-return 把业务工具 source_ref 当作政策 evidence_id，结构归属检查拒绝后保留安全回退。这个拒绝同时挡住了候选正文中的无依据承诺和错误退款解释，不能证明语义核验已有效。

## 引用由本次请求限定

应用拥有的 composition_schema 现在根据 allowed_claims 枚举可用 claim_ids，并只从 KNOWLEDGE_FACT 中列出政策 evidence_ids。没有知识证据时，evidence_ids 只能为空数组。不存在 claim 的输入在调用 provider 前拒绝；运行时逐段归属检查仍保留，因为全局词表不能证明证据属于某段声明的事实。

使用最新运行捕获的四条合成输入重放（两条失败、两条知识对照），合成与核验共八次 API 调用。四条结构归属均合法，旧 verifier 仍全部 PASS；但人工可见：

- audio-defect 仍承诺“我们会继续为您跟进”，没有已接受跟进任务的依据。
- gift-return 将 refund_window_missing／refundable_until=null 解释成“不在可退款时间窗口内”，并未回答已使用赠品的全额退货问题。
- elliptic 将 delivered 说成“已签收”，输入没有承运签收事件。

因此不能把消除结构拒绝或四条 PASS 称为答案收益，必须补齐真正的支持性合同。

## 核验所有事实与承诺

原 AnswerVerifier 的提示仅要求“没有编造高风险事实”，不足以表达事实支持与完整性。现在系统消息明确要求逐项核对原始业务事实、政策原文、执行依据及独立用户目标；缺失数据只能支持未知，承诺需要已接受任务或履约依据。合法引用不能替代内容支持。待检查数据以 JSON 单独传入，不与校验指令混写。

模型返回 UNKNOWN 表示证据含义无法判断，原因归为 UNGROUNDED；实际调用或解析异常仍为 VERIFIER_UNAVAILABLE，避免把证据不确定误报成服务故障。两种状态都不可发布。

冻结上述四条答案，只重新核验，四次 API 调用：

| 案例 | 原模型核验 | 新模型核验 |
|---|---|---|
| 拆封非质量耳机退货 | PASS | PASS |
| 审核通过是否到账／跟进承诺 | PASS | REJECT |
| 已用赠品全额退款／窗口缺失 | PASS | REJECT |
| 历史否定退货条件 | PASS | PASS |

这是已消费开发答案的核验诊断，没有重跑 Agent、检索或发布，也不能推算端到端成功率。历史否定案例的 delivered→“已签收”仍被漏检，说明通用模型核验不能替代业务字段含义。后续需在业务读模型／工具结果拥有者明确记录时间、业务事件时间与状态语义，并贯通合成和核验输入；不在答案后处理处临时改写句子。

## 验证与复现

90 项相关测试通过，包括实际 JSON Schema 词表约束、逐段归属保留、provider 完整预算检查、支持性状态与故障区分，以及指令与不可信数据分离。API 请求和原始输出保存在两个独立目录，合计十二次调用。

```bash
.venv/bin/python -m scripts.run_conversation_compose_replay \
  --capture artifacts/eval/rag-mixed-segments20-2026-09-06/mixed-cases.jsonl.gz \
  --output /path/to/vocabulary-output --verify \
  --case-ids expanded-audio-defect expanded-gift-return expanded-opened-audio expanded-elliptic

.venv/bin/python -m scripts.run_answer_support_replay \
  --capture /path/to/vocabulary-output/cases.jsonl \
  --output /path/to/support-output
```

第一阶段历史结果由当时的旧 verifier 产生；当前代码运行第一条命令会使用更新的核验合同，不能要求重现旧标签。源请求、模型配置、哈希和实际返回是原结果依据。默认模型和规划预算未调整，完整目标继续保持未完成。
