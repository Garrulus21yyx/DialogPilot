# Target 结果状态合同

同一条执行链输出不同维度，不使用一个 PASS 替代所有结果。

| 字段 | Owner | 含义 |
|---|---|---|
| coverage.complete | ResultBoardSnapshot.coverage_complete | 必要证据齐全且无事实冲突，不等于所有任务成功 |
| task_completed | ResultBoardSnapshot.task_completed | 所有计划任务已有结果、均 SUCCEEDED，且证据覆盖完整 |
| verification_status | AssembledResponse | 本条公开文本的检查状态；模板/安全说明为 NOT_CHECKED；待检查草稿为 PENDING |
| verified / grounded | AssembledResponse.verified | 检查为 PASS 且绑定当前文本 hash；未核验不能标为 true |
| delivery_status | Publication / Delivery | 该回复的提交和送达情况，独立于以上状态 |

Completed 是本轮应用请求返回正式回复，不是客服业务成功。例如：工具不可用，系统正式回复“暂时无法查询”，仍可以 Completed，但 task_completed=false。

安全模板是可交付的应用文案，不伪装成模型检查通过。答案检查失败后生成的不同模板，没有继承被丢弃草稿的核验结果。正常原子工具成功后的模板可以 task_completed=true、verification_status=NOT_CHECKED。

明确 OOS/澄清等无 WorkPlan 的终止路由，不声称完成了一项业务任务；task_completed=false。历史已提交 Publication 不回写重算，新提交使用本合同。API、评测 trace 和持久化投影读取同一 Owner，不保留旧的 requirement→verified 推导路径。

性质测试覆盖所有双任务状态组合与证据缺失/冲突/结果不全组合；另有 HTTP 投影与幂等重放测试。此合同不替代实际业务终态评测或真实数据库恢复测试。
