# G0：精排接入、工具误拦截修复与同预算对照交付

2026-09-07。本轮完成已有修复和实验交付，未新增策略或推理 API 调用；召回、真实 Agent 查询和最终答案验收仍按主线继续。生产精排默认保留 listwise，未推广本地 CE。

## 根因与责任边界

1. **缓存身份遗漏实际模型。** 原请求只记录精排 prompt 版本，更换同 prompt 的模型可能复用旧结果。`ResultReranker.version` 现在由 prompt＋配置的 ModelProfile 计算，适配器转交身份；API 与校准入口用实际精排身份构造请求；KnowledgeRetriever 在访问缓存或候选源之前拒绝身份不一致，返回 `INVALID_CONTRACT / RERANKER_IDENTITY_MISMATCH`。本地模型身份包含权重、tokenizer、配置、预处理、运行库、batch与精度。模型目录在进程存续期间应保持不可变，更换模型需重启加载。
2. **业务名词被当成授权指令。** 原规则将 `claim` 与近邻 `approved` 匹配为攻击，政策中的“申请是否获批”因此导致整个工具结果被隔离。修复位于 `PromptInjectionGuard` 规则所属层，区分带动词补语的授权宣称和被动申请状态；不放行整个知识工具，不按文档ID特判。输入与工具输出仍共享规则，附加攻击仍被检查。这是规则边界修复，不是通用语义安全保证。
3. **实验观察点错误。** 原脚本在工具安全检查后读取 `ToolResult.data`，却称为 packed coverage。现在分别捕获原始 handler EvidencePack 与最终模型可见消息；原始结果保留并注明统计限制。每条清空观察缓存，精排未调用时 fallback 为未观察，而非沿用上条结果。

本地 CE 作为可选实现：完整 query＋标题／正文进入 tokenizer，不作固定字符截断；超模型 token 预算、模型错误或非有限分数保留输入顺序并明确 fallback；取消向上传播。正常输出是完整、稳定ID排列。默认 `RAG_RERANKER=listwise`；可选 `local_bge` 参数见 `.env.example`。本次只交付接入，不更改生产权重。

## 冻结对照结果

20条已暴露的手工完整开发query，100篇Doc2Dial文档；真实PostgreSQL知识工具、每路20／融合20、最多5片段／2600tokens。没有 Conversation Agent 查询生成或最终答案生成。

| 方案 | 完整候选 | Top5完整 | 修复后pack完整 | 修复后模型可见完整 | MRR@5 | 项目nDCG@5 |
|---|---:|---:|---:|---:|---:|---:|
| 融合 .25/.75 | 19/20 | 17/20 | 17/20 | 17/20 | .6225 | .6790 |
| 融合 .50/.50 | 19/20 | 17/20 | 17/20 | 17/20 | .6100 | .6690 |
| 本地CE＋.25/.75 | 19/20 | 18/20 | 18/20 | 18/20 | .6875 | .7412 |
| 本地CE＋.50/.50 | 19/20 | 18/20 | 18/20 | 18/20 | .6875 | .7412 |
| Flash listwise＋.25/.75 | 19/20 | 19/20 | 19/20 | 19/20 | .7475 | .7974 |

原始实验 Flash 调用20次；修复后的排名重放0次，本次交付审计0次。原始p50观测为本地CE .25约382ms、Flash约1663ms（包括检索／工具环节）；脚本使用排序后 `n//2`、`int(n*.95)` 索引记录分位，不是标准插值估计。只有20条开发样本，未测并发吞吐。**重放的耗时不包含模型评分，不能拿来比较模型速度。**

原规则在5组共100次工具结果中误隔离9次，涉及3个case；原始可见完整数为融合15、本地16、Flash18，重放后分别17、18、19。这里只恢复证据可见性，没有额外救回召回遗漏。唯一完整query候选遗漏仍为西班牙语问题检索英文资料，归入主线G1继续诊断。

决策：保留 Flash 和 .25/.75 默认；本地CE仅作为可选实现。.50/.50与本轮本地模型均未胜出。19/20不是答案正确率，也不能替代旧60条失败的复测。

## 验证与范围

- 137项相关测试通过，覆盖本地排列／同分稳定性／超预算回退／取消、模型身份拒绝与缓存、原始retriever不变量、规则正常状态与攻击组合、工具安全、校准生命周期和真实测试PG检索。
- 对原始与重放100条记录逐条复核：query、候选ID／正文／来源版本／分数／排名、精排顺序一致；源文偏移与pack及可见原文匹配；完整覆盖及MRR/nDCG重算一致；当前guard对保存可见消息检查通过。
- 模型合法ID、来源位置和文字覆盖检查不证明答案语义正确。当前不宣称最终回答、HTTP鉴权、外部消息投递或订单写操作通过本轮验收。
- 校准脚本支持选择1–6条全链路案例及记录实际检索配置，但本轮未补跑付费全链路。之前6条完成／verified结果作为单独批次保留，后续完整答案评分属于G2/G4。

## 产物与复现

原始结果：`artifacts/eval/rag-production-reranker-pair20-2026-09-07/`。
重放及审计：`artifacts/eval/rag-production-reranker-replay20-2026-09-07/`，当前审计为 `audit-current.json`。
来源：Doc2Dial v1.0.1，CC-BY-3.0，[官方资料](https://doc2dial.github.io/file/doc2dial_v1.0.1.zip)。`input-dataset.json.gz` 保存本次精确语料、案例及原始checksum manifest。

先将快照中 `manifest.json`、`corpus.jsonl`、`cases.jsonl` 解压到独立目录，再做零模型／零数据库审计：

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_rag_reranker_replay.py \
  --original artifacts/eval/rag-production-reranker-pair20-2026-09-07 \
  --replay artifacts/eval/rag-production-reranker-replay20-2026-09-07 \
  --dataset /path/to/extracted-dataset --output /tmp/new-rag-audit.json
```

需要真实PG重放时使用 `scripts/run_rag_production_reranker_pair.py` 的 `--replay-from`，提供 `--dataset`、`--queries artifacts/eval/rag-authored-query20-2026-09-07`、本地 `--embedding`／`--reranker` 和新 `--output`。只接受 `TEST_DATABASE_URL`，创建并清理独立评测数据库；重放与付费开关互斥，默认API上限0。此命令会重新导入和向量化，验证已有记录时优先使用上面的轻量审计。
