# 电商适用条件与知识来源校准

这轮包含12条中文模拟开发问题、12份来源版本（11个source_id），验证地区、渠道、型号、同来源历史版本、撤回和跨段落证据。综合手册仅881字符，不能代表超长文档。政策数值是实验设定，不是真实商业规则。

## 适用条件消融：零API调用

通过真实知识工具构造请求，然后直接运行相同PostgreSQL候选来源；一组保留已知条件，另一组省略地区／渠道／型号并将查询时间改为冻结的2026-06-01。租户、授权、generation和候选预算不变，撤回校验一直生效。

| 候选层指标 | 省略条件 | 保留条件 |
|---|---:|---:|
| 完整证据Top5 | 11/12 | 12/12 |
| 候选池含指定不适用来源的请求 | 7/12 | 0/12 |
| 外部推理调用 | 0 | 0 |

历史运费问题在省略as_of后只找到当前版本，丢掉购买时版本。七个指定不适用来源来自地区／渠道／型号的对照条款。这是构造数据上的字段消融，不是生产版本改动前后的提升；没有测省略条件后的最终回答。

探针只完成真实handler的请求构造与候选检索，主动中断后续流程，不伪装成一次完整工具成功。当前脚本为此模式设置零模型调用预算，且无需有效API密钥。

## 资料Metadata修正：消除一条来源冲突

v1的欧洲政策写十四日，但综合手册的七日规则错误标成global。两条都合法进入检索，生成器发现冲突并拒绝给出确定期限。这是安全行为，不是模型应该猜出哪条优先。

在来源定义处把综合手册的region改为CN，保留v1原始定义和捕获，重新执行同样12个问题。修正后欧洲问题依据十四日条款给出答复。

| 真实知识工具＋生成 | v1资料 | v2资料 |
|---|---:|---:|
| ToolMessage必要证据完整 | 12/12 | 12/12 |
| 冲突／弃答 | 1/12 | 0/12 |
| 指定不适用来源返回 | 0 | 0 |
| API调用 | 24 | 24 |

这是资料适用范围修正，使确定答复从11条增加到12条，不能称为检索算法准确率提升。独立模型复核核对v2原文切片、checksum、gold spans及答案，未发现与引用资料不符的答案；不是人工标注或封存验收分数。v2使用Flash精排12次、Pro生成12次，输入14,797、输出2,734、另计cache-read21,504 tokens。

## 复现

需要本地BGE-M3和测试实例TEST_DATABASE_URL。脚本新建隔离数据库并最终删除，不从生产.env读取数据库地址。

```bash
# 候选层：零外部推理
.venv/bin/python scripts/run_rag_tool_calibration.py \
  --scenario applicability --candidate-scope-probe \
  --model /home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181 \
  --output /tmp/rag-scope-probe-new

# 真实工具＋生成：去掉 --candidate-scope-probe，使用现有API配置和新output

# 直接重算已保存结果，无需API
python -m evaluation.rag_tool_calibration_report artifacts/eval/rag-applicability-probe-2026-09-06 --probe
python -m evaluation.rag_tool_calibration_report artifacts/eval/rag-applicability-dev-2026-09-06-v2
```

旧探针运行在v1来源上，当前复现默认使用修正后的v2；其manifest和v1定义均保留，不能把两份source corpus当作相同实验输入。候选池指标统计指定对照来源，并不穷举所有可能的适用性问题。

业务与政策仍允许耦合。本批未运行ConversationAgent、订单工具或完整HTTP链路；业务混合验收、超长文档和检索策略同预算对照仍待完成。
