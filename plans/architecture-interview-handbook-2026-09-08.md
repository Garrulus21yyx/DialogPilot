# 最新架构与面试手册交付计划

起点：2026-09-08，HEAD `59dd33c`，分支 `feat/customer-service-target-architecture`。
用户要求：依据最新代码更新完整架构、代码库结构、可口述追问、技术选型、RAG 数据与优化闭环，最后 push；文档必须完整作答而非大纲。

## 范围与版本
- 当前工作区存在并行代码、实验与文档修改。本任务只交付文档及其导航，不混入其他人的代码或实验。
- 按源码快照核对，并记录快照与未提交实现边界。RAG 主线与微调暂停状态不改变；本任务不运行付费实验、不宣称修复/质量关闭。
- 文档入口：更新 architecture.md、interview-guide.md、project-pitch.md；新建专用深入讲解正文与来源索引，兼容现有 Jekyll 页面。
- 现有未提交文档通过隔离 Git index/交付 checkout 保留，提交只含本轮明确编写内容。

## 步骤
1. done：源码与现有文档核对、参考页面与官方来源取证，确定当前事实和历史结果。
2. done：完成架构/目录/端到端调用链与核心模块讲解。
3. done：完成意图范式、框架、RAG、评测、恢复、多模态与面试追问的详细回答。
4. done：更新导航和项目讲述，检查源码链接、数据口径、Jekyll 构建与页面展示。
5. in_progress：审阅文档改动、隔离提交并 push；记录交付 SHA、验证结果和线上状态。

## 验收
- 每项重要选型有原理、替代方案、项目实现、失败路径、检验方法与边界。
- 至少覆盖用户指定的 intent paradigms / rewrite / RRF weights / HNSW / multi-agent / verifier / synthesizer / LangChain & LangGraph / 外部数据集与中文自建评测。
- 真实数字绑定报告与 split；模拟简历数字单独标明，不写入事实结论。
- 线上导航能到达完整正文；push 成功有远端 SHA 证据。

## 实际交付与检查
- 源码最终基点ceeab4e（起点59dd33c）；写作期间同步扩库实测，逐文件111项引用/证据身份见docs/assets/handbook/source-snapshot.json。未提交代码只读，不夹入文档提交。
- 完成完整架构、80个逐题短答/展开/追问/源码、RAG离线在线14章、项目讲述与事实/模拟分开的简历、官方来源与12份历史报告文本快照。
- 首页、架构页、旧完整教程统一引用同一新正文；历史专题标明旧口径，主导航指向新手册。
- Jekyll 4.4.1构建通过；7页本地链接/锚点无缺失；80题连续编号及完整回答检查通过。
- Playwright Chromium验证390/1440宽的5个核心页面无横向溢出；HNSW搜索2题、展开2题；Mermaid渲染1图无错误。修复源码长路径窄屏换行。
- 结果见docs/assets/handbook/validation.json。未重跑应用测试/付费模型；现有开发/质量缺口没有改为已关闭。
- Pages配置为main/docs，在/tmp/dialogpilot-handbook隔离checkout提交；当前工作目录开发分支与所有原修改保持。
- 下一步：只stage docs与本计划，提交并fast-forward push main，确认Pages构建及线上页面。
