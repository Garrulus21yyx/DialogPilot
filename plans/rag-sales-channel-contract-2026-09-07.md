# 销售渠道 metadata 合同

状态：implementation_complete，所测合同通过；模型语义选择未重新验收。沿用 planning-with-files 跟踪本轮修复。

根因：Agent 的 applicable_channel 是任意字符串，经长度检查即成为 SQL 硬过滤；字段业务语义与合法值没有同一负责人。已有语料使用 web/store/global，订单合同未提供可直接注入的销售渠道事实。

正向合同：业务目录定义已支持的销售渠道 ID、名称、含义；Agent 使用可选 sales_channel 枚举，仅根据明确的问题或可信上下文选择；未知省略。来源导入及内部检索使用同一目录校验。global 是来源的通用适用标记，不是用户销售渠道。检索边界将 sales_channel 转换为内部 applicable_channel，并继续检索所选渠道或global。系统权限仍独立注入；不从当前聊天入口推断订单渠道，也不覆盖假设性问题的明确渠道。

1. complete：业务目录、公开 Schema、参数解析、来源及内部检索校验统一。
2. complete：迁移生产 API/评测入口，性质与真实数据库验证未知/非法/正常/假设条件。
3. complete：记录兼容边界与验证结果，commit/push；不声称枚举可证明用户情境语义。

验证：109项入口/合同回归通过；54项独立PostgreSQL与检索回归通过（有重叠）。外部API 0。详见 docs/rag-sales-channel-contract-2026-09-07.zh-CN.md。
