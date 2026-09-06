# τ³ retail 主链接入与两条开发任务 smoke

## 合同与范围

运行真实 TargetChatApplication，不替换成官方示例 Agent。官方环境拥有业务状态，官方模拟器拥有用户行为，官方 evaluator 拥有 reward。适配不得读取任务答案来生成 Agent 输入。只选 train 前两条、一次运行；不据此报告准确率。

现有主链固定了目标词表、Registry 和写 Flow；τ³ retail 的身份查找、商品交换、支付修改并非现成兼容能力。先实现消息/工具协议桥并保留不支持行为，不为任务新增规则或绕开写权限。受限接入结果必须与完整 benchmark 成绩区分。

## 步骤

- done：核对官方协议、生产装配与环境注入边界。
- done：实现入口消息适配、隔离依赖和最小合同测试（13 passed）；不是完整工具桥。
- done：运行 train 0/1 的第一轮真实应用入口，保存原始输出；均 CLARIFY，无业务执行。
- blocked：两条完整任务及官方评分。缺政策/业务主体/工具语义绑定与换货能力，不能以消息适配替代。
- done：报告已验证范围、能力缺口和复现命令。

## 产物与范围修订

`evaluation/tau3_adapter.py`、`scripts/run_tau3_entry_smoke.py`、`tests/test_tau3_adapter.py`、`docs/tau3-retail-entry-smoke-2026-09-06.zh-CN.md` 与 `artifacts/eval/tau3-retail-entry-smoke-2026-09-06/`。

本次实测是入口诊断，不是完整 τ³ 接入完成。生产目标词表和 Registry 没有可直接注入官方 retail 的语义入口；后续改变的是环境装配与支持的业务合同，不能靠试题特化或不受控写调用补齐。官方 reward 为 null。

## 非目标

不改用户现有未提交文件；不迁移生产目标词表；不另写 Agent 循环；不创建模拟订单副本；不将不兼容写操作包装成只读工具。
