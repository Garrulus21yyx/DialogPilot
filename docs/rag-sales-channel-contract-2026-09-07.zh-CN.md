# RAG 销售渠道过滤合同

本文记录上一轮静态目录修复，已由[运行时注入合同](rag-dynamic-filter-contract-2026-09-07.zh-CN.md)替代。当前实现从宿主配置读取目录，不再内置 web/store。以下保留为历史验证记录。

本轮修复：任意字符串不再能够直接成为销售渠道硬过滤。业务目录位于 application/sales_channels.py，当前支持 web（商家官网购买）、store（实体门店购买）。新增购买渠道先扩展该目录，随后导入来源；不以当前语料出现过的值推导完整业务目录。

链路：Conversation Agent 的 knowledge_options.sales_channel → knowledge_search.sales_channel → API 转换为内部 applicable_channel → PostgreSQL channel IN (所选值, global)。来源的 channel 字段、SourceRevision、检索请求及搜索范围均复用目录验证。global 仅表示通用来源；省略查询渠道表示不施加渠道过滤，返回证据中的适用条件仍需用于回答。

Schema 解释 ID 含义、配送方式与购买渠道的区别，以及假设性问题按假设渠道检索。未知或未支持渠道保留在 query 中，省略可选过滤。订单合同当前没有可直接注入的销售渠道事实，系统不从聊天入口或物流方式推断，也不以当前订单替换用户的明确假设。身份与权限继续由运行时独立注入。

## 兼容与迁移

- 模型/工具公开参数 applicable_channel 替换为 sales_channel；旧参数明确返回 INVALID_CONTRACT / INVALID_KNOWLEDGE_OPTIONS，需要按新 Schema 重新规划。
- 导入 API 保留 channel 名称，公开 enum 和校验。数据库列、来源版本算法、内部 applicable_channel 名称保持兼容，已有 web/store/global 无需重建索引。
- 自定义渠道须在目录登记后使用。未登记旧来源不会自动改成 global；读取源合同会拒绝，部署前需核对实际来源目录。本轮未检查生产数据库中的全部历史渠道。
- 评测语料选项和计划重放 Schema 已迁移；历史实验产物保持原始记录，不改写成新成绩。

## 验证

- 合同、Agent、导入及相关回归：109 passed。
- 独立测试 PostgreSQL 的来源、适用范围、存储、检索及渠道合同：54 passed（与上一组重叠9项；之后补入导入API目录检查）。
- 新测试枚举支持渠道，并使用固定种子的50个随机非法ID，加上 shipping、express、聊天入口等值，跨 Schema、解析、来源、内部请求验证拒绝行为。
- 工具入口验证：未知时无渠道过滤；明确 store 保留为 store，不被上下文 web 覆盖；权限字段独立传递；非法过滤不会调用检索。
- 已有真实数据库测试验证选定渠道加通用来源、其他渠道排除，以及版本、撤回和证据复读。
- 外部推理调用0。未新增 Recall/nDCG 或真实模型渠道选择准确率结论。枚举约束不能证明模型不会在合法值之间误选。

实现与所测边界验证完成；不据此宣称整个 RAG 或自然语言语义问题全部关闭。
