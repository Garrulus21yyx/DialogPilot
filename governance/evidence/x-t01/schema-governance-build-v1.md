# X-T01 schema/version governance build evidence v1

日期：2026-09-02。状态：`IMPLEMENTED / PRODUCTION SNAPSHOT REVIEW PENDING`。

## Owner 与正向合同

- `SchemaVersionRegistry` 分开拥有 PostgreSQL domain schema、legacy Agent checkpoint schema 与 Agent
  runtime code compatibility；PostgreSQL revision 不能冒充 checkpoint version。
- PostgreSQL 迁移是单 head、无分支的 15-revision forward-only chain。每个文件 checksum 进入 immutable
  ledger；目标 revision、ledger subset、DataLocation registry transition 与 approved head artifact 必须一致。
- `upgrade_to()` 支持空库、逐版本和跳版本前向升级；跨进程 migration owner 由 PostgreSQL advisory lock
  串行化。任何 downgrade 请求 typed fail，恢复只能走 forward-fix 或完整 snapshot restore。
- 新 ReAct checkpoint 由 RunStore 强制写入 checkpoint/code version，caller 不能伪造；legacy v0 可由显式
  read-migrate allowlist 恢复，未知 schema/code 在读取、resume 或写节点前 fail closed。
- `governance/schema/x-t01-schema-registry-v1.json` 冻结 15 个 revision/down-revision/checksum、四次
  DataLocation registry transition、三个 schema owner contract 与 downgrade policy，并由脚本可重复生成。

## 本地验证证据

| 产物 | SHA-256 |
|---|---|
| Schema registry artifact | `4b46f0d6e0831f7a645023adfba0feffeb0b0565e5508b4a9e716e79dcf1a86f` |
| Local restore evidence | `0680e1cb627973fce506f0c25be2872e327c7d4a17ef9a6f4982b1f4203ec498` |

本地隔离 PostgreSQL 演练执行 empty→head、custom-format dump、全新数据库 restore、head/ledger verify，随后
对 15 条 migration ledger 与 4 条 DataLocation registry facts 做 canonical count/hash reconciliation：
`record_count=19`、`records_sha256=7dfcc4d23e59da76fc9ab39a4d5c6b714d698f37b680d2ba29b982124a597be9`，
结果 PASS。该 RTO 只属于本地空数据治理快照，不是生产 RTO。

聚焦验证：

```text
TEST_DATABASE_URL=postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot \
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_schema_version_registry.py tests/test_postgres_foundation.py \
  tests/test_react_resume.py
→ 18 passed
```

覆盖 artifact 重建、空库跳 head、15 revision 逐版本、并发 migration owner、checksum drift、downgrade、
checkpoint caller spoof、legacy compatibility 与 unknown version fail-closed。
全量仓库验证：`812 passed in 46.38s`；ruff 与 `git diff --check` 通过。

## 尚未 VERIFIED

- 没有 production snapshot 副本，因此尚未证明生产数据量、扩展、锁时长、RPO/RTO 或业务表 count/hash；
- 没有独立 Platform/Application reviewer signature；
- M3 LangGraph Saver/Store schema 尚未启用，后续 M3-T01/T02 必须在本 registry 发布独立 contract 后才能
  首写，不能复用 legacy checkpoint version。

因此 X-T01 build complete，但 M1/M2 Exit 继续被 production snapshot/restore 与独立复核阻断。
