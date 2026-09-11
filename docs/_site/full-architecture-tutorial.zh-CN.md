# DialogPilot 完整架构教程：从 FastAPI 到可验证交付

> 本页以当前分支代码为事实。第一部分讲当前已经接入 `/chat` 的运行链；第二部分收录目标架构及未完成项。两者不得混读。

{% include_relative _includes/current-runtime-deep-dive.md %}

---

## 第二部分：目标架构延伸，不冒充当前实现

下面是目标架构全文。它解释为什么计划引入 LangGraph 薄运行时、怎样继续完善长期记忆、多模态、恢复和生产治理。凡正文标为 `PLANNED`、`目标` 或 `M*` 的能力，都不是当前 `/chat` 已经完成的能力。

{% include_relative _includes/customer-service-agent-target-architecture-body.md %}
