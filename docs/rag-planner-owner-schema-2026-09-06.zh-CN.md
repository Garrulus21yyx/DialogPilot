# 规划输出 owner 契约准备与反例

当前新增 `planning_output_schema()` 位于 ConversationAgent 的应用 owner，复用 supported goals、missing fields，并由知识工具合同提供 knowledge_options 字段与长度。生产 provider 尚未切换，旧 compiler 尚未收紧；这是一项输出迁移准备，不是完整实施。

支持的 wire 输出：resolved 必须包含 1–4 goals 且不含 missing_fields；insufficient_context 必须包含 missing_fields 且不含 goals；out_of_scope 只包含 status。目标 ID 可省略，保留现有编译器按位置生成 ID 的行为。实体绑定、授权、时间解析、依赖与 Registry 支持仍由实际编译器负责。新 schema 有意拒绝旧 compiler 宽松接受的空字符串、额外字段和非活动分支字段，因此不能声称是无差异替换。部分执行并未新增。

## 六条 Pro/none 回放

固定已消费六条输入、当前 goal 描述、4096 上限、structured_native；仅把上一轮评测 schema 换为应用 owner schema。6 次 API，6/6 schema 合规，6/6 编译，6/6 双工具规划，输出 878 tokens。但逐例检查否定了“格式通过就是查询成功”：

- B20 查询变成“机器型号 B20 是否支持试装？”，丢失机器型号未知这一必要条件，并把 B20 指认为机器型号。
- 质量审核案例添加 order → refund_status → policy 依赖，退款状态失败可能阻断独立规则查询；赠品案例也添加了不使用订单返回值的依赖。
- 其余拆封、非质量、赠品已用、尾款和省略历史的主要条件保留。实际检索和答案未执行，不能报告最终成功率。

原始捕获未修改，保存在 `artifacts/eval/rag-planner-owner-schema6-pro-2026-09-06/cases.jsonl.gz`。独立审查指出编译通过不证明 schema 合规，因此 summary 单独记录捕获后的本地 schema 校验；脚本对后续 `--current-output-schema` 运行直接记录 `wire_schema_valid`。不要把新增字段伪写进历史捕获。

## 验证与下一步

136 项相关测试通过，覆盖状态字段组合、空值与类型组合、长度边界、owner 枚举、shape 合法但授权/依赖无效的反例及现有 Agent 和知识合同。独立限定审查原先运行 66 项，指出的 shape 评分缺口和 options 重复定义已修正。

当前没有证据支持立即生产切换。下一步需验证统一 Agent 查询是否保留已知与未知条件、依赖是否确实消费前序结果，并将这些指标加入实际混合任务验收；不通过下游猜测补条件。后续 provider 接线必须同时迁移文本回放、本地模型适配器、类型失败与生产验收，不能只修改 API 请求参数。
