# WixQA 封存20固定融合对照

使用既有connected manifest的封存20题，ExpertWritten/Simulated各10，与开发所选文章不重合。全量6221篇/11167片段，原官方问题，本地BGE-M3，Dense权重.25对.5（BM25补足1）；每路20/最终20/RRF k10，当前title+content BGE精排，pack5/2600。新增外部API0，本地精排550唯一pair。没有依据封存结果改配置或换题；这20题自本次起已消费。

| 指标 | .25 | .5 |
|---|---:|---:|
| 候选文章Recall | 55% | 75% |
| 打包文章Recall | 55% | 67.5% |
| 打包完整文章集合 | 10/20 | 12/20 |
| 打包MRR | .4958 | .5142 |
| 打包nDCG | .4939 | .5330 |

候选5提高0下降；精排/打包4提高1下降。平均收益保留到pack，满足进入真实入口候选的方向标准，但稳定性未证实：按题配对bootstrap 10000次，Recall差值12.5个百分点的95%百分位区间为[-5,32.5]个百分点；MRR和nDCG区间也跨0。每题来自不同连通组，但样本小，不能把平均值称为已证明的总体提升。

独立评分脚本重算所有阶段的文章Recall/MRR/nDCG，并检查候选/评分/精排/pack ID集合一致，全部通过。完整向量分片SHA/shape/L2归一化及查询文件锁在运行时再验。文章qrel只能证明来源命中，不证明片段覆盖所有证据，更不证明答案正确。这里是离线词法计算，不是PG性能或真实Agent query。

开发两条退步的来源复核也已保存：地理语言题BM25第13独有文章被均衡融合挤出；购物车统计题gold仍在候选，但新增项在精排中挤出它。其他未标注结果可能部分相关，官方标注保持不变，不据此自动判断答案语义。

结论：保留.5为真实入口对照候选，生产默认仍.25；不新增权重网格、不启用动态路由、不恢复微调。下一步使用真实Conversation Agent查询的小批配对，固定Flash精排/生成预算，并独立核对答案与引用；不能用本报告替代该验收。

复现：HF_HUB_OFFLINE=1 PYTHONPATH=. .venv/bin/python scripts/run_wixqa_fixed_comparison.py --split heldout；独立审计 scripts/audit_wixqa_fixed_comparison.py artifacts/eval/wixqa-fixed-heldout20-2026-09-08。结果目录已存在时拒绝覆盖。来源本地保守排除不保证其他机器或删除记录的全局新鲜性。
