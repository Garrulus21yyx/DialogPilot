# τ³ retail 两条入口探测（2026-09-06）

## 结果与边界

已运行官方 retail `train` 的前两条任务（0、1）的**第一轮用户输入**，不是两个完整任务。官方 UserSimulator 生成输入，真实 `build_target_runtime → TargetChatApplication.handle → ConversationAgent → RoutePolicy/Compiler → TurnRuntime → PostgreSQL Publication` 处理。

| 任务 | 官方模拟用户输入概要 | 实际结果 |
|---|---|---|
| 0 | 给出 W2378156，希望更换键盘和温控器的版本 | CLARIFY；missing `customer_service_goal`；未派发业务任务 |
| 1 | 描述键盘轴体、温控器平台换货要求，未给订单号 | CLARIFY；missing `order_id`；未派发业务任务 |

两条均提交了 Publication，文本都是“请补充完成该任务所需的信息。”`Completed` 表示本轮应用调用返回，不表示换货完成。任务 1 还触发了一次 `service_episode_search`，在本次未绑定该工具的环境中被拒绝；不能据此判断生产 Memory 服务故障。

**官方 reward / pass^1 / 准确率均未计算。**本次没有完整用户对话、官方业务工具绑定、官方政策注入及 scorer 调用，不能写成“τ³ 两题通过”或“任务成功率 0%”。

## 为什么没有继续硬跑

- 当前 `ConversationAgent` 的规划词表由项目固定业务目标构成，不含换货；这两条开发任务都要求换货。
- 默认 Registry 的工具与官方 retail 工具没有同名交集。名称差异不是不兼容的充分证明，但当前确实没有已验证的参数、身份、状态和 Receipt 转换。
- 项目订单查询要求已认证主体；官方零售会话先通过邮箱或姓名/邮编查找用户身份。不能从评测任务的隐藏 scenario 中直接取 user_id 注入项目。
- 官方换货、退货、订单修改与项目既有退款 Flow 并不等价，不能仅重命名函数或把写工具标记成 READ。

因此本轮只验证入口。继续获得可评分的完整任务，需要在环境装配与业务能力定义边界支持官方 retail 合同；这不是再加一个消息适配函数就能完成的事情。没有更改生产规划词表或绕过 Flow 来制造成绩。

## 实现与验证

- `evaluation/tau3_adapter.py`：将模拟用户公开文本转为 ChatCommand；保留类型化输出，不把非 Completed 状态伪装成客服答复；工具词表交集只作诊断。
- `scripts/run_tau3_entry_smoke.py`：官方用户模拟器、现有完整 Target 装配、隔离 PostgreSQL/Redis、逐条输出；不连接生产业务数据。
- `tests/test_tau3_adapter.py` 与原 `test_chat_application_runner.py`：13 passed。
- 原始结果：`artifacts/eval/tau3-retail-entry-smoke-2026-09-06/`，含 manifest、两条结果、report；manifest 记录源码 hash 和 τ³ checkout commit。
- 本次关闭中文 Encoder，使用已配置模型；不修改项目默认模型策略。
- 临时 PostgreSQL 数据库已删除，临时 Redis 已停止；结果保存在上述文件。生产数据库、现有未提交改动未被修改。

## 复现

先在独立 venv 安装指定 τ³ checkout，不修改项目 requirements；Python 3.13 还需要 `audioop-lts`。不要继承全局 site-packages，全局标准 MCP 包会遮蔽本项目的 `mcp/` 命名空间。

```bash
uv venv /tmp/dialogpilot-tau3-eval --python .venv/bin/python
uv pip install --python /tmp/dialogpilot-tau3-eval/bin/python /path/to/pinned/tau2-bench audioop-lts
PYTHONPATH="$PWD/.venv/lib/python3.13/site-packages" TARGET_ENCODER_ENABLED=false \
  /tmp/dialogpilot-tau3-eval/bin/python scripts/run_tau3_entry_smoke.py \
  --tau-source /path/to/pinned/tau2-bench \
  --output artifacts/eval/tau3-retail-entry-smoke-new
```

使用 `TEST_DATABASE_URL` 提供可创建独立数据库的本地测试 PostgreSQL 连接；脚本仅删除它自己新建的数据库。模型凭据读取现有 `.env`，不写入报告。每次运行使用新输出目录，不覆盖旧轨迹。以上是入口诊断命令，不是官方 benchmark 命令。

## 后续实施合同（尚未完成）

1. 环境装配显式接收 policy、工具合同与业务主体解析能力；规划能力描述与实际 Registry 保持一致，不新增另一套 Agent。
2. 官方 user identity、订单/商品读取与受控写操作分别在对应 owner 接入；没有语义等价的 Flow 必须作为能力缺口，不伪装为已有退款。
3. 工具请求和结果必须进入官方 trajectory，以便官方 evaluator 重放并检查最终环境；不能只私下改官方 DB。
4. 原两条开发任务运行到官方终态，保留全部轨迹再调用官方 evaluator。届时才可说“跑了两条完整 τ³ 任务”。
