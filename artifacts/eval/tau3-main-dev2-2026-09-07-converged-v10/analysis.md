# 收敛后原两条 τ³ 开发任务：未闭环

运行代码：444f99de1c6a604962a1e8c09e1c1cd6acbe61ef。
官方代码：a2c024725189473d2d7cea3a5cfdbcc67478e41f；train 任务 0、1；seed 300。
模型 deepseek-v4-flash；completion override 4096；用户 512；80 steps，与前轮相同。
运行期间未修改生产代码、评分、模拟用户、超时或预算。

## 原始结果

| 运行 | 任务 | 结果 | 官方评分 | 业务工具调用 |
|---|---|---|---|---|
| v10 | 0 | 模拟用户首条消息为空，ValueError | null，未评分 | 0，未进入应用 |
| v10 | 1 | 追问被覆盖校验拒绝，桥接等待超时 Empty | null，未评分 | 0 |
| 唯一环境重试 | 0 | 追问被覆盖校验拒绝，桥接等待超时 Empty | null，未评分 | 0 |

task 0 的环境重试独立保存在 ../tau3-main-dev2-2026-09-07-converged-v10-environment-retry-task0/。
不替换首次 ERROR，不再追加重试，不将 null 写成业务 0 分或成功率。
两个已进入应用的轨迹都仅有默认问候和第一条用户请求，无官方 business tool call。
评测脚本已清理其创建的独立临时数据库；轨迹和核验记录保留。

## 观察与因果链

1. 主对话作者生成自然的身份核验问题，要求账户邮箱。
2. 两条任务的语义核验都给出 supported=true、answered=true、issues=[]。
3. 应用覆盖校验返回 incomplete：Explain unresolved task outcomes: work:1:semantic-1。
4. 现有一次修订返回相同追问，仍缺同一个内部 outcome 标记；问题没有发布。
5. 未核验的交互被统一标记可重试，Run 被释放回队列；评测桥只 pump_once 然后等待，
   用户得不到问题或具体失败，最后 queue.Empty。不是官方对换货结果打了 0 分。

权威证据是 task-1.json 及重试 task-0.json 的 target_trace、两份 partial-trajectory；
不是根据任务预期结果推断应该执行换货。

## 不调用真实模型的复现

使用相同 NEEDS_USER_INPUT 结果、同一个绑定 email 字段、相同公开文字，
调用生产 ResponseAssembler / AnswerVerifier，语义模型由既有 SDK stub 提供相同通过判断：

| 公开文字 | 选择的支持 | 实际结果 |
|---|---|---|
| Please provide your account email. | INPUT_REQUEST | verified=false / INTERACTION_UNAVAILABLE |
| 同上 | INPUT_REQUEST + 同任务 WORK_ITEM_OUTCOME | verified=true / ANSWER_SUPPORT_CHECKED |

共享根因在 application/response_assembly.py 的 _verify_support：
待补字段和 NEEDS_USER_INPUT 的任务状态被独立要求，缺少“已问全绑定字段即可表达等待输入”的覆盖关系。
不是金额、商品类别、工具权限或退款规则问题。

现有测试把所有 allowed_claims 全选，部分直接使用强制 PASS 的 verifier，
因此证明了理想归因格式的接线，没有覆盖真实模型合法选择较少支持的情形。
1093 项回归通过不能替代本轮真实任务验收。

## 后续修复范围（本轮尚未修改）

- 覆盖 Owner：明确 INPUT_REQUEST、任务 outcome、事实、Receipt 的表达关系。
  完整绑定追问应覆盖对应等待输入状态；独立失败、部分成功和业务事实仍需各自证据，不能一概豁免。
- 错误 Owner：区分暂时服务故障与核验/修订耗尽，不因存在 checkpoint 就一律可重试。
- 评测适配：将应用确定的失败投递为明确评测错误；不靠加长等待或无限 pump 隐藏问题。
- 验收使用支持集合的组合测试及生产核验器，不再仅以“全选支持”的测试作为闭合证据。

状态：input_coverage_contract_open / end_to_end_quality_open。
暂停依赖成功闭环的意图案例检索与工具治理成绩实验。保留整个相关合同修复范围，
不改商品、邮箱、换货任务专用生产分支，不在本报告声称修复完成。
