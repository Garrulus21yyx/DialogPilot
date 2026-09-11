# ADR-0001：PostgreSQL Platform Foundation

- 状态：Accepted
- 日期：2026-09-02
- Owner：Platform + Application
- 适用范围：新增 Conversation、Invocation、Outbox 事实；不提前迁移 Ticket、Delivery、RunStore

## 决策

1. 生产关系数据库固定为 PostgreSQL 18；本地与 CI 使用官方 `postgres:18.1-alpine` 镜像。
2. Python 驱动固定为 Psycopg 3.3.5 binary distribution，连接池固定为 psycopg-pool 3.3.1。
   在线代码只从进程级 pool 获取连接；数据库不可用时返回 typed unavailable 并 fail closed，禁止回退
   SQLite 或另一事实库。
3. schema migration 的唯一 Owner 是 Alembic 1.19.1 + SQLAlchemy 2.0.52。应用启动不隐式建表，
   发布流水线先显式执行 `scripts/run_postgres_migrations.py`。
4. namespace 固定为：`dialogpilot_platform` 保存 migration ledger/平台控制事实；
   `dialogpilot_app` 保存 Conversation、Invocation、Outbox 等应用事实；扩展能力以后使用独立 schema，
   不写 `public`。
5. 普通事务使用 PostgreSQL `READ COMMITTED`。唯一键、CAS 与 transactional outbox 依赖数据库约束；
   需要可串行化业务不变量的命令由 repository 显式升级隔离级别并重试 typed serialization failure，
   不全局切成 `SERIALIZABLE`。
6. schema change 只允许 forward migration。已应用 revision 的文件 checksum 写入
   `dialogpilot_platform.migration_ledger`，漂移时拒绝继续；回滚采用 forward-fix 或已演练的完整 restore，
   不自动执行未知破坏性的 downgrade。
7. 备份使用 `pg_dump --format=custom`；restore 演练在隔离数据库执行 `pg_restore`，校验 Alembic head、
   ledger checksum、关键表 count/checksum。生产凭据由部署 secret 注入，至少季度轮换；不得写入仓库。

## 单主边界

- M1 首期只有新建的 Conversation/Invocation/Outbox 由 PostgreSQL 单主。
- ResponseDelivery 从新安装起直接由 PostgreSQL 单主；仓库不维护 SQLite Delivery 或迁移 binding。
- TicketService 与 RunStore 的存储边界由各自模块声明；任何事实都不得双写到第二权威库。

## 依据

- Psycopg 官方安装与 pool 文档：<https://www.psycopg.org/psycopg3/docs/basic/install.html>、
  <https://www.psycopg.org/psycopg3/docs/advanced/pool.html>
- Alembic 官方文档：<https://alembic.sqlalchemy.org/en/latest/>
- PostgreSQL 18 schema 与隔离级别：<https://www.postgresql.org/docs/current/ddl-schemas.html>、
  <https://www.postgresql.org/docs/current/transaction-iso.html>
- PostgreSQL 官方 Docker image：<https://hub.docker.com/_/postgres>

## 后果与非目标

- 增加 PostgreSQL、migration 与集成测试的运维成本，换取跨实例唯一约束、事务 outbox 与明确恢复面。
- SQLite 仍可作为局部单元测试实现或待迁移领域的兼容 source；它不再承接任何新增全局事实。
- 本 ADR 不宣称 Ticket、Delivery 或 RunStore 已迁移，也不引入双写兼容层。
