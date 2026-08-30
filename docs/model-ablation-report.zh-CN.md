# DeepSeek 三档模型消融报告（2026-08-30）

这页回答一个具体问题：在 DialogPilot 当前 15s 单 Agent、20s 请求预算下，开启 reasoning 或升级 Pro 是否真的改善意图、路由和 Agent 执行？

## 1. 实验合同

- 数据：`dialogpilot-layered-core` `1.0.0-seed`，checksum `3dd911...b53c2e`。
- 每档 15 条：意图 dev 5 + heldout 3，routing/Agent dev 5 + heldout 2。
- 每档仅运行 1 次；这是工程 pilot，不是具有置信区间的 benchmark。
- 三档对照：Flash/off → Flash/high 用于隔离 reasoning；Flash/high → Pro/high 用于隔离模型规模。
- 所有角色同时切换同一 profile，业务数据、Prompt、Skill、超时和 Docker 数据卷不变。
- routing 使用数据集提供的 gold intent，单独验证 Planner、TaskPlan 和执行；Intent Accuracy 在另一层测量。
- DeepSeek 当前价格来自[官方 Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)，2026-08-16 16:00 UTC 后区分峰谷；Token 以[官方 usage 返回](https://api-docs.deepseek.com/quick_start/token_usage)为准。
- 这 15 条会触发 Intent、Worker/ReAct，并在 fan-out 完成时触发 Synthesis；它不是 Verifier、Judge、Memory、rewrite、rerank 的质量基准。“完整”指本页承诺的 15 条 intent/routing 合同和计量列完整，不代表九个角色都已覆盖。

复现命令：

```bash
PYTHONPATH=. .venv/bin/python scripts/run_model_ablation.py \
  --compose-file docker-compose.yml \
  --compose-file /tmp/dialogpilot-compose-test.yml \
  --base-url http://127.0.0.1:18000 \
  --output /tmp/dialogpilot-model-ablation.json
```

runner 不修改 `.env`，每档只重建同一个 app 容器，结束后恢复原来的分层矩阵；每完成一档立即写 checkpoint。

## 2. 结果

| 指标 | Flash / off | Flash / high | Pro / high |
|---|---:|---:|---:|
| Intent Accuracy | **8/8（100%）** | 7/8（87.5%） | 7/8（87.5%） |
| Owner Exact | 7/7 | 7/7 | 7/7 |
| Task Exact | 6/6 | 6/6 | 6/6 |
| Task success | **8/8（100%）** | 7/8（87.5%） | 3/8（37.5%） |
| ReAct loop completed / entered | 7/7 | 6/6 | 2/2 |
| 工具调用次数 | 13 | 14 | 4 |
| Case P50 | **1.43s** | 1.79s | 5.61s |
| Case P95 | **13.19s** | 16.50s | 15.00s* |
| 模型调用 P95 | **7.40s** | 8.86s | 10.62s |
| Input Token | 10,144 | 17,163 | 13,016* |
| Output Token | 6,292 | 8,153 | 5,134* |
| Cache-hit Input Token | 21,120 | 14,464 | 6,656* |
| Thinking calls | 0 | 22 | 18 |
| Reasoning Token | 未单列 | 未单列 | 未单列 |
| 估算成本（谷时） | **$0.00653** | ≥ $0.00926 | ≥ $0.01890 |
| 估算成本（峰时） | **$0.01306** | ≥ $0.01852 | ≥ $0.03780 |

`*`：Pro/high 多次在 15s Agent timeout 被取消，所以 case P95 被超时上限截断；被取消的供应商调用没有返回 usage，Token 和成本是下界，不是完整账单。P95 看似低于 Flash/high，不代表更快。

## 3. 两个可归因差分

### Flash/off → Flash/high：reasoning 的影响

- Intent Accuracy：-12.5 个百分点。
- Task success：-12.5 个百分点。
- Case P50：+25.4%；P95：+25.1%。
- Input Token：+69.2%；Output Token：+29.6%。
- 峰时成本：至少 +41.7%。
- 失败：OOS 天气问题从 `other` 误判为 `query`；复合技术+账务请求中 Technical task 超过 15s。

在这批样本和预算下，reasoning 没有质量增益，反而增加延迟、Token 和一次任务超时。

### Flash/high → Pro/high：模型规模的影响

- Intent Accuracy：持平 87.5%。
- Task success：87.5% → 37.5%，下降 50 个百分点。
- Case P50：+213%；模型调用 P95：+19.9%。
- 峰时已返回 usage 的成本下界：+104.2%。
- 四条路由执行失败：复合登录+扣款、否定扣款、技术闪退、安全+异常扣款，均由 15s Agent timeout 导致。

Pro/high 的 Output Token 反而较少，是更多调用被超时取消造成的幸存者偏差，不能解释成更省 Token。

## 4. 为什么 Owner Exact 都是 100%，执行却失败？

Owner Exact 验证的是 Planner 生成的 `TaskPlan`：谁应该负责。Task success 验证的是 Worker 在时间预算内是否完成。Pro/high 能正确生成 Technical + Billing 的计划，但 Technical Worker 15s 超时，因此出现：

```text
Owner Exact = 1
Task Exact  = 1
Coverage    = 0
Task success = 0
```

这正是分层评测的价值：如果只看路由集合，会把“分对人但没有做完”误报成成功。

## 5. 工具指标为什么不是 Tool Exact？

现有 7 条 routing seed 标注了期望 Owner 与 Task，但没有标注每条应调用的具体工具。因此这里能证明的是：进入 ReAct 的工具循环是否以 `completed` 结束、调用了多少次工具；不能证明工具选择完全正确。

另外，Pro/high 的 `2/2 loop completed` 存在幸存者偏差：5 个任务在进入完成态前就超时。面试时必须同时报 `task success 3/8` 和 `entered loops 2`，不能只说工具完成率 100%。

## 6. 当前工程决策

这轮结果支持高频闭合角色继续使用 Flash/off。它没有证明“Flash 永远优于 Pro”，只证明在当前客服 seed、Prompt、工具、15s/20s 预算下，全局开启 high 或全局升级 Pro 不划算。

当前生产默认仍采用按角色分层：Intent/Worker/ReAct/Memory/rewrite/rerank 为 Flash/off，Synthesis/Verifier/Judge 为 Pro/off。后续若要启用 reasoning，应在 human-reviewed gold 上按角色单独消融，而不是全局打开。

可机读摘要：[model-ablation-2026-08-30.summary.json](./data/model-ablation-2026-08-30.summary.json)。
