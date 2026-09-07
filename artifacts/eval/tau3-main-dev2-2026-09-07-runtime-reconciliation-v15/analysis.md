# v15：固定版本验证，未闭环

运行版本为 `490ca53`，使用独立 worktree，原 train 任务 0、1，seed 300。
模型、预算和环境版本见 manifest。未择优重试，未改写模拟用户消息。

| 任务 | 官方结果 | 中断前情况 |
|---|---|---|
| 0 | ERROR / reward=null | 3 次应用回复，5 次官方只读调用，0 次换货写入；等待审批后模拟用户返回空消息 |
| 1 | ERROR / reward=null | 1 次身份追问后模拟用户返回空消息；0 次换货写入 |

两次异常均为 `UserMessage must have either content or tool_calls`。这是观察到的模拟器消息构造失败；未证明底层供应商为何生成空内容，不把它改记为业务得分 0 或通过。

任务 0 的只读调用为 find_user_id_by_name_zip ×1、get_order_details ×1、get_user_details ×1、get_product_details ×2。没有 observed_operation_status；但本次未执行到写后查询，不能据此单独证明写后对账闭环。

应用回复均为原生文本，记录的四次完整答案核验均 pass，application-errors.log 为空。任务 0 能表达退款方向及资料不足，无旧 segments/support_ids 格式拒绝。模型核验 pass 不是独立人工质量分。

任务 0 从收集支付方式的 FIELDS 转为正式 APPROVAL。用户同时表示同意并提出兼容性和流程前置问题；不能将本次再次询问简单定性为无条件重复确认。v14 已观察到的重复确认、渠道说明误判和写后状态表达问题仍未通过本次验证排除。

## 实现验证与限制

- 原生回复迁移固定 fc56b1e 回归：483 passed，包含真实 PostgreSQL。
- 对账权限修复：独立复审及实际 _ToolReconciler / ToolManager 调用链测试已覆盖真实 operation_key、未知 key 和自主 Agent 权限边界。
- 固定 490ca53 追加原生合同、工具绑定与规划回归：42 passed。
- 不添加商品、订单、Google Home 等专用生产分支，不通过虚构操作 key 或把 UNKNOWN 改成成功消除症状。
- 当前状态：`customer_dialogue_quality_open`。业务成功、用户表达、模拟器稳定性分别记录；本轮没有新的业务闭环成绩。

下一步应先定位模拟用户空输出的 SDK/供应商边界，并以保留结果核验回复与审批连续性，避免重新执行业务写入来调试表达。自然文本迁移已完成的实现证据，不替代这两项剩余验收。
