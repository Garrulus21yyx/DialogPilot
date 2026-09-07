# 中文电商 RAG 全链路首轮探测

状态：未通过，不扩到20条。实际调用5次Flash；没有得到可交付的政策答案，不能将1条Completed计为答案正确。

路径：模拟政策导入与版本索引 → 隔离PostgreSQL/Redis → 官方生产build_target_runtime → coordinator/admission → 当前Context → Conversation Agent → knowledge_search handler → PG混合召回/LLM精排 → 回答组装/核验/应用内发布。未执行HTTP鉴权、外部消息发送和业务写操作。只走到的阶段才计为覆盖。

| 案例 | 实际执行结果 | 失败位置 |
|---|---|---|
| 耳机拆封、否认质量问题，追问七天无理由 | Failed，knowledge_search未执行 | 规划选择refund_policy但没有resolved_query，编译器拒绝 |
| 确认质量问题，追问加急寄回是否报销 | knowledge_search返回OK；发布“服务暂不可用” | 生成器返回自然文本，运行时组装仍要求segments；composition_render失败，核验未执行 |
| 2026年3月1日适用的运费上限 | Failed，knowledge_search未执行 | 模型as_of=2026-03-01，knowledge_query_options要求带时区时间点 |

这三个问题均不能归因到chunk或向量召回。

规划接口需要将上下文知识查询的必填完整query要求体现在输出Schema；日期需要先明确日历日期到业务适用时间的解释合同，并让Schema与验证一致，不能在检索器下游任意补UTC。返回格式迁移需让生成、组装、核验共同使用同一合同后再验收。

## 工作区与证据边界

本轮未覆盖其他正在进行的生产修改。启动时保存composition_output、response_assembly、target_conversation_provider等文件SHA256；结束核对发现response_assembly已发生变化。日志明确为composition_render/ValueError/composition requires segments。不能用这次迁移中的探测证明最新工作区仍有同样问题，也不能仅因文件更新就声明已修复。audit.json保留前后指纹。

首轮冻结用例、所用模拟政策/版本、工具实际参数与结果、模型捕获、最终outcome均在artifacts/eval/rag-full-ecommerce3-2026-09-07。报告不把schema失败当无证据，不把框架Completed/coverage.complete=true当答案已受支持。

下一轮在生成/组装合同一致的固定快照上重跑这3条；先处理规划必填query和日期合同，再确认实际引用与核验。3条通过之后才扩20条，扩展查询策略排在入口稳定之后。

执行脚本：scripts/run_rag_tool_calibration.py --full-chain --scenario ecommerce-full --model <本地BGE-M3路径> --output <新目录> --max-api-calls 40。TEST_DATABASE_URL必须显式指向隔离测试实例；脚本新建独立数据库并在结束删除，Redis使用临时Unix socket。API模型本轮通过环境固定各角色deepseek-v4-flash/reasoning none。7项Context/角色相关测试通过，脚本编译通过；这些检查不是3条业务验收通过。
