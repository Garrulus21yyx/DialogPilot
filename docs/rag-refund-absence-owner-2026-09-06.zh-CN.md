# 混合客服退款查询：无申请结果的所有者修复

状态：退款查询证据合同已实施并验证；整体 RAG 与回答体验持续优化。

## 观察、机制与所有者

此前真实混合场景的订单和政策都可读取，但 refund_status 在数据库没有申请记录时抛 BusinessObjectNotFoundError。ToolManager 的通用异常路径把它变为 error，TargetToolExecutor 又将其解释成 RETRYABLE_FAILURE。回答因此提示稍后重试，无法使用“当前未记录申请”这一有限事实。

原退款查询仅按 tenant/user/order 筛选退款表，缺行还可能意味着订单不存在或越权。不能在 handler 捕获同名异常并凭空宣告“无申请”。权限范围和存在性必须由业务服务确定。

## 正向合同

CustomerOperationsService.lookup_refund_status 在单条 SQL 的快照中从有权访问的订单出发 LEFT JOIN 同租户、同用户的退款记录，返回 RefundLookup：

- FOUND：得到归属于当前用户的退款申请，保留现有退款字段。
- NO_APPLICATION：订单可访问，但本系统在本次查询中没有该订单的退款申请；附同次观察的 order_version。
- 不存在或无法访问订单：仍抛出相同的 typed BusinessObjectNotFoundError，不生成无申请事实。
- 数据库等执行错误：继续传播为执行失败，不被投影成无申请。

NO_APPLICATION 不证明外部不存在退款、款项未到账或用户没有退款权利。订单版本用于绑定观察对象；它不是退款版本，也不保证此后仍无申请。证据仍受 60 秒有效期及既有复验约束。

get_refund_status 保留“必须有当前申请”的读取接口，复用新观察并在缺申请时抛错。get_refund_status_for_operation 继续要求精确匹配原 operation_key；不会因一般订单可访问就确认不存在的写入。operation 路径不输出 NO_APPLICATION，也不声称重新验证当前订单归属。

## 贯通消费者

共享 refund_status 工具升级为 refund-view-v2，普通调用返回上述结果；FOUND 原有字段及 operation_key 保持可被既有写入对账读取。工具描述、输出字段及 typed outcomes 同步。

AuthorityPolicy 的 refund.current_state 升级为 v2，公共字段为 order_id、lookup_status，分支约束保存在不可变 JSON schema 字符串并纳入策略 fingerprint：

- FOUND 继续要求 refund_id、status、updated_at，非空字段校验未被删除。
- NO_APPLICATION 要求正整数 order_version；拒绝退款状态、金额及 operation 等额外字段。
- 未知 lookup_status 拒绝。

通用 required_fields 保留；可选分支 schema 同时用于 validate_output 与 EvidenceReceiptIssuer。签发后的完整内容 hash 保持后续复验与签发 payload 一致。不存在“工具 success 就绕过事实验证”的新分支。

策略 fingerprint 改变会按既有规则使旧 fingerprint 的证据凭据失效，需重新读取；不限于单个退款凭据。没有改数据库数据结构，没有改退款提交资格或批准逻辑。

## 验证

- 真实 PostgreSQL 业务服务、工具、读续接和写入工作流：60 passed，1 skipped。
- 证据签发及 AuthorityPolicy：22 passed，1 skipped（未配置数据库的测试）。
- 独立限定合同审查：25 passed，2 skipped；先发现旧必需字段不接纳无申请，补齐后复审无新阻断。
- 生成测试覆盖字段子集删除、未知 discriminator、矛盾的退款状态、缺失/非法版本及非法 operation 字段，核查 output validation 与签发拒绝一致，并复验合法凭据。
- 读续接夹具已迁移到当前 reference→显式 type selection 契约；分别覆盖有/无申请、同请求重放及新轮刷新。

## 真实混合入口结果

复用明确要求“查订单、查实际退款进度、解释审核是否表示到账”的一条已消费中文模拟开发用例。仍走 TargetChatApplication、统一 ConversationAgent、真实业务及知识工具、合成、支持性检查和发布边界；不包含 HTTP、持久 worker 或真实业务写入。

| 指标 | 修复前捕获 | 本次 |
|---|---:|---:|
| 三项所需工具调用 | 3/3 | 3/3 |
| 退款读取有效事实 | 0/1 | 1/1 |
| 目标政策模型可见 | 1/1 | 1/1 |
| 目标政策最终引用 | 0/1 | 1/1 |
| knowledge 支持性检查通过 | 0/1 | 1/1 |
| API 调用 | 3 | 4 |

本次包含此前已提交的引用关联 schema 修复，因此最终引用变化不能全部归因于退款语义改动。只运行一个开发案例、没有重复采样，不宣称稳定正确率提升。4 次调用包含规划、精排、合成、支持性验证。

实际回答仍包含 NO_APPLICATION 内部码、无关金额及重复/多余说明。业务事实正确可用不等于用户表达质量合格；此缺陷明确保留，未宣告答案层闭合。订单不存在/无权访问的通用工具异常分类也仍是后续范围，未被本次静默放宽。

产物：artifacts/eval/rag-refund-absence-mixed1-2026-09-06，含原始 gzip、manifest、completion、summary 和 SHA256。与 rag-policy-state-mixed1-2026-09-06 原失败捕获独立保存。
