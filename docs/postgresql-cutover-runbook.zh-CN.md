# PostgreSQL 数据迁移与 Cutover Runbook

## 不变量

- 每类事实任何时刻只有一个可写 Owner。
- migration batch、source snapshot、delta 和 binding switch 都有稳定 ID、checksum、count 与操作者。
- `UNAVAILABLE` 是 typed failure；任何组件不得静默改读/改写 SQLite。
- cutover 顺序不允许出现 `switch writer → stop old writer` 的双 writer 窗口。

## 标准流程

1. **Prepare**：应用 migration；完成空库、逐版本与生产快照副本升级；记录 revision/文件 checksum。
2. **Snapshot export**：在 source owner 的一致性边界导出，记录 high-watermark、表 count、逐记录
   canonical checksum、tenant 缺口和 retention 范围。
3. **Backfill**：以稳定 source key 幂等写入 target staging；同 key 异内容立即停止。
4. **Shadow-read**：只读比较 source/target，不让 target 反向修正 source，不向用户暴露 target 结果。
5. **Freeze**：停止旧 claim，等待/接管已有 lease；停止旧 writer，记录 final watermark。此时尚未切换。
6. **Final delta + reconcile**：导入 watermark 后增量；count、key set、内容 checksum、状态分布全部相等。
7. **Atomic binding switch**：以单个版本化 binding/pointer CAS 将 repository 指向 PostgreSQL。
8. **Start new writer**：只有 switch 成功后才启动新 claim/write；执行 read-after-write 与 outbox probe。
9. **Observe**：保留旧库只读，监控 conflict、lag、outbox、tenant denial、连接池和 SLO。
10. **Forward-fix / restore**：逻辑缺陷优先 forward-fix；需要恢复时停止新 writer，恢复已演练备份，
    reconcile 后原子切回。禁止把同一未知副作用 invocation 在另一 writer 重放。

## 备份与恢复演练

```bash
pg_dump --format=custom --no-owner --file dialogpilot.dump "$DATABASE_URL"
createdb dialogpilot_restore_drill
pg_restore --exit-on-error --no-owner --dbname dialogpilot_restore_drill dialogpilot.dump
python scripts/run_postgres_migrations.py --database-url "$RESTORE_DATABASE_URL" --verify-only
```

验收证据必须包含：备份 SHA-256、起止时间、PostgreSQL major、Alembic head、ledger、关键表 count/
checksum、RPO/RTO、执行人与清理确认。恢复演练只对隔离数据库执行。

## 凭据轮换

1. 创建新 role credential 并赋予同一最小权限；2. 部署新 secret 并等待旧 pool 排空；3. 撤销旧凭据；
4. 运行 migration/read/write/outbox probe；5. 归档不含 secret 的 rotation evidence。DATABASE URL 不写日志。
