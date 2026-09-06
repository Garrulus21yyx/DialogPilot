# 退款事实受控表达接线

本轮完成三项修复中的退款事实表达子合同。业务owner按FOUND五种已知申请状态与NO_APPLICATION无记录状态生成可选原文；没有新增Agent或模型。NO_APPLICATION要求原始订单版本，不能含申请状态；FOUND要求申请ID和已知状态，输出明确为本系统申请记录状态，不推导权益、签收或账户到账。

ResponseAssembler仅根据权威requirement refund.current_state及VERIFIED_STATE来源种类产生CONTROLLED_REFUND_FACT，不根据任意JSON中有同名字段推断类型。composer输出fact_ref/statement_id；服务端以来源内容及原文绑定的目录恢复文本。受控support不能用于自由文本，过期statement_id及篡改目录拒绝。

同一成功退款工作项的可自由表述outcome不再作为旁路来源；失败和部分结果仍保留。单结果候选不再绕过受控渲染直接发布，安全回退复用同一业务owner原文。其他知识文本仍走现有核验，尚未迁移到原子核验或一次修订。

87项针对性测试通过，覆盖所有已支持退款状态、片段组合和顺序、来源版本变化、非法字段/引用、自由重写拒绝、事实类型归属、单结果及回退。旧无受控事实的合成合同仍保持v3；受控目录请求使用v4-fact-refs，provider版本v10。回放工具已保留新的受控事实种类作为原始核验证据。

真实入口两例（已消费开发样本）全部Flash，7次API：
- refund-state：3次调用，最终仅为“订单 DP9302 在本系统当前未记录到归属于您的退款申请。无法从当前系统记录确认退款是否已经到账。”每句由服务端目录恢复，无权益扩写。
- refund-both：4次调用，同两条受控原文加知识政策段落，引用存在、现有核验通过；政策段落仍包含多余故障检查说明。不能报告零幻觉或总体准确率。

产物：artifacts/eval/rag-controlled-refund2-input-2026-09-06/cases.json 与 rag-controlled-refund2-2026-09-06/{mixed-cases.jsonl.gz,summary.json,manifest.json,mixed-manifest.json,completion.json}。捕获断言所有模型为Flash，选中的受控原文保留在最终答案。实际检查只证明受控片段未改写，不能证明自由政策文本全部正确或需求全部覆盖。

待完成：逐项verifier接管开放文本、最终发布程序检查hash及证据版本、最多一次局部修订、新分组变异和混合入口验收。当前发布仍使用原整段verifier，不宣称用户要求的三项修复已全部完成。

独立复核发现并补齐：controlled退款在PARTIAL/失败时的outcome不再提供自由support，摘要清空，原有程序按typed status追加确定通知；旧refund-view-v1缺少lookup_status时抛业务owner的UnsupportedRefundObservation，安全回退提示重新查询，不猜测FOUND也不调用合成模型。新增partial及旧事实真实assemble测试。最终89项测试通过。历史捕获仍是历史协议，未提供隐式FACT→CONTROLLED迁移；新捕获使用显式受控kind。
