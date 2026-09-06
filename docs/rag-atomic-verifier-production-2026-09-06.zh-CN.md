# 原子核验接入生产与 Flash 开发验证（2026-09-06）

状态：实现阶段提交，事实幻觉与整体 RAG 优化仍在进行。保留既有 synthesizer/verifier；没有新增 Agent、默认升级 Pro 或重执行业务动作。

## 根因与合同

已知漏洞是退款查询的 NO_APPLICATION 被生成器扩写为资格承诺，整段核验又被前半句正确事实掩盖。退款观察的事实 owner 已在 09a7391 提供受控 statement/fact_ref。本次改变自由文本到发布之间的核验材料与消费合同。

AnswerVerifier 接收最终答案和原始证据快照，返回逐项 SUPPORTED/CONTRADICTED/INSUFFICIENT、答案位置、证据叶路径及需求检查。服务端检查格式、文本覆盖、真实位置，聚合支持性与回答义务；模型没有整段 PASS 字段。证据位置存在及字符覆盖不能证明语义蕴含，也不能证明每个复合句已被正确拆开。

问题中的回答义务与操作限制分属不同 owner。ANSWERED/LIMITATION 必须定位答案，MISSING 阻止通过；EXECUTION_OWNED 只记录执行边界的责任，不能授权或认证动作，不能替代所有回答义务。模型错误分类仍可能漏掉需求；仅含执行限制的输入不获得本答案核验器的通过。

ResponseAssembler 对自由 composer 文本及 worker pass-through 统一核验。明确语义拒绝后最多重新 compose 一次，只用原始 board 和核验反馈；修改后重新核验，修订不能调用业务工具。格式失败/服务异常没有有效核验材料时直接采用既有安全输出。知识答案保留最终引用检查和有效来源复验。

核验结果绑定 question/answer/context/evidence；最终组装结果绑定完整可发布文本，system notice 在核验前加入。Target 发布前校验该绑定。旧的 checked、PASS_THROUGH 和 composer checkpoint 缺绑定时拒绝发布，不能通过旧快照绕过新边界。删除 grounded_final 自报预验证旁路。文本绑定是流程完整性检查，不是对业务事实持续有效的密码学证明。

旧整段实现移入 evaluation/legacy_answer_verifier.py，历史重放显式使用该基线。生产与评测协议同步；当前协议 claim-check-request-v3-need-ownership。

## 实际结果与失败记录

全部新增模型调用使用 deepseek-v4-flash。实际入口是统一 Conversation Agent / Target 路径和隔离数据库；本地 BGE-M3 负责 embedding。

| 开发实验 | 捕获调用数 | 实际结果 |
|---|---:|---|
| production2 首次 | 未知 | 本地 hashlib 名称遮蔽导致异常，捕获尚未落盘；已修，不能记为零成本 |
| production2-retry，两例 | 7 | 退款查询通过；混合例因问题定位规则过严退回 |
| production2-final，两例 | 7 | 退款查询通过；混合例因 ANSWERED 空答案定位退回 |
| schema-mixed1，一例 | 6 | 一次修订后仍退回；暴露回答义务与执行限制混用 |
| ownership-mixed1，一例 | 4 | 完成并获 KNOWLEDGE_SUPPORT_CHECKED；独立复核发现附加关系断言漏检 |
| production-v3，冻结六变体 | 6 | 三条正确表述通过，三条无依据权益断言均为 INSUFFICIENT，无协议失败 |

本次可捕获调用总计 30 次，另有首次异常前未捕获成本。不同阶段已改变协议，不能把整张表视作固定策略成功率。每个实际入口 summary 保存延迟及供应商 usage 字段；没有用未知单价虚构费用。

冻结六变体对照：

| 核验方案 | 无依据误放行 / 3 | 正确答案未通过 / 3 | 协议失败 / 6 |
|---|---:|---:|---:|
| 历史 Flash 整段 | 2 | 0 | 0 |
| 历史 Flash 原子 v2 | 0 | 1 | 1 |
| 本次 Flash 原子 v3 | 0 | 0 | 0 |

这是已消费开发样本上的改善，不能推断总体准确率或统计稳定提升。旧版输出预算 256，新版 4096；同一模型不等于相同 token 成本。六例重放只测给定答案的检查，不等于完整发布链验收。

## 未解决的反例

ownership-mixed1 的来源只说明“审核通过不代表退款已经到账”，答案添加“二者是两个独立的环节”。独立复核判该关系增强应为 INSUFFICIENT，模型却将整段判 SUPPORTED，理由只支持前半句。字符覆盖和合法证据路径都通过，仍然漏掉复合命题。

因此不能将此次 KNOWLEDGE_SUPPORT_CHECKED 计为完全正确答案。其余核心查询答复有依据，耳机排查流程有来源但冗余。下阶段应先对复合命题、因果/独立关系、条件与否定准备分组新变体，保持 Flash，比较漏检/误拒/协议失败及修订后需求覆盖，不做这句话专用过滤。

另外，现有公开 verified/grounded 与 board requirement 完成状态仍有历史语义混用。评测必须分别读取 synthesis_reason 和最终文本；安全回退、工具完成、证据核验通过和用户需求正确回答不能合成一个成功指标。该对外状态迁移仍需单独贯通消费者，不以本次实现提交宣称关闭。

## 验证与复现

相关测试 170 passed，1 skipped（环境相关集成测试）；包含支持/需求状态组合、未知格式、完整文本/数字覆盖、输入快照、一次修订、证据及文本绑定、真实入口旧 checkpoint 拒绝、既有业务事实与工具执行回归。独立只读复核未发现本次实现提交阻断，并确认上述新语义漏检；不是整体质量验收通过。

重放当前六变体：

```bash
MODEL_VERIFIER=deepseek-v4-flash MODEL_VERIFIER_REASONING=none \
.venv/bin/python -m scripts.run_claim_verification_replay \
  --capture artifacts/eval/rag-entailment6-input-2026-09-06/cases.jsonl \
  --output <新的输出目录>
```

实际入口复用 scripts/run_rag_tool_calibration.py 的 mixed-business 参数，输入为 artifacts/eval/rag-atomic-mixed1-input-2026-09-06/cases.json；环境使用隔离测试数据库及现有 provider 配置。相应 manifest 保存实验配置。提交的 captures.sanitized.jsonl.gz 删除 thinking/signature，保留实际请求、工具核验材料和 usage；原始捕获留本地，summary 保存原始 SHA256。没有重写过去失败结果。
