# Cloud父内检索的精排与打包验证：收益有限，暂不采用

固定前轮8题两组20候选、官方query、同本地预训练BGE reranker（FP16 batch4、全文输入不截断），候选并集193个query-passage pair只评分一次，共320臂内位置。最大模型输入564token，评分与排序约4.30秒；无API/embedding/微调。此耗时不代表线上端到端延迟。

| 阶段／指标 | Dense基线 | parent_local |
|---|---:|---:|
| 候选Recall20（前轮） | 55.21% | 61.46% |
| CE Recall5 | 52.08% | 54.17% |
| pack/序列化Recall5 | 52.08% | 54.17% |
| pack MRR5 | .5417 | .5208 |
| pack nDCG5 | .5022 | .4960 |

同pack5/2600正文预算，16份模型视图来源原文逐字复验；最大序列化估算3103token（正文预算不包含全部元数据）。未通过Agent上下文/安全guard/生成，不能称真实答案准确率。

逐例：1be662…3救回一条gold，最终Recall0→.5；e1b602…1则2/3→1/3，正确证据仍在候选，却被新增候选经CE打分挤出前5。二者导致宏平均净+2.08pp，但MRR/nDCG下降。web chat与语言支持此前特定漏gold仍未救回，因此目标失败没有关闭。

结论：这8个开发例不足以支持上线。保留原默认；不继续为此候选做付费生成或消耗已用heldout。候选保留与最终排序都影响收益，后续应基于剩余失败的query意图与父内语义匹配继续归因，不能单纯提高每父配额。整体跨数据集验收和领域Agent读回仍在主线队列，微调暂停。

复现`HF_HUB_OFFLINE=1 PYTHONPATH=. .venv/bin/python scripts/run_mtrag_cloud_parent_validation.py`（输出目录需不存在）。通用实验脚本支持显式臂名，并以首臂作比较基准，避免把Dense基线误称权重.25。5项相关检查通过，覆盖候选身份、评分排序、实际打包/序列化指标和原有实验回归。完整产物位于`artifacts/eval/rag-g4-cloud-parent-validation8-2026-09-08/`。
