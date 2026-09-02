# M4-T03P final provider budget build evidence

日期：2026-09-02。状态：`FOUNDATION IMPLEMENTED / M4-T03 IN PROGRESS`。

## Root cause 与 Owner

既有 `ContextAssembler` 只约束 ChatApplication 上游选择的 section/history，并使用固定 reserve；它看不到 Worker
system prompt、Bundle/task contract、ReAct 累积 tool use/result、tool schemas，且真实调用的 `max_tokens` 可能由
model reasoning profile 提高。因此上游装配成功不等于真实 provider request 不超限。

本 slice 将最终预算 Owner 放在所有 Anthropic-compatible 调用共用的 `create_message` 边界。每次调用先由
`ModelProfile.request()` 得到真实 request，再统一统计 system、全部 messages/history/content blocks、tool schema、
协议开销和真实 output reserve；总量超过 pinned `max_context_tokens` 时返回 typed
`ProviderContextBudgetExceeded`，provider side effect 尚未开始。ReAct 每一步都经过同一边界，故累计工具结果会被
重新计费，不可能因只预算首轮而静默越界。

`ModelPolicy` 为每个 role 解析独立 `MODEL_<ROLE>_MAX_CONTEXT_TOKENS`，非法/过小值在启动期 fail closed。估算与
provider-reported usage 同时进入调用记录，暴露 estimate/actual ratio 与最大预计 context utilization，供后续真实
tokenizer/provider count 校准，不能把估算值冒充账单 token。

## 边界

本 slice 只建立最终 admission gate，不在 provider adapter 里语义截断。M4-T03 仍需版本化 node/task/route
`ContextPolicy`、可回读 receipt/locator 的工具结果压缩、provider native cache capability/privacy policy 与 shadow
对账，因此保持 in progress。

冻结合同：`governance/concurrency/m4-t03-provider-budget-v1.json`。

验证：provider/model/context/ReAct 聚焦 `118 passed in 4.61s`；全量仓库 `919 passed in 83.58s`；Ruff 与
`git diff --check` 通过。生成式矩阵覆盖 0/1/10/100 个 tools 与 0/100/10000 字符累计结果，所有组合只能在
预算内 admission 或 typed rejection，拒绝路径 provider 调用次数为零。
