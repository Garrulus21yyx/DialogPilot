# Langfuse 领域 Agent 实测记录

日期：2026-09-07。集成代码：`4536728`。SDK：Langfuse 4.15.1。

## 接入与边界

- 安装并使用官方 `langfuse/skills` 的 `skills/langfuse`，固定上游版本 `fc574260e5530b217d94e06465a4da0c7d846888`；本机安装目录为 `/home/yang/.codex/skills/langfuse-official`，未覆盖已有技能目录。
- 按官方 instrumentation skill 执行“发送实际调用 → CLI 回读 observations → 检查层级、正文、模型、usage 和脱敏”的验收。
- 使用官方 `langfuse.langchain.CallbackHandler` 接入现有 TargetFrameworkAgent；不再维护自写模型/工具采集回调。现有业务 TraceSink 保留业务事件职责。
- 用户授权的凭据仅写入 Git 忽略的本机 `.env`，权限 `600`。启用 tracing，环境标为 `development`。凭据不进入此报告或提交。

## 云端证据

两次均为真实 TargetFrameworkAgent、实际模型 `deepseek-v4-flash` 与合成只读商品目录工具；不是完整 HTTP 用户旅程，也不是 τ³ 评分重跑。

| 运行 | 云端回读 | Agent | Generation | Tool | Framework chain |
|---|---|---:|---:|---:|---:|
| [第一次](https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/traces/f99305a2c397fcb687dbd3f6edbcb7bd) | HTTP 200，23 observations | 1 | 3 | 2 | 17 |
| [development 脱敏验证](https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/traces/6fe115beb5bd347eee7540993be4f1d3) | HTTP 200，15 observations | 1 | 2 | 1 | 11 |

第二次回读确认：

- 所有 observation 均属于 `development`；不存在指向缺失 observation 的 parent ID。
- 根 Agent 有独立审计 session；metadata 包含 work item 与 invocation 绑定。
- 两次 generation 均有模型名、input、output、usage；total 分别为 864 和 1,028 tokens，后者包含 768 cache-read tokens。
- `catalog_search` 的请求和结果均可见。工具属于合成 fixture，不访问真实客户数据。
- 合成邮箱和 token 原文未出现在云端回读记录，Langfuse Secret Key 也不存在。
- 官方 SDK 的 `metadata.scope.attributes.public_key` 包含项目 Public Key；不将其误报为 Secret Key 泄漏，也不声称所有配置键均被移除。

## 验证限制

- 此次验证领域调用的远端采集；API/τ³ 入口已接同一集成，但本次没有重新执行完整 API 或 τ³ 任务。
- 未验证自定义模型自动计价，不报告成本收益。
- 脱敏测试覆盖已有规则，不代表任意自然语言隐私识别完备。测试只使用合成内容。
- 尚待处理的 DomainOutcome、追问恢复、失败反馈及无进展问题仍按原计划推进；可观测性通过不等于业务正确率提高。

参考：[Langfuse 官方最佳实践](https://langfuse.com/docs/observability/best-practices)、[官方技能](https://github.com/langfuse/skills/tree/fc574260e5530b217d94e06465a4da0c7d846888/skills/langfuse)。
