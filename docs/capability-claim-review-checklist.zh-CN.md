# 能力声明与简历事实 Review Checklist

适用节点：`X-T05`。任何 README、架构文档、报告、演示、发布说明或简历中的完成态声明，都必须先登记到 `governance/claims/claim-registry-v1.json`。模板为 `governance/claims/evidence-link-template-v1.json`。

## 提交者检查

- 声明只描述一个有边界的能力；明确输入、环境、路径、typed outcome 和不适用范围。
- `maturity` 使用 `TARGET / EXPERIMENT / IMPLEMENTED / VERIFIED / READY` 之一，且不把较低阶段写成较高阶段。
- 指向真正拥有语义的代码 Owner，不以 API adapter、报告或测试替代 Owner。
- 至少链接一个自动化测试、一个冻结 data/gate manifest、一个运行报告、完整 40 位 commit SHA 和不可变版本。
- manifest 与 report 都保存 SHA-256；修改证据必须发布新版本并迁移 claim，不能原地改 hash 来维持旧声明。
- `limitations` 明确生产/本地、synthetic/human-reviewed、flag-off/canary/GA、样本量、环境和未验证故障面。

## Reviewer 拒绝条件

- 组件测试被写成 E2E 成功率，synthetic/provisional 数据被写成 Gold 或 human-reviewed。
- 没有 provider invoice 却声称成本下降；没有 production SLO/RTO 证据却声称生产性能或高可用。
- 使用 LangGraph/checkpoint/幂等 key 就声称 exactly-once；没有业务权威 receipt 却声称写 effect 确定发生/未发生。
- 仅完成 OCR/VLM adapter 就声称多模态 RAG 已完成；仅作技术选型就声称质量提升。
- 复制外部项目百分比，或报告中的分母、dataset/version、baseline、置信方法无法回溯。
- commit 不在当前历史、证据文件缺失/hash 漂移、scope/limitations 为空，或同一 claim 有互相冲突的 Owner。

## 合并与定期抽检

1. 执行 `PYTHONPATH=. .venv/bin/python scripts/validate_capability_claims.py --sample-size 3 --seed 20260902`。
2. Reviewer 打开抽中的每个 claim，逐项访问 Owner code、tests、manifest、report 和 commit；机器 PASS 只证明链接完整，不替代内容判断。
3. 若证据不足，删除对外声明，或把 maturity 降为 `TARGET/EXPERIMENT` 并写明缺口；不得创建空报告或自签名来“补齐”。
4. Release Gate、实现状态或限制变化后，重新审查受影响 claim。`READY` 还必须链接对应不可变 GateDecision 与 owner/approver 签署。
