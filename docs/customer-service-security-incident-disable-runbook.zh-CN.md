# 客服 Agent 安全事件与 Disable Runbook

状态：X-T03 build runbook。适用于当前文本客服、Knowledge、Memory、Agent 与工具面；不授权尚未实现的
Multimodal/VLM 输入。

## 1. 触发条件与分级

以下任一为零容忍事件：跨租户/跨用户数据；未授权或重复写；Prompt/Context 导致权限绕过；审批身份或
参数 replay；checkpoint/trace 暴露 server secret；binary/polyglot 进入 Knowledge；删除后数据复活。

- SEV-0：已发生跨租户泄露、错误写、凭据泄露或重复副作用。
- SEV-1：可稳定利用但尚无已知业务 effect；或权威 receipt 为 unknown。
- SEV-2：被边界拦截、无泄露/副作用的攻击尝试。

Incident Commander 是唯一处置协调者；事实 Owner 分别为 Authentication、Application、Knowledge、Memory、
Agent Runtime、Domain Tool 与 Observability。模型输出、caller 日志和自由文本异常都不是 effect 权威。

## 2. 立即 containment

1. 停止受影响入口的新 admission。全局事件由 ingress 下线 chat/upload；route candidate 事件调用
   RolloutManager rollback。previous 不兼容时保持 `BLOCKED_FORWARD_FIX`，不能强退。
2. 写工具事件设置 `TOOL_APPROVAL_MODE=require_all`，在工具注册/ingress 层禁用具体 write tool；保存
   ToolExecutionLedger，不删除或重放 unknown-effect attempt。
3. Knowledge/Memory 注入事件停止受影响 corpus/generation，active pointer 只原子回到 compatible previous；
   in-flight 按 pinned refs 完成或 typed conflict，禁止静默读取新 generation。
4. 认证/secret 事件撤销相关 token/credential、轮换 JWT/API/DB secret；轮换前保留最小审计证据。
5. 上传事件关闭 `/knowledge/add` 与 `/knowledge/upload` ingress，隔离相关 SourceRevision；不直接覆盖
   immutable revision 或删除证据。
6. Multimodal/VLM 当前为未实现面：保持路由不存在与未知格式拒绝。任何临时接入都不得绕过 M5 Gate。

## 3. 调查与权威证据

- 身份：Principal subject/scopes、tenant/user/conversation/request/operation keys。
- 内容：只保存 fingerprint、source/revision/chunk/manifest refs；避免复制原始 PII 到事件聊天或 Trace。
- 工具：call/operation key、approval actor/binding、effect status、authoritative receipt；unknown 保持
  reconciliation。
- Rollout：Bundle/hash、route/backend/generation/corpus/retrieval refs、pointer event 与 assignment stage。
- 删除：tombstone/deletion epoch、projection watermark/outbox receipt。

使用 `governance/security/x-t03-threat-model-v1.json` 将事件映射到 threat/control owner。Trace/badcase
导出前运行 secret/PII scan；任何 `[REDACTED]` 前的原始值只留在受控事实库。

## 4. 恢复条件

必须同时满足：根因与受影响 authority 已确定；credential 已轮换；跨租户/重复写/删除复活为零；unknown
effect 已由权威 receipt 解析或继续 reconciliation；adversarial corpus 与 held-out 测试通过；新 Bundle/
generation 重新走 shadow/canary gate；Security 与对应 Domain Owner 审阅证据。

被 rollback 的 Bundle 不复活，使用新版本 forward-fix。被污染 SourceRevision 不改写，发布新 revision/
manifest。恢复写工具时必须保留相同 operation/idempotency 合同。

## 5. 事后闭环

在事件记录中链接代码 Owner、自动化测试、corpus manifest、运行报告、commit/version、残余风险和适用范围。
更新 threat model 需要新 model/version/checksum；禁止就地修改已作为 Gate prerequisite 的证据。生产渗透测试、
数据库角色/RLS、加密卷与真实 credential rotation 属于部署证据，不能用本地组件测试冒充。
