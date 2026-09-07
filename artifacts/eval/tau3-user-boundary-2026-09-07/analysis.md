# 模拟用户空消息：调用边界归因与修复

## 结论与边界

已复现并修复一个共享配置根因，不是业务类别补丁：τ³ 启用 LiteLLM
`drop_params=True`，当前 Anthropic-compatible 模型名不在 SDK thinking 支持列表中。
请求配置中的 `thinking={type:disabled}` 被删除；供应商实际收到的请求没有关闭思考。
512 输出预算可能全被思考消耗，导致空正文；也可能剩少量正文但截断。

v15 原失败的 raw response 未保存，无法声称逐字还原了当时返回。
本次用原任务场景、官方默认 persona、可见对话前缀重建一次用户请求，明确不是原始请求精确回放。
修复前后均保留证据，不选择最佳结果覆盖失败，也未调用订单或换货工具。

## 因果对照

| 探测 | 请求与结果 |
|---|---|
| task-0-probe | 旧策略；原始响应仅 thinking，stop_reason=max_tokens；512 输出，正文为空 |
| task-0-captured-replay | 最初回放遗漏 SDK 全局 drop_params，显式 UnsupportedParamsError；此失败保留，回放合同已补 SDK 参数设置 |
| task-0-wire-probe | 旧策略；provider_request 无 thinking；512 输出，含思考及截断正文 |
| task-0-policy-probe | 相同历史前缀和 512 预算，明确传递 thinking 控制；wire 含 thinking:disabled；177 正文 tokens，0 reasoning，正常停止 |
| task-1-policy-probe | 第二个失败前缀；17 正文 tokens，0 reasoning，正常停止 |
| task-1-trace-audit | 验证 Langfuse 属性装配；17 正文 tokens，0 reasoning，正常停止 |

这不是统计成功率，也不证明模拟用户未来永不失败。官方两任务仍保持 ERROR/null。
seed=300 是调用侧设置，旧 SDK 可将它过滤，不能宣传为供应商级确定性保证。

## 最小共同修复

- `simulator_parameters()` 使用 SDK 支持的 `allowed_openai_params=['thinking']` 保留必要控制。
  未扩大输出预算，未自动重试空消息，未更改任务场景、STOP、用户决策或评分。
- 官方 CustomLogger 钩子保存请求配置、最终 provider_request、原始 provider_response、
  SDK response、finish_reason、usage、调用身份和异常类别。
- τ³ 校验空消息之前更新的 user_state 在错误路径保存；报告包含异常调用栈与执行方向。
- 文本、工具调用、空正文、截断及 SDK 异常分别记录；本地写失败不能标 capture_complete。
- LiteLLM 官方 Langfuse OTEL 集成承担远端追踪；修正区域配置和专用属性路由。
  关闭时刷新实际 credential-scoped provider，不把 flush 当成服务端回执。
- 标准 SDK 在此版本没有公开 close；仅在该适配边界处理其 provider cache，并以真实 SDK 测试约束。
- 凭据、推理块及嵌套 JSON 中的推理内容脱敏；保留块类型、reasoning_present 和数值用量。
  被脱敏的请求不能声称精确回放，回放入口明确拒绝。

## 验证

- 集中回归：50 passed（诊断、环境工具绑定、自然回复合同）。
- 独立只读复审：17 passed；包括三个未注册模型名的本地 HTTP 请求体测试、
  真正 LiteLLM callback 生命周期、官方 OTEL 内存导出、实际 provider flush/shutdown。
- 复审发现并修复：嵌套原始 JSON 推理泄漏、落盘失败假成功、刷新了错误 OTEL provider。
- Langfuse CLI 回查确认 `simulate-user` GENERATION、session、输入输出、用量均存在。
  模型名在 SDK 导出的 `llm.model_name`/`llm.response.model` 属性中可查；本次未验证云端价格映射。
  [真实模拟用户 trace](https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/traces/2ecc9faa769f66554283626bfbb181c5)
- 依赖实测：litellm 1.82.6、langfuse 4.15.1、anthropic 1.2.0、opentelemetry-sdk 1.44.0。

## 单调用回放

在包含项目和 τ³ 可选依赖的环境中：

```bash
python scripts/replay_tau3_user_call.py <simulator-calls/call.json> --output <new-directory>
```

仅调用用户模型，不启动 Target、业务数据库或工具执行器。输出目录必须是新目录。
历史缺失捕获时可使用 `--trajectory ... --tau-source ... --task-id ...`，产物明确标注重建。
`--current-simulator-policy` 明确覆盖旧参数进行对照，不伪装成原参数回放。

参考：[LiteLLM callbacks](https://docs.litellm.ai/docs/observability/custom_callback)、
[Langfuse tracing practices](https://langfuse.com/docs/observability/best-practices)，2026-09-07 核对。

剩余主线仍为审批连续性、最终回复质量与原业务任务闭环；本次用户调用探测没有执行换货。
