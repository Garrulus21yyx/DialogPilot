# Command-primary P0–P4 收敛报告

## 边界

本轮只覆盖当前生产 Registry 声明的只读客服能力：知识回答、退款资格查询、
退款状态 continuation 和图片文字读取。数据是项目作者编写的 synthetic contract，
不是实际业务流量，也不构成 SOTA 证据。

## P0：唯一决策 Owner

`structured_read_only_primary` 现在显式声明 authority。它覆盖 Knowledge、
Clarify、只读 AgentTask 和 OOS；规划失败时返回 typed failure，不再无声回退旧
Intent。旧 Intent 投影由最终 `TurnPlan` 重新计算，只用于兼容输出，篡改保存的
投影不会改变 Route、Work、risk、requirements、tools、approval 或 Flow 状态。

迁移模式 `shadow`、`knowledge_primary` 和 `structured_knowledge_primary` 仍然允许
旧链处理其未接管范围，便于做对照实验。

## P1：客服 Command Gold

`data/eval/customer-service-command-gold-v1` 冻结了 8 条 Dev、4 条 sealed
Heldout 和 4 条澄清对话合同。每条包含当前 Flow 状态、history、用户消息、正确
Command/Flow/参数或 Clarify/OOS，以及预期下一状态。manifest 固定文件 checksum，
validator 验证 Registry 边界和 Dev/Heldout 不重叠。Heldout 尚未用于调参或评分。

## P2：57.5% 失败归因

对现有 SGD Dev 1,200 条预测按“最早权威失败边界”重新归因：

| Owner | 数量 | 占 510 个失败 |
|---|---:|---:|
| Terminal decision | 365 | 71.57% |
| Argument binding | 94 | 18.43% |
| Provider output | 25 | 4.90% |
| Command kind | 19 | 3.73% |
| Flow selection | 7 | 1.37% |

这个 runner 没执行真实工具和 Publication，因此没有虚构这些下游阶段的失败。

## P3：Flow Retriever shadow

新增 deterministic lexical Flow retriever，只做 shadow，不裁剪 Structured LLM
可见的 Registry，也不授权 Encoder ACCEPT。在 SGD Dev 的 600 个有 Flow Gold 的
case 上：Recall@1 23.83%、@3 35.33%、@5 43.33%、@10 55.50%。当前结果不足以
替代 53 Flow 全量 Prompt；其价值是建立可复现基线，并证明必须继续改 retrieval。

## P4：结构化澄清和多轮评测

Structured LLM 的输出合同升级为 `status + commands + clarification`。
Clarification 包含 typed reason、`missing_dimensions` 和 Registry-bounded
`candidate_flow_ids`，并贯穿 RoutePolicy、TurnPlan、pending signal 与最终追问。
多轮 scorer 同时报告必要澄清 Recall、不必要澄清率、候选消除、一次追问解决率、
OOS 误判和平均增加轮数。

使用 `.env` 中真实 provider 重跑原 20 条 Clarify Dev 后，旧 oracle 通过 5/20：
5 条 CLARIFY、8 条 NO_SUPPORTED_FLOW、7 条因不满足新 clarification schema 而成为
INVALID_PROVIDER_OUTPUT。这里不能简单解释成“模型只有 25%”：原 20 条主要是商品
识别和安装环境，而当前生产 Registry 只注册知识回答、退款只读和图片文字读取；旧
oracle 却一律要求 CLARIFY，和新合同“仅 Registry objective 算 supported”不一致。
因此该 run 只证明严格解析和 fail-closed 已生效，同时暴露出旧 Dev scope 与当前
Registry 不匹配；它不是当前 bounded Command Gold 的质量分数。

## 验证状态

Command-primary 相关测试：42 passed、2 skipped。全仓测试：892 passed、156
skipped、3 failed；3 个失败均不在本轮因果面：一个来自工作区既有 RAG bundle
字段未同步，两个需要未配置的 TEST_DATABASE_URL/DATABASE_URL。SGD Test 与客服
Heldout 均未消费。
