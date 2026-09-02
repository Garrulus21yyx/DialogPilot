# M4-T04 ServiceEpisode direct-cutover checklist

本项目当前没有线上流量或必须保留的旧运行数据。`retrieval.memory_retrieval_bindings` 只保存一个新
ServiceEpisode target，不保存 legacy、previous、candidate、shadow、canary 或运行时 rollback 路径。

1. 用 `initialize(tenant_id, target)` 创建 `enabled=false` 的唯一 binding；相同 tuple 重放幂等，异 tuple 冲突。
2. 离线校准期间如需替换 generation，只能对 disabled binding 调用
   `replace_before_cutover(expected_version)`；每次 CAS 必须完整替换 policy/backend/corpus generation tuple。
3. 验收 canonical fixture replay、tenant/user/deletion 性质、EvidenceReceipt 和真实 `ChatApplication` E2E。
4. 验收通过后调用一次 `activate(expected_version)`。数据库使 enabled target 单调且不可变；不存在切回旧链的运行时操作。
5. 同一变更删除旧 raw-memory reader、writer、adapter、索引配置与依赖。开发回退使用 Git，不在运行时保留第二条链。

当前实现只完成 1～2 和单向 activation 合同；第 3 项完成前不得执行第 4～5 项。
