# 最新诉求与历史背景：真实 Context → Agent 四例诊断

本轮起点 `1d26f9f`。这是4条预先编写并冻结的中文电商模拟开发案例，不是 MTRAG 的四条原题，也不是新鲜验收。公共领域问题不能强制电商 Agent 接受。未改生产 prompt、召回或精排策略。

## 入口与成本

历史写入隔离 PostgreSQL，沿现有 memory/context loader、manager.prepare、Conversation Agent planner，捕获实际 provider 消息和计划。没有手工构造 TargetTurnContext，没有执行检索、业务工具、生成或发布。隔离数据库已删除。

4次 deepseek-v4-flash 调用，输入17,648 tokens、输出420 tokens（总18,068，供应商用量含缓存口径）。每例只有一次规划，未重试。1项产物审计通过，逐条核对实际模型输入的历史角色和原文、当前消息、只读知识计划。测试不声称证明查询语义。

4条均为冷投影 DEGRADED/CURRENT_CONTEXT_PROJECTION_LAGGING；但实际模型输入中历史均2/2完整可见。不能把此状态等同于上下文丢失，也不能据本次说明所有投影情形正常。长期记忆本轮NOT_REQUIRED，未测试长历史压缩。

执行时工作区含其他任务 runtime 修改，`source-hashes.json` 在运行后记录实际源码身份；不是干净提交全链验收。原始 manifest 的 `synthetic_ecommerce=false` 来自旧脚本仅按 `--ecommerce` 开关填写；本次使用外部fixture，该字段不准确。保留原文件，本报告和cases.json明确四条均为模拟。脚本已修正为从外部fixture的synthetic字段推导，未为修正元数据重跑模型。

## 逐例核对

| 案例 | 实际查询概要 | 判断 |
|---|---|---|
| 已拆封耳机，否认质量原因，问无理由退货 | 英文查询保留耳机、拆封和no-reason，但追加括号unconditional | 保留主要目标；同义扩展有语义增强风险。无理由不自动意味着没有任何条件。本轮是查询而非肯定答案，不能据此计为答案幻觉 |
| 比较两类会员后，用户选择先问企业会员 | 企业会员和普通会员区别、各自权益 | 包含企业会员目标，也保留旧比较范围；不是漏掉目标。是否挤占候选预算未测，不能直接判失败 |
| A款耳机保修→明确改问所有商品通用退货流程 | 所有商品通用退货/退款流程 | 未带入A款或保修限制；保持新主题。并未证明知识库确有统一适用规则 |
| 申请条件→明确改问审核通过是否代表到账，不查订单 | 审核通过与到账的关系、后续处理阶段 | 未继续查询资格条件，未计划业务工具；查询保留疑问而非虚构到账结论 |

可确定：实际历史注入4/4、生成知识查询4/4、只读知识计划4/4、明确话题切换两例均未携带旧限制。不能把这些计为“查询准确率100%”或Recall提升。

## 决定与下一步

本轮没有证据支持重构Context或统一增加一个rewrite模型。保留现有Agent负责理解的边界，记录英文扩义与范围拓宽，先检查它们是否造成候选/最终证据损失再决定owner级修复。

原MTRAG诊断仍有融合丢证据与文档内定位缺口；不能用这四条模拟成功代替原失败修复。下一步回到已冻结MTRAG三组精排结果，完成打包/知识工具序列化的同预算重放，确定候选收益能否保留；不继续依据四条样本猜词调prompt。真实Agent到最终答案的封存验证仍开放。

复现（输出必须为新目录）：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_rag_g2_context_misses.py \
  --cases artifacts/eval/rag-g2-query-focus4-2026-09-08/cases.json \
  --output /tmp/rag-query-focus-new-run --call-limit 6
```

需要现有测试PG容器、Redis、provider配置；运行涉及API费用。证据在 `artifacts/eval/rag-g2-query-focus4-2026-09-08/`，只审计冻结消息无需再次调用模型。
