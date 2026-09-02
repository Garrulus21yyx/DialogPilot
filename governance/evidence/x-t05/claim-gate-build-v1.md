# X-T05 capability claim gate build evidence v1

日期：2026-09-02。状态：`IMPLEMENTED / NO READY CLAIMS`。

## 正向合同

对外能力声明由 `governance/claims/claim-registry-v1.json` 统一登记。每条 claim 使用闭合字段集合：一个有边界的 statement、maturity、scope、代码 Owner/paths、自动化测试、带 SHA-256 的 data manifest 与 run report、当前历史中的完整 commit、不可变 version 和至少一个 limitation。

`scripts/validate_capability_claims.py` 验证路径不能逃出仓库、Owner/test 文件存在、manifest/report 内容 hash 精确、commit 是当前 HEAD 的祖先、claim identity 唯一且阶段代数闭合。缺证据时 typed fail，不创建空报告或把 maturity 自动提升。模板与人工 review checklist 明确禁止把组件测试写成 E2E 成功率、provisional 写成 Gold、checkpoint 写成 exactly-once、adapter 写成完整多模态 RAG、技术选型写成质量提升，或复制无本项目证据的百分比。

## 首批 registry 与随机内容抽检

首批登记四条 bounded `IMPLEMENTED` claim：X-T01 本地 schema governance、X-T02 build-time replica fencing、X-T03 deterministic threat controls、X-T04 route cost budgets。没有 `VERIFIED` 或 `READY` claim。

固定 seed `20260902` 随机抽取三条：

- `x-t02-replica-fencing-build`：代码/tests/contract/report/commit 一致；statement 只说 repository tests，明确没有真实 primary promote、Redis cluster partition 或生产多副本 Agent ledger。
- `x-t03-deterministic-security-controls`：13-case manifest、threat report 和 receiving-boundary Owner 一致；只声明冻结 threat model 内的 deterministic controls，明确无独立 pen test，VLM 路径未启用。
- `x-t04-route-cost-budgets`：route/offline budgets、typed exhaustion tests、gate/report/commit 一致；明确 flag-off 且无 invoice sample，不陈述成本下降百分比。

机器 audit 固化在 `governance/evidence/x-t05/claim-audit-v1.json`，结果 PASS。该内容复核由实现上下文完成，不替代未来 Product/Support Ops 对真实发布文案的独立批准。

## 产物与验证

| 产物 | SHA-256 |
|---|---|
| Claim registry | `74449c312cb1e04d831c3464f8bf438f5c9122749ca9b3428227696d5894e022` |
| Evidence template | `c920efde3bb22e26308672668ac6c90ff814a513a1891d4e2f11daaca68a890f` |
| Frozen audit | `f511f2b870df96d69e0b90914765b6794491b3323298242b32ac0a42d6298931` |
| Review checklist | `12de28d2c07df8a91ff0a2e4aac6af3ea8c55daf089ef0287e62f9b11316c0f2` |

聚焦 claim/schema/security/cost suite：`31 passed in 1.24s`；全仓：`824 passed in 52.87s`；ruff 与 `git diff --check` 通过。

后续新增/修改对外声明必须先迁移 registry 和证据 hash。机器链接完整性不判断业务文案是否适合发布；任何 `READY` claim 还必须链接不可变 GateDecision、Owner/approver 签署与生产适用范围。
