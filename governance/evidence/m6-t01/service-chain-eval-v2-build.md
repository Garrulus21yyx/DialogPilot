# M6-T01 Dataset v2 / Rubric v2 build evidence

日期：2026-09-02。状态：`IMPLEMENTED / CONTRACT FIXTURES PROVISIONAL_NOT_GOLD`。

## Owner 与正向合同

`evaluation.service_chain` 新增独立 v2 合同，不修改或冒充现有 v1 intent/routing/retrieval/stateful 数据身份。v2 case 固定 11 个服务链 observation layer：perception、route_mode、context_memory、retrieval、tool_authority、tool_effect、generation_claims、publication、handoff、delivery_feedback、service_outcome；未知 layer/status/rubric 字段 fail closed。

每条 case 必须保存 group-safe split、source/license/review、authoritative backend state 和分层 decision Gold。decision Gold 分开支持 intent source fusion、Domain Owner/supporting agents、instance applicability、Knowledge/Memory RRF 与 ActiveCase selection，避免只标最终 RouteMode。

确定性 Rubric 独立检查：

- required/forbidden Tool、调用顺序、参数 subset、receipt fields；
- claim→evidence refs、state transition、Handoff required fields；
- Task→Owner、dependencies、parallel waves、dependency receipt、budget outcome；
- duplicate publication 与 per-operation effect 次数；
- cross-tenant/stale evidence/unnecessary VLM 等 zero-tolerance flags。

可选 RAGAS-compatible scorer 只通过固定 scorer ID/version 提供 faithfulness、answer relevancy、context precision/recall 等语义投影；它不能覆盖任一 deterministic failure。Knowledge/ServiceEpisode/Media retrieval slice 与 Recall@K/MRR/nDCG/filtered exact-vs-ANN 在 manifest 中分 corpus 冻结，不以总分掩盖失败。

## 冻结 contract fixture

`data/eval/service-chain-v2-contract` 包含 Knowledge-only、authoritative Tool read、unsupported-authority Handoff 三条 schema/runner contract fixture，其中 heldout 已可见且明确 consumed/provisional；它们用于证明合同可执行，不是 human Gold、fresh heldout 或质量样本。

| 产物 | SHA-256 |
|---|---|
| manifest | `2eee0b898f0fb9104b359dc77a71aeb82009852af1452454aa571c453cfdf39e` |
| cases.jsonl | `6b51366980dfab6fe8392ade7663adafe8fcec70a35f00fdca509c8100763b8c` |

聚焦 v2 property tests：`7 passed`；全仓：`835 passed in 51.39s`；ruff 与 `git diff --check` 通过。测试证明正确 Tool/receipt/parameter 与 single publisher 可通过，满分语义结果不能覆盖缺 receipt/重复 publication，claim evidence/scorer version、TaskGraph owner/wave/budget、authoritative backend state 与 unknown field 均 fail closed。

## 后续边界

M6-T03 仍负责 Product/Support Ops/Privacy 人审 Gold 与独立 fresh heldout；M6-T02 负责完整多轮 simulator，M6-T04 负责持久 observation。当前三条 fixture 不支持成功率、生产质量、RAGAS 提升或 release READY 声明。
