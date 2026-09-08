# 知识证据的安全与领域 Agent 归档边界

冻结上一轮32题三臂的96份完整序列化视图，API0。调用生产UntrustedContentGuard及ToolResultPersistence，使用InMemoryStore验证归档内容，不是实际PG归档可用性或整个Agent调用。未执行完整ToolManager终态、ContextCompaction、模型读取或答案生成。

| 边界 | 安全隔离 | 保持全文inline | 转摘要/读取引用 | 原文可恢复 |
|---|---:|---:|---:|---:|
| 默认工具预算2840 tokens | 0/96 | 69/96 | 27/96 | 96/96 |
| 强制边界256 tokens | 0/96 | 0/96 | 96/96 | 96/96 |

2840来自代码默认(16000-1200-600)/5，实际部署可被模型配置和环境变量改变，未声称线上正在使用此预算。中间件使用SDK count_tokens_approximately，和packer的TokenEstimator不同，不将两者估算数字等同为provider真实token数。

归档后可见内容为400字符preview及read_tool_result引用。原文仍完整，但这27份不再全文inline。是否成功读取所需页、是否耗尽步骤预算，需要真实领域Agent实验；不能按“归档可恢复”继续声称模型完整证据Recall不变，也不能把归档本身计为数据丢失。

路径必须区分：DIRECT知识任务将知识结果转换为Fact，再由ResponseAssembler提取model_evidence送合成；它不必经过领域Agent ToolResultPersistence。领域Agent调用知识工具才经过本次归档边界。因此27/96不是DIRECT回答丢失率。后续应分别捕获DIRECT合成输入、领域Agent工具后模型输入/读取调用。

首次离线执行使用权重字符串0.25作为模拟work_item_id，被SDK namespace禁止句点规则拒绝；生产归档正确返回archive_failed，没有模型调用。修复模拟ID为weight-0-25后重放，未放宽归档合同。失败属于fixture身份错误，不计安全或语义失败。

1项测试实际重放192视图，覆盖两个预算边界、原文恢复和inline/ref区分。脚本 `scripts/replay_mtrag_tool_boundary.py`；产物 `artifacts/eval/rag-g4-mtrag-tool-boundary-2026-09-08/`。artifact仅用于归档身份，不冒充完整检索事实转换；生命周期删除/权限隔离仍依赖已有owner测试，本轮没有重新验收这些能力。

下一动作：优先真实DIRECT合成输入小样本配对；领域归档挑选发生offload的固定样本验证读取行为。保持当前生产策略，不能仅增大上限掩盖消费路径问题。
