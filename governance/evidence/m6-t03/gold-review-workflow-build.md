# M6-T03 Gold / fresh-heldout workflow build evidence

日期：2026-09-02。状态：`WORKFLOW IMPLEMENTED / REAL GOLD DATA BLOCKED`。

本节点只实现 review workflow Owner 合同，没有读取或修改工作区中任何未跟踪的 draft dataset，也没有把 synthetic/provisional 数据提升为 Gold。

## 正向合同

- `ReviewCandidate` 只接受已获 Privacy review identity 的脱敏输入、opaque source refs，以及 user/order/product/time/semantic 五维 SHA-256 group identity；直接 email/phone 在标注前拒绝。
- Gold promotion 要求两个不同 reviewer 的 blind annotation。完全一致可 `AGREED`；不一致必须由第三个独立 arbitrator 保存 labels/time/rationale，不能自动多数投票或由模型晋级。
- split 基于五维 group fingerprint 与冻结 salt 确定；同组不能跨 dev/heldout。参与任何修复后必须绑定完整 fix commit 并单调转成 `consumed_regression`，不再计入 fresh heldout。
- manifest 保存 privacy review、reviewer/arbitrator、decision status、labels hash、slice、split、consumed/fix refs；Cohen's kappa 只接受成对 annotation。
- release report 固定 Wilson 95% CI，并按 invalid/OOS/security/compound/multi-turn/Handoff/multimodal/standard slice 报告，不以总体平均掩盖错误 slice。

冻结 policy `governance/evaluation/m6-t03-gold-review-policy-v1.json`，SHA-256
`ea9563aeb1d2abb98f481589f8c64fe98095278fc01b7e944a4377b47d6ca87d`。

## 验证与阻断

聚焦 workflow property tests：`8 passed`；全仓 `843 passed in 51.37s`；ruff/diff checks passed。

真实 M6-T03 仍阻塞于：脱敏生产工单抽样、Product/Support Ops 两名标注者、Privacy review、争议仲裁、IAA 实测、独立 sealed fresh heldout 和运行报告。缺少这些外部事实时不得生成 Gold manifest、成功率、置信区间或 Gate evidence。因此任务状态不是完整 IMPLEMENTED/VERIFIED。
