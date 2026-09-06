# 回答阶段的会话上下文：边界诊断与对照

状态：诊断和组件实验完成，生产传递尚未迁移；整体任务保持进行中。

## 已证实的丢失点

TargetConversationManager.prepare 加载 TargetTurnContext，包含消息角色、原文、source_ref、seq、观察时间、摘要及覆盖水位。ConversationAgent 将其投影给规划模型；领域执行也取得 recent_relevant_turns。

但 TurnRuntime._assemble_response 只向 ResponseAssembler 传当前 raw_text 和系统通知。合成 payload 为当前消息、allowed_claims、work_item_outcomes，未带同等历史；AnswerVerifier 也只按当前消息检查。DIRECT 纯知识路径的 GroundedAnswerGenerator 虽支持 history，调用方没有传入。

因此当当前消息是“不是……根据刚才的退货原因说明规则”，而检索 query 又遗漏非质量条件时，合成无法从当前消息还原否定对象。这与 planner 已获得历史并不矛盾，也不意味着必须再增加一个 query rewrite Agent。

## 低成本实验

从 rag-owner-repairs-mixed6 捕获中取同一条 expanded-elliptic，使用新增显式评测参数 --planner-context：

1. 复制该条实际 planner 输入的 conversation_context，含角色、来源和摘要状态。
2. 加入合成模型输入；校验模型收到单独的 user_context。
3. 原始业务事实和知识 claims 不变，用户历史不提升为企业政策来源。
4. 不增加查询生成模型，不重跑检索，也不重写历史捕获。

程序断言证明上下文与来源捕获完全相同，去除新增字段后其他合成输入逐项不变。新增2次API调用（合成+支持性校验）。

没有历史的上一次组件重放主要并列非质量、质量退货规则；带历史的本次输出明确写出“您表示不是质量问题退货”，并据此引用已拆封耳机的适用规则。引用和支持性检查通过。

但是输出仍重复条款，且补充无关质量退货流程及快照解释。单个随机样例不能证明稳定收益，支持性通过也不等于简洁性/相关性合格。前次真实入口的连接失败成绩没有被此次重放替换。

## 最小生产迁移合同（待实施）

- 已有会话上下文所有者提供统一、带来源的模型视图。规划、合成和覆盖校验复用同一语义视图，不分别解析另一份历史。
- 从 PreparedTurn 传递当前已加载的上下文，保持请求和会话边界；不在合成时自行查询另一会话或重新生成记忆。
- 将用户情境与权威业务事实、知识原文分别输入。历史用于消解指代、否定及已知条件；历史里的客服政策说法不能替代当前知识证据。
- 同时覆盖混合合成、DIRECT 纯知识生成、最终支持性/目标覆盖校验、预算处理、checkpoint/replay 和实际入口验收。
- 保留角色、source_ref、水位和不可用状态。缺历史仍应允许独立问题，不能伪造一个“已经消解”的完整条件。
- 输入预算不足时保持明确的预算结果或带记录的裁剪；不能默默丢掉最后一个必要问句，再声称上下文完整。

不使用针对“不是”的词面补丁，不由知识库猜测用户条件，不把用户声称已经退款当作业务工具验证结果。

## 复现及产物

```bash
.venv/bin/python -m scripts.run_conversation_compose_replay \
  --capture artifacts/eval/rag-owner-repairs-mixed6-2026-09-06/mixed-cases.jsonl.gz \
  --output artifacts/eval/context-replay-reproduction \
  --case-ids expanded-elliptic --verify --planner-context
```

产物位于 artifacts/eval/rag-context-elliptic-replay1-2026-09-06，保存 manifest、原始 gzip、SHA256 摘要及精确输入复制断言结果。此参数明确仅供评测，生产 ResponseAssembler 未改变。
