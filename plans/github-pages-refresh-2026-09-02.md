# GitHub Pages 全站深度重写计划（2026-09-02，第二轮）

## 目标

依据当前重构后的项目实现、类型合同、数据库 schema、API、评测与机器报告，把 `main/docs` 的 8 个主导航页面恢复到原长教程的实现颗粒度并继续扩展；每条链路必须说明 Owner、数据结构、存储/过期、顺序、失败与恢复、API、验证证据和面试追问，最终直接更新线上 Pages。

## 约束

- 当前分支的代码、测试、契约与已生成证据是事实来源；页面不得把目标/计划态写成已落地。
- 保留工作区中与本任务无关的既有未跟踪文件和改动。
- 只提交本次 Pages 更新涉及的文件。
- 推送前进行本地构建/链接/事实一致性检查，并审阅最终 diff。
- 不以摘要、大纲或链接外包正文；首页必须顺序讲完从身份/意图到送达/长期服务连续性的整条链。
- 恢复并扩展旧教程的连续追问题库，覆盖当前实现和新架构延伸。

## 步骤

1. **completed** — 从当前实现提取 API、请求时序、路由代数、RAG 在线/离线、Memory 分层/过期、用户事实、多模态、工具/副作用、发布/送达、Handoff/Commitment、Trace 与评测的权威矩阵。
2. **completed** — 对照重写前长教程和当前 8 页，建立章节与问答覆盖差异表。
3. **completed** — 深度重写首页完整教程，恢复代码级顺序、配置、状态表、故障路径和大规模连续追问。
4. **completed** — 深化其余 7 页，使职责互补但正文自足，并补充新架构延伸问答。
5. **completed** — 自动核查 API/模块/配置引用、站内链接、导航、Jekyll 构建与页面规模；运行相关和全量测试。
6. **completed** — 审阅提交范围，推送当前分支与 `main`，等待 Pages built 后逐页线上验收。

## 产出文件

- `plans/github-pages-refresh-2026-09-02.md`（本计划）
- `docs/index.md`、`docs/full-architecture-tutorial.zh-CN.md`
- `docs/interview-guide.md`、`docs/architecture.md`、`docs/project-pitch.md`
- `docs/agent-evolution.md`、`docs/evaluation-500.zh-CN.md`
- `docs/rag-pipeline-evaluation.zh-CN.md`、`docs/customer-service-rag-production-audit.zh-CN.md`
- `docs/_config.yml`、`docs/assets/dialogpilot.js`

## 风险与待核实项

- 用户已明确：内容以当前 `feat/customer-service-target-architecture` 分支为事实，完成后直接推送 `main` 更新 Pages。
- 第一轮 261 行首页和精简问答被用户判定为颗粒度不足，本轮不能以通过构建或链接替代内容验收。
- 工作区已有大量未跟踪成果，需判定哪些是本次页面事实来源、哪些应保持不入提交。

## 第二轮验证记录

- 清洁 index 快照 Jekyll 构建成功；8 个渲染页面分别为 4298、1115、1238、1088、493、615、662、754 行。
- `api/main.py` 的 38 个 endpoint 路径均可在当前运行链教程中检索到。
- 8 个主页面的站内链接检查通过；源码/机器报告链接已改为 main 上的 GitHub blob。
- 相关链路测试：77 passed、5 skipped。
- 全量测试在提供 Compose PostgreSQL 测试 URL 后：884 passed；未提供 URL 的首次运行只有 2 个 stateful PostgreSQL fixture 因环境前置缺失失败，不属于文档或实现回归。
- `3b7a1fe` 已推送到当前分支与 `main`；GitHub Pages 构建状态为 `built` 且 commit 与之相同，8 个线上 URL 均返回 HTTP 200 并命中各页新增深度章节。
