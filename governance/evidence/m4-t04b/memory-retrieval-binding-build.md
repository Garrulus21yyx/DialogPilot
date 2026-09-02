# M4-T04B direct-cutover Memory binding evidence

日期：2026-09-02。状态：`SINGLE TARGET CONTRACT IMPLEMENTED / ACTIVATION NOT EXECUTED`。

用户确认没有旧运行数据，目标架构采用 clean rebuild + direct cutover。因此 binding Owner 不再建模
legacy/previous/candidate、SHADOW/PINNED_CANARY/LEGACY mode 或运行时 rollback；这些状态没有业务事实来源，保留它们只会形成
第二条运行路径。

`MemoryRetrievalBinding` 只绑定一个完整 policy fingerprint + backend generation + corpus generation target、`enabled` 与
monotonic version。初始化必须 disabled；同 target 重放幂等，异 target 冲突。disabled 状态可用 expected-version CAS 完整替换
target；`activate` 是单向切换，数据库 trigger 禁止 enabled 后修改 target 或退回 disabled。migration `0021` 尚未发布且数据库无旧
数据，因此直接收敛原 migration，不新增伪迁移/数据搬运脚本。

聚焦 binding/schema-governance 回归：`10 passed`；全仓：`974 passed in 99.45s`。Ruff 与 `git diff --check` 通过。
真实 offline replay 与 `ChatApplication` E2E 未完成，binding 继续 disabled。
