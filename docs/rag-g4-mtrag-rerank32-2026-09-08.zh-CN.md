# G4：固定候选的本地精排对照

2026-09-08，起点 `97ef229`。沿用32个开发conversation的官方rewrite，固定.25/.5/.75三组Top20候选；本地预训练BGE-reranker-v2-m3、FP16/batch4、完整query与正文、不截断、不微调。API0。

## 结果

| Dense权重 | 候选R@20 | 精排前R@5 | 精排后R@5 | 精排后MRR@5 | 精排后nDCG@5 | 精排Top5至少一条相关 |
|---|---:|---:|---:|---:|---:|---:|
| 当前.25 | 50.26% | 31.77% | 42.71% | .4870 | .4118 | 18/32 |
| .5 | **63.02%** | 38.28% | 47.66% | **.5255** | .4485 | **22/32** |
| .75 | 60.00% | **45.57%** | **50.00%** | **.5255** | **.4583** | **22/32** |

相同精排模型下，.75相对当前.25的R@5净增 **7.29个百分点，10题提高、3题降低**；.5净增4.95个百分点，9题提高、2题降低。MRR方面两者均提高6题，分别降低5题和4题；nDCG仍有误伤（.75降低8题、.5降低6题）。完整逐例结果保留，不能只报告均值。

.5的候选覆盖最好，.75的精排Top5相关性指标更好；这支持继续比较更靠后的证据可见阶段，而不支持现在直接切生产权重。两者最终至少命中一个相关passage的题数相同。32题用于开发，不是新鲜验收。

本轮R@5按标注passage比例宏平均，**不是答案正确率、不是所有必要证据完整率**。nDCG@5的ideal按min(5,正相关数)计算，不能与之前nDCG@20直接混用。官方rewrite仍可能缺上下文，四条双路Top20 miss不可能由本轮精排救回。

## 成本与可见性

三个方案共1920个候选位置，按query/passage去重后仅1104对需要模型评分，节省816次重复pair评分（42.5%）。没有复用异query的旧Doc2Dial缓存。评分和排序耗时约10.9秒（包含本次模型加载，不是线上单请求延迟），API0、未重算Dense。

实际最大padded输入631 tokens，均低于8192；本轮候选没有包含全语料最长5627-token片段。这不证明线上全部精排输入都如此短。所有候选正文从锁定完整zip按原ID读取，任意缺失或重复ID失败。

3项测试通过：Top5指标分母、候选保持与稳定同分规则、1104分数和三组实际排序复算。另运行一次不加载模型的缓存重放，全部分数与排名解压内容完全一致，新增模型pair0。缓存身份绑定模型文件、候选产物、原始语料manifest和完整query/正文hash。

## 复现

```bash
PYTHONPATH=. HF_HUB_OFFLINE=1 .venv/bin/python scripts/run_mtrag_reranker_pair.py \
  --hybrid artifacts/eval/rag-g4-mtrag-hybrid32-2026-09-08 \
  --manifest artifacts/eval/rag-g4-mtrag-adapter-2026-09-07/manifest.json \
  --corpora /tmp/dialogpilot-mtrag-corpora-20260907 \
  --model /home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3 \
  --replay artifacts/eval/rag-g4-mtrag-rerank32-2026-09-08 \
  --output /tmp/mtrag-ce-new-replay
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_mtrag_reranker_pair.py
```

去掉replay会真实重新评分；一般核对结果应优先使用上述缓存路径。

产物：[汇总](../artifacts/eval/rag-g4-mtrag-rerank32-2026-09-08/report.json)、[逐例](../artifacts/eval/rag-g4-mtrag-rerank32-2026-09-08/cases.json.gz)、[缓存身份](../artifacts/eval/rag-g4-mtrag-rerank32-2026-09-08/identity.json)、[零模型重放检查](../artifacts/eval/rag-g4-mtrag-rerank32-2026-09-08/replay-audit.json)。

下一项沿用三组冻结精排结果，核对生产证据打包/模型可见序列化的预算与来源对应，不再调这批query或重训模型；先尽量无API重放，再确定少量真实工具/答案验证。尚未完成PG、实际ToolMessage、生成或封存验收，生产默认保持不变。
