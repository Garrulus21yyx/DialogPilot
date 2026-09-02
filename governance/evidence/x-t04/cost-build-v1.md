# X-T04 cost budget build evidence v1

日期：2026-09-02。状态：`IMPLEMENTED / ROUTE FLAG-OFF / PRODUCTION BILLING REVIEW PENDING`。

本证据证明目标 RoutePath 与 Knowledge ingest 的成本合同已实现；当前 production `/chat` 仍是 legacy
publisher，新 RoutePath 仅 evaluation/dark-shadow，不能据此声称生产成本 Gate 已通过。

## 正向合同

- `RouteCostBudgetRegistry` 对八种 RouteMode 各有且只有一个版本化预算，分别限制 model call、tool call、
  retrieval call、provider input/output token 总额；上限由 M2 route component algebra、Worker ReAct=4 steps、
  executed workers≤3、conditional synthesis/verifier 推导。
- 超过调用次数会在下一次 provider/tool/retrieval side effect 前抛出 typed
  `ROUTE_COST_BUDGET_EXHAUSTED`；provider 官方 token usage 在响应后记账，若越界则停止后续昂贵路径并返回
  route-owned `rule_clarify|abstain|handoff`，不自动选择更昂贵模型或额外检索。
- 每个 RoutePath 返回 budget policy/version、实际资源计数与 provider usage；Trace 记录低内容 route/call/token/
  exhausted dimension。provider response ID 保留在 request-local usage artifact，用于账单抽样关联。
- offline Knowledge ingest 使用独立 `offline-knowledge-ingest-budget-v1`，限制 batch source 数、单 source/总
  bytes、总 chunks 与估算 embedding tokens；所有检查在 Chroma/Sparse persistence 前完成。在线 Route 额度
  与离线 ingest 额度没有共享 counter 或借用路径。
- `reconcile_provider_usage()` 对 captured 与 billed calls/input/output tokens 做精确 delta；release manifest 要求
  reconciliation rate=`1.0`、budget regression=`0`、silent expensive fallback=`0`。

## 冻结产物

| 产物 | 标识 |
|---|---|
| Online policy | `route-cost-budget-v1` |
| Offline policy | `offline-knowledge-ingest-budget-v1` |
| Cost release manifest | `evaluation/gates/x-t04-cost/v1.json` |
| Manifest SHA-256 | `460906533e3b4ce503e4f63e4625c6dd4092f915c5cdab8e13bb4a5668eeae2b` |
| Embedded policy SHA-256 | `db81645a1ad3f99d62db01447aaa00f4725ad59181a63eaf0bfe2e0d53933241` |

Manifest 由 `scripts/create_x_t04_cost_manifest.py` 可重复生成；测试会拒绝代码 policy 与 frozen gate projection
漂移。

## 可复现验证

```text
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_cost_budget.py tests/test_llm_metrics.py tests/test_chat_application.py \
  tests/test_knowledge_base_retrieval.py tests/test_tool_security_trace.py \
  tests/test_route_path_application.py tests/test_route_execution.py \
  tests/test_knowledge_ingestion_api.py
→ 99 passed in 1.83s

TEST_DATABASE_URL=postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot \
PYTHONPATH=. .venv/bin/pytest -q
→ 806 passed in 19.02s
```

验证包括全部 RouteMode 闭合、调用前阻断、provider token 越界 typed fallback、tool handler 不执行、offline
batch 在 persistence 前原子拒绝、provider request ID/usage 采集、reconciliation delta 与 frozen manifest 重建。

## 尚未 VERIFIED 的部署证据

- production provider invoice/export 的真实抽样数据尚不可用，因此当前只验证 reconciliation 算法与合成
  sample，不宣称账单已经对齐；
- legacy production `/chat` 尚未切 RoutePath；启用必须经过 M2 Exit 与 bounded canary；
- Chroma 默认 embedding adapter 不暴露官方 billable token，本地 ingest 使用统一 estimator 做 hard cap；若
  换成计费 embedding provider，必须接官方 usage 并新增 billing sample；
- 尚无独立 Platform/Application reviewer signature、生产 cost alert 或 canary cost regression report。

因此 X-T04 build 可以作为 prerequisite 的 `IMPLEMENTED` 证据，但 M2 Exit 的 production billing/canary
evidence 仍保持阻断。
