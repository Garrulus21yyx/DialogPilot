# M5-T02A Agent-owned MediaRequirementDecision build evidence

日期：2026-09-02。状态：`IMPLEMENTED / BEHAVIOR FLAG-OFF`。

## 正向合同

Agent 是业务 media requirement 的唯一 producer。canonical `MediaRequirementDecision` 固定 mode、bindings、decision reason、task schema hash、policy version 与 producer；decision/binding identity 都由规范化字段 SHA-256 确定。Application 与 Multimodal runtime 不能因为存在附件、cache/parser capability 而创建、升降或改写 requirement。

- `NO_MEDIA_REQUIRED` 必须 bindings 为空且只有版本化 `MEDIA_IRRELEVANT|TEXT_EVIDENCE_SUFFICIENT|NO_RELEVANT_ASSET` reason；其 `requires_aggregation=false`，因此 downstream job/signal/OCR/VLM 计数合同为零。
- `MEDIA_TARGETS` 必须含唯一 binding。每个 binding 显式绑定 requirement→asset→optional region、REQUIRED/OPTIONAL、L1/L2 stage、reason 和 omission policy，不允许平铺 asset/region 后做笛卡尔积。
- Multimodal consumer validator 只返回 `VALID` 或 typed `INVALID_MEDIA_REQUIREMENT`，检查 exact schema/canonical ID、Agent producer、task/policy version、asset ACL、region、FactRequirement requiredness、risk minimum stage 与 Authority-approved omission policy。
- required→optional、L2→L1、缺失/越权 omission policy、未知 requirement/schema/field、伪造 producer均 fail closed；validator 不修改原 decision。

冻结 artifact `governance/media/m5-t02a-media-requirement-v1.json`，SHA-256
`cca389b958c095f32bafc562e0a7cb5edb8bb20ae52ac870edb4d8ae9f51cc25`。

聚焦 contract/property tests：`11 passed`；全仓：`854 passed in 51.17s`；ruff/diff checks passed。

本卡只发布 schema/producer port/validator，不启用附件 admission、storage、OCR、VLM、aggregation 或在线行为。M5-T02/M5-T03/M5-T07 必须消费本 artifact；Multimodal bounded canary/GA 仍受 M4/M5/M6 Gates 与安全 review 阻断。
