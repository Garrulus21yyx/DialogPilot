# τ³ 原两条开发任务：追问简化后 v11

## 固定条件

2026-09-07，代码 d6bfcf7（含 41fa261 追问修复），官方源 a2c024725189473d2d7cea3a5cfdbcc67478e41f。
train 任务 0、1，seed 300，deepseek-v4-flash，completion budget 4096，模拟用户 512 tokens，max_steps 80。
沿用既有脚本、工具绑定、官方 ALL/ENV/ACTION 评分；没有修改代码、提示词、预算、模拟器或期望动作。
无重试择优。完整配置和源码 hash 见 manifest.json；HTTP/SSE、多领域路由及 heldout 泛化不在本次范围。

## 结果

| 任务 | 结果 | 官方分数 | 业务写入 | 用户交付 |
|---|---|---|---|---|
| 0：两件商品一起换货 | ERROR | null，未进入评分 | 0 次 | 前两轮追问成功；第三轮 ASSEMBLY_INVALID，未发布 |
| 1：指定键盘缺货则只换温控器 | EVALUATED | ALL/ENV/ACTION 均 1.0；db_match=true | exchange_delivered_order_items 1 次，符合期望参数 | 有效审批后写入成功，但后续多轮通用兜底，用户仍困惑 |

不能把任务 0 的 null 换算成官方 0，也不能把任务 1 的数据库成功当作回答质量通过。
这只是两个反复使用的开发任务，不是总体成功率或简历性能成绩。

## 可证实的变化

- 两个任务都成功发布首轮身份追问并接收用户姓名/邮编，旧的重复 INPUT_REQUEST/outcome 标记堵点不再出现。
- 任务 0 第二轮完成身份、订单和两次商品查询，追问用户选择替代款。
- 任务 0 第三轮返回 `target_interaction_assembly_invalid`、retryable=false；没有进入原来的队列空等超时。
- 任务 1 经有效审批后提交一次温控器换货；官方数据库和动作核验通过。

## 尚未闭合

1. 任务 0 第三轮在表达转换/校验阶段失败。已有日志仅给出 ASSEMBLY_INVALID；没有保留足以确定具体转换异常的原因。
   Langfuse 中 assemble_response 和 customer_service_turn 都记录相同终态。
   子 Agent 此时调用 request_user_input，内容同时包含交换详情、退款收款方式和确认请求。
   这证明追问与动作确认仍有职责重叠，**但不能据此断言它就是该次 ASSEMBLY_INVALID 的直接原因**。
2. 任务 1 第 3、4、6、7、8 轮 COMPOSER_FALLBACK，第 9 轮 ANSWER_SAFE_FALLBACK。
   第 6 轮已经写入，公开回复却是“请求已提交”加“无法提供可靠回答”；后续还出现“部分请求未完成”。
   原始写入事实与用户可理解的完成说明没有稳定贯通。
3. 任务 1 共 9 个应用轮次，读取调用多次重复。需要结合每轮目标判断哪些是必要刷新，不能直接把所有重复读取判为错误。
4. 本次未触发瞬时重试；评测入口只 pump_once 的后台持续执行限制仍未验证/修复。
5. 任务 0 清理阶段出现 LangGraph store pending-task warning；与第三轮业务错误分开记录。
   LiteLLM provider 警告涉及成本识别，不应冒充业务失败根因。

## 轨迹计数

- 任务 0：find_user_id_by_name_zip 1，get_user_details 1，get_order_details 1，get_product_details 2；无写入。
- 任务 1：find_user_id_by_name_zip 4，get_user_details 4，get_order_details 7，get_product_details 2，calculate 1，get_item_details 9，exchange_delivered_order_items 1，transfer_to_human_agents 1。
- 任务 1 官方没有 nl_assertions/communicate_info，评分不能替代用户回复质量核验。

## 回查

- 任务 0 Langfuse session：tau3-708d835432a7420baddd29847a3f8a27；失败 trace：39af2872d79ff14b5406ac461e09254d。
- 任务 1 Langfuse session：tau3-383b0152aad644afa8eef094498b39d4。
- 原始公开对话及工具请求：task-0-partial-trajectory.json、task-1-trajectory.json。
- 官方评分、每轮应用结果和最终审核：task-0.json、task-1.json。

状态：partial_business_success / customer_dialogue_quality_open。
下一步应共同核对表达转换错误可观测性、问询/审批职责及写入后交付，不按商品或单句文案添加特例。
