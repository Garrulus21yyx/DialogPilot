# 二十条混合开发案例：规划、证据与发布失败分层

本轮扩大真实应用入口的开发校准，仍由统一 ConversationAgent 理解订单＋政策问题，使用真实隔离 PostgreSQL 订单服务、BGE-M3/PG 知识检索、ToolMessage、合成和发布检查。仅注册只读业务工具，不是 HTTP/领域 Worker/写操作或跨会话记忆验收。

## 目标语义修复

先用冻结五条规划输入比较 Pro/none 与原 Flash/low，输出上限均为 2048。Pro/none 仅三条保留订单和知识两工具，两个失败把“只询问改址规则”选成 change_address。这不支持直接换更贵模型。

原规划输入只有目标名称列表和零散解释，缺少完整的目标语义。现在由 ConversationAgent 编译词汇所有者维护 goal_descriptions，并与 supported_goals 同源，明确知识咨询、业务读取、执行动作和会话取消的区别；provider 引用该定义。没有把错误动作在下游改写成知识查询，也没有放宽实体、依赖或动作校验。

Flash/low 同预算五条重放均保留两工具；相对之前同配置四条，是单次开发校准的改善，不是稳定提升证明。独立审查又纠正了 product_identification/product_assistance 的描述：它们实际需要绑定 asset_id，纯文本资料咨询用 product_qa。运行捕获保留了实际看到的旧描述，最终代码包含这项描述修正；未修改已完成推理的输入记录。

## 扩大范围与结果

二十条输入在推理前冻结，来源是既有二十条合成政策问题与三条订单记录的组合。历史指代案例通过真实 Memory 接口预置上下文。它们复用了已消费政策问题，不是 heldout，也不是二十个真实企业问题。

| 阶段信号 | 条数 |
|---|---:|
| 完成捕获 | 20/20 |
| 调用订单＋知识两工具 | 14/20 |
| 期望政策来源在实际模型工具消息中可见 | 14/20 |
| 最终答复引用期望来源 | 5/20 |
| 通过知识支持检查 | 5/20 |

共 54 次 API 调用，预先上限 120，没有每改一个权重就重跑生成。

失败可以定位到具体阶段：

- 五条规划在约 2048 output tokens 处截断，未调用工具：赠品、保修、电池安装、会员权益、历史否定。
- 九条已经取得期望政策，合成通过结构解析，但正文没写证据引用，因此在语义 verifier 前被拦截。它们是运费、偏远地区配送、优惠券、改址、换货、隐私、包装破损、签收与多证据材料问题。
- audio-defect 保留了订单查询，但改走 refund_status，没走期望知识查询。答复还出现“我们会及时跟进”这一无依据承诺。此案没有进入知识语义检查，说明最终合成支持性检查还存在业务路径覆盖缺口。

两工具调用与 source-ID 命中只代表阶段信号，不能等同最终正确率。模型可能有合理的额外业务查询，必须结合问题语义复核，不按预期工具名机械判全部答案错误。当前结果也没有量化最终答案的稳定提升。

## 验证与标注限制

新增 report 从实际 output_for_model 中重算来源可见性和最终引用，不能用内部完整 artifact 冒充模型所见。测试覆盖该差别及重复案例拒绝；59 项相关测试通过。较早更广测试为 76 项通过、1 项 PostgreSQL 条件测试未运行。

独立审查指出 expanded-elliptic 的辅助 source_text 是原文改写。原输入保留，annotation-amendment.json 给出实际导入原文；该字段未传给模型，当前指标仅用 source_id，没有使用它作字符级 gold。不得据此声称精确 evidence-span 验收。

## 复现

```bash
MODEL_INTENT=deepseek-v4-flash MODEL_INTENT_REASONING=low \
MODEL_INTENT_MIN_COMPLETION_TOKENS=2048 \
.venv/bin/python scripts/run_rag_tool_calibration.py \
  --mixed-business \
  --mixed-case-file artifacts/eval/rag-mixed-expanded20-input-2026-09-06/cases.json \
  --max-api-calls 120 --model /path/to/pinned/bge-m3 --output /path/to/new-output

.venv/bin/python -m evaluation.rag_mixed_report \
  --capture /path/to/new-output/mixed-cases.jsonl \
  --definitions artifacts/eval/rag-mixed-expanded20-input-2026-09-06/cases.json \
  --output /path/to/new-output/summary.json
```

下一步的有界修复合同：规划预算需容纳已观察到的思考与最终结果；合成用结构化证据关联，由发布层渲染引用；全部模型合成答复都接受支持性检查，包括没有知识结果的业务路径。保留既有权限、来源复验、失败状态和动作凭证。优先重放本轮捕获来筛选，再运行新的完整入口案例；不把当前 checkpoint 当作全链路闭环。
