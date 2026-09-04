# DialogPilot Target Architecture v1 运行手册

## 请求执行形态

`/chat` 按成本与风险选择最短安全路径：

1. Pending input、approval、reconciliation 等已绑定状态由 Deterministic Resolver
   直接恢复；
2. 明确请求由 bounded fast path 生成 Registry-backed Command；
3. 其余请求先经过 Target Encoder。只有 artifact 中 `enabled: true` 且与当前
   Registry 绑定的只读 Tool 或复合 Skill 可以 ACCEPT；
4. Encoder DEFER 后调用 Structured Semantic Router；
5. `DIRECT` 不启动领域 Agent，`AGENT_TASK` 只启动一个领域 Worker；只有互相独立的
   多领域 WorkItem 才由 LangGraph `Send` 并行派发；
6. 写操作进入预定义 Flow，经 approval、operation key、Receipt 和 reconciliation。

退款领域特别区分：政策问题直接读取 `knowledge_search`，资格问题直接调用
`refund_eligibility_check`，状态问题直接调用 `refund_status`；只有明确要求执行退款
才创建 `execute_refund:v1` Workstream。物流状态复用 `order_lookup`，发票政策复用
Knowledge Provider。单 Tool 任务不包装成 Skill，也不启动领域 Agent。

Registry 当前包含 6 个领域 Agent、19 个原子 Tool、1 个复合 Skill
（`product_identification`）和 5 个受控 Flow。Agent 在 DELEGATED WorkItem 的
能力包络内可动态组合 Tool/Skill；商品类别和属性只作为 Catalog/RAG 数据。

显式图片文字/OCR 请求直接调用共享 `media_read`；外观、区域、控件或空间关系判断
委派一个 Agent，并只开放共享 `media_observe`。附件存在本身不会触发 Product Agent。
`media_observe` 的输出是媒体观察，不得满足订单、退款或账户实时状态 requirement。

同轮出现账户安全任务和 Registry 标记为 interruptible 的业务写请求时，RoutePolicy
只阻断后者；安全查询、其他只读任务和人工转接继续执行。被阻断的写流程不会运行
预检 Tool、不会创建 Flow，用户解决安全问题后可重新发起。

## 启动配置

默认加载仓库内的 `artifacts/target-encoder-zh-v1`。可配置：

```bash
TARGET_ENCODER_ENABLED=true
TARGET_ENCODER_ARTIFACT_DIR=/absolute/path/to/target-encoder-zh-v1
```

`TARGET_ENCODER_ENABLED=false` 是能力级 kill switch：它只关闭 Encoder ACCEPT，
请求继续进入 structured provider，不关闭 DIRECT、Worker、Flow 或其他业务能力。
artifact 缺失、摘要不一致、Bundle 版本过期或 capability owner/effect 不一致时启动失败，
不会静默加载不兼容模型。

Structured provider 使用 `ModelRole.INTENT` 的模型配置。provider 超时返回 retryable
typed failure；无效 JSON、未知 goal 或凭空生成 entity 返回 non-retryable typed failure。
二者都不执行工具。

## 数据库和恢复

先运行 Alembic migration。LangGraph PostgreSQL checkpointer 只保存图执行位置；以下
权威数据必须保留在业务表中：

- Conversation event/state 与 CAS version；
- Pending interaction / approval 与 consumed signal；
- operation key、operation fingerprint 与 Action Receipt；
- selected Publication、Delivery outbox 与 Delivery Receipt。

写工具超时后保持 `RECONCILING`。客户端用新的 `request_id` 和原 `approval_id`
查询结果；Runtime 先按 operation key 查询权威状态，禁止重复提交写操作。

缺用户输入或写流程预检完成后，LangGraph 在当前 thread 上产生 native interrupt；
ConversationState 同时保存 PendingInteraction/PendingApproval 与 checkpoint thread ID。
恢复时必须先通过 DeterministicResolver 的 signal/version/CAS 校验，再以新验证的
WorkPlan 调用 `Command(resume=...)`。LangGraph checkpoint 不能自行授权审批或业务写入。

## 验证命令

本地 Target 核心：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_target_architecture_contracts.py \
  tests/test_conversation_state_resolution.py \
  tests/test_target_turn_planning.py \
  tests/test_target_orchestration_runtime.py \
  tests/test_write_workflow.py \
  tests/test_handoff_runtime.py \
  tests/test_target_architecture_e2e.py \
  tests/test_target_encoder_runtime.py \
  tests/test_target_architecture_eval.py
```

真实 PostgreSQL / HTTP：

```bash
TEST_DATABASE_URL=postgresql://... PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_target_http_postgres_e2e.py \
  tests/test_target_product_http_postgres_e2e.py \
  tests/test_target_persistence_and_manager.py
```

重新构建 Encoder 必须创建新的 artifact 目录，训练器拒绝覆盖现有产物：

```bash
PYTHONPATH=. .venv/bin/python scripts/build_target_encoder_dataset.py
PYTHONPATH=. .venv/bin/python scripts/train_target_encoder.py
```

## 评测与门禁

`TargetArchitectureEvaluator` 固定输出六层结果：`TRIGGER → ARTIFACT → CONSUMPTION →
STATE_SIDE_EFFECT → OUTCOME → COST`。每层保留 reason code 与 evidence ref，不产生一个
可掩盖失败的加权总分。

安全门禁由 capability 显式声明 `required_invariants`。缺少必需 observation 与明确失败
具有相同 fail-closed 结果。例如 `execute_refund` 缺少 duplicate-side-effect 证据时只
禁用退款写能力；订单查询与商品识别不受影响。

每个 Completed `/chat` 响应包含 `evaluation_trace`，记录：

- invocation 是否进入主链、deterministic resolution；
- plan ID、Registry fingerprint、RouteMode、Owner 与 understanding reason；
- WorkItem control mode、capability envelope 与 AgentResult status；
- state version、Workstream status 与 committed Receipt references；
- verifier outcome、缺失 Requirement、provider 是否被调用、WorkItem 数和延迟。

Trace 只发布结构化标识和状态，不包含 Prompt、工具原始输出、认证信息或用户秘密。

## 故障处置

| 现象 | 处置 |
|---|---|
| Encoder artifact 校验失败 | 启动 fail-closed；恢复匹配 Bundle 的已审核 artifact，或显式关闭 Encoder |
| Structured provider 暂时不可用 | 返回 retryable failure；不调用工具 |
| Agent/只读工具单点失败 | 保留独立成功结果；依赖项标记 BLOCKED |
| 写结果未知 | 保持 RECONCILING，按 operation key 查询，不盲重试 |
| Ticket 无 committed Receipt | 不转移 Owner，不宣称已创建人工工单 |
| 必需安全证据缺失 | 只禁用对应 capability，并保留 `missing:<capability>:<invariant>` |

## 当前能力边界

Encoder 数据是 synthetic prototype contract，不是生产流量。当前只有退款状态类别通过
门禁，总 heldout coverage 为 13%；General/Product 继续 DEFER。通用视觉观察需要
启用 VLM provider；Product Skill 当前闭合的是图片文字/OCR 型号与 Catalog 唯一匹配。
