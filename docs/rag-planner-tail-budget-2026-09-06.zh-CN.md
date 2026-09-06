# 已截断规划的五条定向重放

沿用 expanded20 实际 ConversationAgent 捕获的五条规划输入，保持 Flash/low、提示词及上下文，仅把请求输出上限从 2048 改为 4096。没有注入最新 goal_descriptions，也没有重跑订单、检索、合成或发布。共五次 API 调用，无 SDK 重试。

| 案例 | 原调用停止原因 | 新调用 output tokens | 重编译结果 |
|---|---|---:|---|
| 赠品退货 | max_tokens | 2384 | 订单查询＋知识查询 |
| 人为损坏保修 | max_tokens | 935 | 订单查询＋知识查询 |
| B20 试装 | max_tokens | 2026 | 订单查询＋知识查询 |
| 积分退款 | max_tokens | 756 | 订单查询＋知识查询 |
| 历史否定／拆封耳机 | max_tokens | 2028 | 订单查询＋知识查询 |

五条均正常结束。历史否定查询为“耳机已拆封且非质量原因的退货适用规则是什么？”，指代和否定未丢失。B20 问题生成了英文检索表达，是否影响中文语料的词面召回尚未测，不能把编译成功等同检索成功。

实际输出共 8129 tokens，原五次截断输出共 10242 tokens。上限翻倍没有让实际用量翻倍；但四条新输出仍小于 2048，存在采样波动，不能把五条恢复全部因果归于预算提高。这里只有赠品案例本次完整输出确实超过旧上限。这是按失败选择的开发诊断，不是 heldout，也没有成功样本上的误伤估计，不调整生产默认模型或预算。

## 评测边界修正

B20 案例原捕获的 order_id 绑定为 AMBIGUOUS：DP9301 和 B20 都是当前文本中的候选，模型明确选择 DP9301 及对应 source_ref。原重放器只接受 UNIQUE，因此虽然生成成功，编译前被评测器拒绝。

现在按捕获的最高优先级候选重建 UNIQUE／AMBIGUOUS，要求 EntityBindingSet.as_payload 与原绑定投影完全一致，再交实际编译器验证选择。旧候选的授权／过期／工作流状态不在此重放范围；不可重建状态在 API 调用前失败。原 cases 捕获不改写，summary 明确记录重编译修正；没有追加 API。此修复属于评测边界，不是生产实体识别改进。

验证：34 项规划 schema 与重放测试通过，包含多候选明确选择、状态不一致拒绝、STALE／UNAUTHORIZED／MISSING 拒绝。尚未证明整个 Agent 上下文和生命周期能从投影重建，只支持文档声明的空活动状态规划重放。

## 复现

```bash
MODEL_INTENT=deepseek-v4-flash MODEL_INTENT_REASONING=low \
MODEL_INTENT_MIN_COMPLETION_TOKENS=2048 \
.venv/bin/python -m scripts.run_conversation_plan_replay \
  --capture artifacts/eval/rag-mixed-expanded20-2026-09-06/mixed-cases.jsonl.gz \
  --output /path/to/new-output --modes text --max-tokens 4096 \
  --case-ids expanded-gift-return expanded-warranty expanded-battery-install \
  expanded-membership expanded-elliptic
```

下一步仍是合成合同中的结构化证据关联及业务答复支持性检查，优先用已捕获输入验证，再做混合入口验收。本轮不能声称 20 条端到端结果从 5 条成功提升为 10 条。
