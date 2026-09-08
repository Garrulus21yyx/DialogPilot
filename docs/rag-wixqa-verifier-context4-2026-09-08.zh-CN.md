# WixQA 核验上下文对照：未采用（2026-09-08）

当前调用链 ResponseAssembler → AnswerVerifier → verify_claims → structured_call。实际使用supported/answered/approval_terms_complete/issues整体合同，历史逐项协议不能描述当前运行。已有答案和证据绑定仍在，但语义支持由模型判断。

实验冻结上一轮高度题均衡答案与其首段有来源的修订；分别使用完整运行证据和仅知识证据。所有知识packs逐字不变，不重新检索、不生成新答案。Flash NONE、同SYSTEM/schema、4次调用、无SDK重试，不修改生产。

| 输入 | 原答案 supported | 有依据首段 supported | 原答案输入tokens |
|---|---|---|---:|
| 完整运行上下文 | true | true | 8056 |
| 仅知识packs | true | true | 4510 |

四次answered也均true、issues为空。首段修订只覆盖本例明确的高度调整问题，未用它替代复杂多意图答复。原答案附加H字段/viewport说明的引用分别描述elements/strips，未建立sections适用性；这里是作者来源充分性判断，不据此宣称实际产品功能相反。

结论：本例缩短约44%输入没有改变语义判决，不能把漏检归因于重复运行上下文，也不能证明重复上下文在所有任务都无影响。保留完整业务证据生产合同；不让混合业务答复丢掉回执/授权等材料。没有把降低输入tokens当成准确率收益。

独立脚本audit_wixqa_verifier_context4.py核查4次请求hash、成对答案、知识packs、同system和调用数，全部通过。这些机械检查不判定自然语言支持性。最初系统python缺jsonschema，改用项目.venv执行审计通过，未重跑模型。

已有MTRAG对象焦点prompt和Pro诊断保留，禁止再次按案例关键词叠加核验补丁。下一项以生成端为变量：同原始证据比较默认回答与只解释有明确适用依据的必要步骤，保持现有核验与总调用预算，观察无依据附加结论是否减少、必要信息是否丢失。需先固定跨案例输入和评价标准，再运行；不得把整类开放问答改成只复制一句话的模板。

产物artifacts/eval/wixqa-verifier-context4-2026-09-08含冻结输入、完整4次输出和审计。整体RAG和语义可靠性未关闭；默认权重不变，微调暂停。
