# GitHub Pages 全站内容校准计划（2026-09-02）

## 目标

依据当前重构后的项目实现、契约、评测与生产化资料，更新并补充 `main/docs` 发布的 8 个主导航页面；核查页面间口径、链接、导航与构建结果，提交并推送到远端。

## 约束

- 代码、测试、契约与已生成证据是事实来源；页面不得把计划态写成已落地。
- 保留工作区中与本任务无关的既有未跟踪文件和改动。
- 只提交本次 Pages 更新涉及的文件。
- 推送前进行本地构建/链接/事实一致性检查，并审阅最终 diff。

## 步骤

1. **done** — 盘点 8 个页面、导航配置及重构后链路的权威事实来源。
2. **done** — 建立页面职责矩阵与统一术语/状态口径，识别陈旧、缺失和冲突内容。
3. **done** — 更新、增补 8 个主导航页面及必要的导航/样式资源。
4. **done** — 核查站内链接、数据引用、实现状态、页面构建和移动端导航。
5. **in_progress** — 审阅 diff，仅提交本任务文件，推送当前分支并确认远端状态。

## 产出文件

- `plans/github-pages-refresh-2026-09-02.md`（本计划）
- `docs/index.md`、`docs/full-architecture-tutorial.zh-CN.md`
- `docs/interview-guide.md`、`docs/architecture.md`、`docs/project-pitch.md`
- `docs/agent-evolution.md`、`docs/evaluation-500.zh-CN.md`
- `docs/rag-pipeline-evaluation.zh-CN.md`、`docs/customer-service-rag-production-audit.zh-CN.md`
- `docs/_config.yml`、`docs/assets/dialogpilot.js`

## 风险与待核实项

- 用户已确认：内容以当前 `feat/customer-service-target-architecture` 分支为事实并 push 当前分支；不切换或更新 `main`。线上 Pages 要待该分支后续合入 `main` 才刷新。
- 工作区已有大量未跟踪成果，需判定哪些是本次页面事实来源、哪些应保持不入提交。
