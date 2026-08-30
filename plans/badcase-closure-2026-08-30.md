# Bad Case 闭环实施计划

目标：为 DialogPilot 增加最小但完整的 Bad Case 工程闭环。线上信号只生成候选；人工确认期望后，才能导出到现有四层评测体系的 dev/regression，不能自动提升为 Gold 或 holdout。

## 约束

- `BadCaseRegistry` 是生命周期和持久化唯一 Owner，工单、Trace、Verifier 只是信号生产者。
- 保存脱敏快照和版本证据，不保存 JWT、密钥、完整工具结果或未发布候选。
- 自动捕获不得阻断主请求；P0/P1 捕获失败只记录日志。
- 状态转换必须闭合、可审计、幂等；不允许跳过人工归因直接导出。
- 已用于修复的样本只能进入 `dev` regression；不得成为 fresh heldout。

## 步骤

1. `done` — 审查现有 SQLite Owner、API/Auth、Trace/Verifier 和评测合同。
2. `done` — 实现 BadCase 类型、脱敏、去重、SQLite Registry 与状态机。
3. `done` — 接入用户反馈、自动捕获和管理员查询/流转 API。
4. `done` — 实现审核后导出四层 regression JSONL 的脚本。
5. `done` — 增加状态机、隐私、自动捕获、API、导出与回归测试。
6. `done` — 更新 README、架构教程、面经和 Pages 内容。
7. `done` — 全量验证、提交、push、Docker 与 CI/Pages 验证。

## 变更文件

- `services/badcase_registry.py`
- `scripts/promote_badcase.py`
- `tests/test_badcase_closure.py`
- `api/main.py`
- `Dockerfile`、`docker-compose.yml`、`.env.example`
- `README.md`、`docs/architecture.md`、`docs/project-pitch.md`
- `docs/full-architecture-tutorial.zh-CN.md`、`docs/interview-guide.md`
