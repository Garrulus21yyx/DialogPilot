# 目标语义校准与部分执行合同缺口

业务读取升级后，ConversationAgent 的退款资格描述仍笼统称为“当前资格”，容易把操作提交预检当成完整退货政策判断。本轮由编译词汇拥有者校准目标含义：refund_policy 可回答已知订单涉及的政策问题；refund_eligibility 仅覆盖订单状态、配置窗口与已有申请；product_qa 可查询未知前置条件下的使用规则，不必先取得媒体 asset_id。

## 六条低成本规划对照

同六条已消费开发输入，使用当前目标描述、相同 4096 输出上限，分别运行 Flash/low 和 Pro/none，各六次 API 调用。使用真实编译器，不执行工具和生成答案。

| 案例 | Flash/low | Pro/none |
|---|---|---|
| 拆封非质量退货 | 订单＋知识 | knowledge_options 中非法 null 导致编译拒绝 |
| 审核通过是否到账 | 订单＋退款状态，漏政策 | 同左 |
| 已用赠品全额退货 | 订单＋知识 | 订单＋知识 |
| 不知机器型号能否试装 B20 | 输出 4096 tokens 后截断 | 订单＋知识，查询为英文 |
| 尾款是否等于发货 | 订单＋知识 | 订单＋知识 |
| 历史否定 | 订单＋知识，但 query 漏耳机、已拆封 | 订单＋知识，保留耳机、拆封、非质量条件 |

两者均为 4/6 两工具；Pro 相对 Flash 救回 B20、误伤拆封案例各一条，不能据此切默认模型。Flash 的一条 query 丢条件，说明工具覆盖不代表查询质量。赠品目标改善也只是开发观察，没有证明最终答案提升。69 项相关测试通过，独立审查确认目标描述与实际工具合同一致。

## 部分可执行不是一个提示词问题

当前代码支持整条 RESOLVED 或整条 CLARIFY，缺少“这个目标能执行、另一个目标缺字段”的规划表示。已核对：

1. ConversationAgent 对 insufficient_context 立即返回空命令；TurnProposal 禁止终态带命令。
2. RoutePolicy 对非 RESOLVED 清空可执行命令；TurnPlanCompiler 不产生 work plan。
3. 即使执行阶段出现 NEEDS_USER_INPUT，TurnRuntime 也跳过合成，TargetChatApplication 优先只发布追问。
4. manager 只保存 NEEDS_USER_INPUT 工作项，去除 dependencies；ResultBoard 将下游标为 BLOCKED，但这些下游不在该保存集合中。
5. orchestration 恢复时清空旧 facts/results，因此现有 suspended 机制不能直接证明依赖续办、完成结果保留或不重复执行。

当前 PendingInteractionState 明确仅支持 READ，并限制与其他 pending interaction/approval 并存。这些约束不能因 RAG 调优而被静默放宽。

## 后续迁移的正向合同

规划必须保存每个目标的身份、条件、缺失字段归属及依赖。独立且授权充分的读取可执行；缺字段目标及其依赖后继保留为待续，不能丢弃。已完成读取的证据和已交付结果应持久保存，补充输入只恢复对应的未完成目标，证据失效时按现有机制重新取得。

部分答案与追问应共同经过适用的证据检查和发布记录；不能直接把答案拼进 challenge 绕过引用、状态或来源元数据。恢复、重试及重复输入必须保持工作项和发布的幂等性。现有审批、工作流和写操作边界保留，不能隐式开放缺输入的写操作。

需要一起核对 TurnProposal/RoutePolicy/compiler、work plan/result board、manager 的 suspended 状态、orchestration 恢复、TurnRuntime、NeedsInput 和 HTTP/publication/history/checkpoint。验收应覆盖独立读取、读取依赖链、补错字段、重复恢复、过期证据、取消与写操作隔离，而不只复测 B20 一条。

这部分仍是确认的合同缺口，尚未实现。目标说明校准、模型对照与此架构工作分开记录；不得把当前六条规划输出称为部分执行功能完成。
