# 原任务 v14：业务成功不等于完整客服闭环

固定提交 fc56b1e，隔离工作副本；与 v13 相同 task 0/1、seed 300、模型与预算。
执行前在该提交重跑 483 项测试通过，包含真实 PostgreSQL，无跳过。

| 任务 | 官方评分 | 业务写入 | 对话结果 |
|---|---|---|---|
| 0 | ALL/ENV/ACTION 1.0，db_match=true | 换货一次；后续转人工一次 | 七轮；第五轮表达核验失败，第六轮错误否认可确认性，仍未闭环 |
| 1 | ERROR / null | 无换货 | 应用正常追问两轮后，模拟用户返回空 UserMessage，未取得官方评分 |

未挑选成功尝试替代失败；两条开发任务不代表 heldout 成绩或线上成功率。

## 可追溯发现

自然文本作者不再出现 segments/support_ids 格式拒绝。输入与审批均通过原生文本链。
任务 0 仍有重复确认、返件说明表达失败和状态误判，不能仅凭官方分数宣称客服体验成功。

状态误判的直接链条：

1. 换货实际提交，Receipt=COMMITTED，实时订单随后显示 exchange requested。
2. 下一轮模型调用适配器附加的 observed_operation_status，自行编造 operation_key。
3. 内部凭证表查不到该编号，返回 UNKNOWN；这不代表那笔已提交换货的状态未知。
4. 该结果进入事实板；核验模型优先采用 UNKNOWN 和本轮 BLOCKED，否定正确订单状态说明。
5. 修订作者改成“无法确认是否成功”，核验反而通过。

Langfuse session：tau3-9b211b2ab40a476585863199f4d665ba。
原作者 trace e79f42fb8dc046cfbeaa4f5377faa4ff；核验 trace 1e51d508b7bfd1c6b50b14ecc92d6208；
修订作者 trace ae0bd665aecbcb46eadcb89b7615ebd9。

根因边界是自主 Agent 工具权限与受控运行时对账权限混合。修复该权限分工，
不是按订单、商品、UNKNOWN 字样或本案例修改答案。

另外两类问题继续单独保留：

- 补充渠道能力与业务政策的表达缺乏可靠依据，核验反馈还错误要求普通业务事实带来源标签。
  原始候选、反馈与发布结果均保留，不将 LLM 判断当客观 Gold。
- task 1 的空消息属于模拟用户输出失败。未更改模拟器、补造用户确认或重写官方评分。

状态：natural_reply_contract_implemented / customer_dialogue_quality_open。
