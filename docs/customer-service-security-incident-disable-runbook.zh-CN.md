# 客服 Agent 安全事件与 Disable Runbook

状态：Target 原型安全处置手册。适用于文本客服、Knowledge、Memory、框架 Agent、工具及已启用的
媒体输入。控制依据为 `governance/security/target-threat-model-v2.json`；不是生产安全认证。

## 1. 触发条件与分级

以下任一为零容忍事件：跨租户/跨用户数据；未授权或重复写；Prompt/Context 导致权限绕过；审批身份或
参数 replay；checkpoint/trace 暴露 server secret；binary/polyglot 绕过 Knowledge 文本上传合同；删除后数据复活。合法图片/PDF 按独立媒体合同处理。

- SEV-0：已发生跨租户泄露、错误写、凭据泄露或重复副作用。
- SEV-1：可稳定利用但尚无已知业务 effect；或权威 receipt 为 unknown。
- SEV-2：被边界拦截、无泄露/副作用的攻击尝试。

本地演示由开发者协调处置；事实 Owner 分别为 Authentication、Application、Knowledge、Memory、
Agent Runtime、Domain Tool 与 Observability。模型输出、caller 日志和自由文本异常都不是 effect 权威。

## 2. 立即 containment

1. 停止受影响入口的新 admission。全局事件由 ingress 下线 chat/upload；Bundle 问题由开发者显式选择
   已验证版本或提交 forward-fix，不存在自动 rollback/promotion 状态机。
2. 写工具事件在能力注册/工具执行入口禁用受影响动作；仅增加确认不能修复越权或重复提交。保存
   PostgreSQL OperationLedger 和 Receipt，不删除或重放 unknown-effect attempt；无关只读能力可继续。
3. Knowledge/Memory 注入事件停止受影响 corpus/generation，active pointer 只原子回到 compatible previous；
   in-flight 按 pinned refs 完成或 typed conflict，禁止静默读取新 generation。
4. 认证/secret 事件撤销相关 token/credential、轮换 JWT/API/DB secret；轮换前保留最小审计证据。
5. Knowledge 文本上传边界事件关闭受影响的 `/knowledge/add` 或 `/knowledge/upload` ingress，隔离相关 SourceRevision；不直接覆盖
   immutable revision 或删除证据。
6. 媒体事件禁用受影响的 VLM/媒体能力并隔离相关 asset/parse refs；保留无关只读路径。OCR/模型输出
   检查不能证明隐藏像素或 QR 攻击均被阻断，不将合成输出测试作为真实视觉红队结论。

## 3. 调查与权威证据

- 身份：Principal subject/scopes、tenant/user/conversation/request/operation keys。
- 内容：只保存 fingerprint、source/revision/chunk/manifest refs；避免复制原始 PII 到事件聊天或 Trace。
- 工具：call/operation key、approval actor/binding、effect status、authoritative receipt；unknown 保持
  reconciliation。
- Runtime：Bundle/hash、route/backend/generation/corpus/retrieval refs 与请求固定的 execution refs。
- 删除：tombstone/deletion epoch、projection watermark/outbox receipt。

使用 `governance/security/target-threat-model-v2.json` 将事件映射到当前 threat/control owner。
`x-t03-threat-model-v1.json` 及 `security-x-t03-v1` 语料保留为历史冻结证据，不代表当前运行拓扑。Trace/badcase
导出前运行 secret/PII scan；任何 `[REDACTED]` 前的原始值只留在受控事实库。

## 4. 恢复条件

恢复受影响能力前：根因与 authority 已确定，相关回归、对抗和本地 E2E 验证通过，未再出现该安全关键
不变量失败；unknown effect 已由权威 receipt 解析或保持 reconciliation，不允许因此盲目重试。
仅凭据受影响时要求轮换；仅 Bundle/generation 被替换时由开发者显式激活已验证的新版本。
保存对应范围的机器报告，不要求无关能力重新审批或重跑所有数据集。

存在安全问题的 Bundle 不再设为 Active，使用新版本 forward-fix。被污染 SourceRevision 不改写，发布新 revision/
manifest。恢复写工具时必须保留相同 operation/idempotency 合同。

## 5. 事后闭环

在事件记录中链接代码 Owner、自动化测试、corpus manifest、运行报告、commit/version、残余风险和适用范围。
更新 threat model 需要新 model/version/checksum；禁止就地修改已作为 Gate prerequisite 的证据。生产渗透测试、
数据库角色/RLS、加密卷与真实 credential rotation 属于部署证据，不能用本地组件测试冒充。
