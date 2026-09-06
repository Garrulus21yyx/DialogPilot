# 混合客服回答：字段解释布局实验

状态：实验完成，不接入生产；整体 RAG 优化仍在进行。

## 问题与假设

真实混合入口的回答会复述“订单快照”“已送达不证明签名”等内部字段说明。事实所有者提供这些说明是为了避免模型把状态强化成签收证明；删除说明可能重新引入错误。因此本实验只检验一个假设：将说明从 FACT.value.field_semantics 移到同一个 claim 的 interpretation_guidance，能否保留语义约束并减少不相关复述。

权威业务事实、原始捕获、知识证据、渲染和支持性验证输入保持不变。只有回答合成模型输入使用深拷贝后的实验布局。没有修改生产 ResponseAssembler、ConversationAgent 或工具契约。

## 输入与费用

使用 rag-reference-mixed6-2026-09-06 中已消费的三条中文模拟开发案例：赠品退货、B20 安装、上下文省略。它们原本由统一 ConversationAgent、真实 PostgreSQL 业务读取及知识工具产生。本次只重放捕获的回答合成输入，不执行规划、业务查询、检索、来源复验或 Publication。

模型配置记录在 manifest.json；本次为 deepseek-v4-pro / none，3 次合成、3 次支持性验证，共 6 次 API 调用。原输入投影可逆性和调用者输入未被修改均经过程序断言。

## 观察

| 案例 | 既有回答字符数 | 实验字符数 | 人工观察 |
|---|---:|---:|---|
| 赠品退货 | 165 | 172 | 仍复述快照，增加退款窗口未知说明 |
| B20 安装 | 159 | 94 | 去掉快照解释，保留型号未知时不可试装及适配型号 |
| 上下文省略 | 242 | 361 | 仍复述快照，增加窗口及质量退货流程 |

三条支持性验证均 PASS，但支持性不是相关性、完整性或最终正确率。字符数只是诊断量，不能把短回答自动算作更好。这里对照的是既有运行输出，没有重复采样估计模型波动；不能把某条变化归因于布局并声称稳定收益。

结论：未显示一致的相关性或简洁性改善，不推广这个布局。保留显式评测参数供复现，避免基于一个较短样例改生产。

## 复现

```bash
.venv/bin/python -m scripts.run_conversation_compose_replay \
  --capture artifacts/eval/rag-reference-mixed6-2026-09-06/mixed-cases.jsonl.gz \
  --output artifacts/eval/rag-compose-guidance3-reproduction \
  --case-ids expanded-gift-return expanded-battery-install expanded-elliptic \
  --verify --separate-field-guidance
```

原始输出采用确定性 gzip 保存在 artifacts/eval/rag-compose-guidance3-2026-09-06/cases.jsonl.gz；summary.json 保存解压后 SHA256、逐例观察指标及结论。生产配置未改。

## 同期 scope 结论校正

来源目录列出当前来源中观察到的范围，不是所有合法用户条件的枚举。已知用户地区为 CN，即使目录中没有 CN 专用来源，仍能正确检索适用范围为 global 的政策。真实 PostgreSQL 回归测试明确覆盖这一点。

所以不能用“条件不在目录中”直接判定 INVALID_CONTRACT。展示名与来源 canonical ID 的映射、适用主体选择仍需来源和业务语义支持；目录准备完成不等于范围解析完成。

## 后续优先级

优先处理实际混合入口仍存在的政策目标漏选及部分任务执行问题。检索组件回放用于低成本筛选；最终仍需统一 ConversationAgent 的业务事实与政策证据混合验收。此实验不能作为端到端质量提升或全链路闭环证据。
