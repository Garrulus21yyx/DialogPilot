# M4-T03 provider native cache policy build evidence

日期：2026-09-02。状态：`POLICY FOUNDATION IMPLEMENTED / RUNTIME ENABLEMENT PENDING`。

`ProviderCacheGate` 将 provider capability 与 tenant privacy policy 分开建模。只有 mode、tenant、region、data class、
policy/provider TTL、multimodal、no-training、zero-retention、deletion handling 和 usage fields 全部匹配时才返回
ENABLED；任一不匹配返回 DISABLED reason，正常走未缓存推理，不把 cache miss/unsupported 当模型失败。

explicit mode 只在 caller 提供的 stable system prefix 上设置 provider-native breakpoint；suffix/current input 保持实时，
tool schema 只在 caller 显式声明时于最后一个稳定 schema 设置 breakpoint。prefix 不匹配时 fail-safe 关闭 cache。
`create_message` 消费 typed `ProviderCacheInvocation`，未知 dict 不能绕过合同，并记录 policy status/reason 以及 provider
返回的 cache creation/read tokens。cache 不成为 Evidence、Memory 或业务结果缓存。

冻结合同：`governance/concurrency/m4-t03-provider-cache-v1.json`。聚焦 `32 passed`，Ruff/diff checks passed。
全量仓库回归：`937 passed in 84.22s`。

当前没有在生产 caller 默认构造 invocation，因此仍是 disabled-by-default；真实 tenant/region/privacy 配置、provider
staging conformance、图片增删与 hit/miss answer/Coverage/security 等价性尚未执行，不声明 M4-T03 或 cache READY。
