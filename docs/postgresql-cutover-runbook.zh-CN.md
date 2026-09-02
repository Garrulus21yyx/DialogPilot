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

X-T01 的仓库级 schema registry 由以下命令重建；输出必须与冻结 artifact 完全相同：

```bash
python scripts/create_x_t01_schema_registry.py \
  --output governance/schema/x-t01-schema-registry-v1.json
python scripts/rehearse_x_t01_restore.py \
  --database-url "$DATABASE_URL" \
  --output governance/evidence/x-t01/restore-local-v1.json
```

若宿主没有 `pg_dump/pg_restore`，本地 Compose 演练可额外指定
`--postgres-container dialogpilot-postgres`。`upgrade_to()` 只允许沿冻结线性链向前；已在更高 revision 的
数据库请求较低 target 会返回 `ForwardOnlyMigrationError`。禁止执行 Alembic downgrade 来伪造可逆性；
逻辑修复发布新 revision，灾难恢复使用完整备份并重新做 count/hash reconciliation。

## ResponseDelivery 专用流程（M1-T03A）

1. 升级到 Alembic `20260902_0005`。旧 SQLite 仍是唯一 writer；先执行 `export` 和 `backfill`，
   再用 `reconcile` 做只读 shadow compare。缺 tenant/invocation、重复 final、scope 冲突或 checksum
   不符必须停止。
2. 维护窗口先停止新 admission 和 legacy delivery worker，再执行 `freeze`。该命令先在 SQLite
   `BEGIN IMMEDIATE` 内把 selection/ACK writer fence 设为 `FROZEN`，随后才把 PostgreSQL binding
   从 `SQLITE_ACTIVE` CAS 到 `FROZEN`，所以中途崩溃只会损失可用性，不会出现双 writer。
3. 冻结后执行 `activate`：重新导出 final snapshot、以 `FINAL_DELTA` 补齐、在 binding 行锁事务内
   重新计算 count/status/ID/content hash，匹配后切成 `POSTGRES_ACTIVE`，最后把 SQLite fence 设为
   不可逆 `RETIRED`。只有 binding active 且 legacy retired 后才能恢复 PG worker claim/new admission。
4. binding switch 前可执行 `abort-before-switch`：先把 PG binding 恢复为 `SQLITE_ACTIVE`，再释放相同
   freeze ID 的 SQLite fence。switch 后此命令 fail closed；只允许 PG restore 或 forward-fix。

```bash
python scripts/migrate_response_deliveries.py export \
  --sqlite-path "$RESPONSE_DELIVERY_DB_PATH" --output delivery-snapshot.json
python scripts/migrate_response_deliveries.py backfill \
  --snapshot delivery-snapshot.json --database-url "$DATABASE_URL"
python scripts/migrate_response_deliveries.py reconcile \
  --snapshot delivery-snapshot.json --database-url "$DATABASE_URL"
python scripts/migrate_response_deliveries.py freeze \
  --sqlite-path "$RESPONSE_DELIVERY_DB_PATH" --database-url "$DATABASE_URL" \
  --freeze-id "$FREEZE_ID" --actor "$OPERATOR"
python scripts/migrate_response_deliveries.py activate \
  --sqlite-path "$RESPONSE_DELIVERY_DB_PATH" --database-url "$DATABASE_URL" \
  --freeze-id "$FREEZE_ID" --actor "$OPERATOR" --switched-at "$SWITCHED_AT"
```

Legacy `selected` 没有发送 receipt，固定迁成 connector=`NONE` 的 `DELIVERY_UNCERTAIN`；所有 legacy
outbox 都是 acknowledged/automatic-send-disabled，不会因 backfill、进程恢复或 restore 自动重发。
旧 SQLite 没有 outbox ID，exporter 首次确定性生成 compatibility ID 并由 snapshot checksum 冻结；
`response_id` 原样成为 `publication_id`。

## 凭据轮换

1. 创建新 role credential 并赋予同一最小权限；2. 部署新 secret 并等待旧 pool 排空；3. 撤销旧凭据；
4. 运行 migration/read/write/outbox probe；5. 归档不含 secret 的 rotation evidence。DATABASE URL 不写日志。
