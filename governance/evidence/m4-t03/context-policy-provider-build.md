# M4-T03 ContextPolicy/provider boundary aggregate evidence

日期：2026-09-02。状态：`IMPLEMENTED / VERIFICATION DEPENDENCIES PENDING`。

M4-T03 的七项 build contract 已由四个 owner slice 闭合：

1. `ContextPolicyV1` 按 closed node/task/route algebra 选择 section，并输出逐 section decision trace；
2. 所有 `create_message` 调用在 provider side effect 前执行最终预算；
3. 预算覆盖 system、全部 messages/history/content block、tools schema、协议开销和真实 output reserve；
4. ReAct 工具结果先成为 receipt/locator-preserving structured envelope，再按 step 删除旧 excerpt；
5. 每个 ReAct provider step 从 immutable checkpoint messages 重建并重新 admission；
6. active tickets/Knowledge/Profile/History/Summary 的 `90/85/75/65/55` 由版本化 policy 唯一拥有；
7. native prompt cache capability 与 tenant/region/data-class/TTL/retention/deletion policy 双门禁，默认无 typed
   invocation 时关闭，显式模式只标 stable prefix/schema breakpoint。

聚合证据：

- [provider budget](../m4-t03p/provider-budget-build.md)
- [versioned ContextPolicy](../m4-t03c/context-policy-build.md)
- [provider cache policy](../m4-t03cache/provider-cache-policy-build.md)
- [tool-result context](../m4-t03r/tool-result-context-build.md)

最新全量仓库回归 `942 passed in 84.00s`，Ruff/diff checks passed。

## 不构成 VERIFIED 的缺口

- 尚无真实 provider tokenizer/count API calibration 与 staging limit rehearsal；
- native cache 仍 disabled-by-default，没有 tenant-approved runtime enable、图片/schema 变化 staging conformance 或
  hit/miss answer/Coverage/security 等价报告；
- 任务相关 ActiveCase selection 属于 M4-T03B，当前 legacy active-tickets section 不能证明“无关工单不广播”；
- WorkingContext field/source/TTL/rebuild/terminal cleanup 属于 M4-T03A，并依赖 T03B/T03D/T04。

因此 M4-T03 可作为 T03B 的 build prerequisite，但不能作为 M4 Exit verified evidence。
