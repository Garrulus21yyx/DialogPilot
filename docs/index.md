---
layout: default
title: DialogPilot 文档
---

# DialogPilot 文档

DialogPilot 当前以本地可复现客服 Agent 为目标：PostgreSQL 是请求、知识、服务经历、用户事实和回答发布的权威存储，Redis 只保存当前会话投影。仓库不保留旧数据迁移、Shadow/Canary 或生产签署模拟。

## 推荐阅读顺序

1. [项目 README](../README.md)：运行方法、当前能力和真实边界。
2. [目标架构](customer-service-agent-target-architecture.zh-CN.md)：最终责任划分。
3. [实施计划](customer-service-agent-implementation-plan.zh-CN.md)：节点、合同和验收场景。
4. [架构边界](architecture.md)：当前代码的数据流和 Owner。
5. [项目讲述](project-pitch.md)：简历与面试表达。
6. [面试追问](interview-guide.md)：高频问题与事实校准。

本地验收结果见：

- [E2E 机器报告](../evaluation/reports/local-e2e-v1.json)
- [PostgreSQL 恢复报告](../evaluation/reports/local-postgres-restore-v1.json)
