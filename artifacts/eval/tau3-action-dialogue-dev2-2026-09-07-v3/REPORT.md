# 重复确认与完成状态：第三次整体验证

2026-09-07，运行提交 d8458f8。固定原 train 任务 2/3，seed 300，每个版本各运行一次；不是 heldout 成绩。v1/v2 的失败与中间结果均保留，不择优抹去失败。完整环境和源码指纹见 manifest.json。

## 当前结果

| 检查 | 任务 2：三件商品退货 | 任务 3：待发货 T-shirt 改款 |
|---|---|---|
| 普通补信息 | 身份、退款方式 | 身份、修改范围 |
| 正式 APPROVAL | 1 次 | 1 次 |
| 实际写工具 | return_delivered_order_items，1 次 | modify_pending_order_items，1 次 |
| 官方 ENV 严格重放 | 1 | 1 |
| 最终业务状态 | return requested | pending (item modified) |
| 内部结果 | SUCCEEDED / SUCCEEDED | SUCCEEDED / SUCCEEDED |
| task_completed | true | true |
| 最终公开表达 | 已提交退货，与凭证一致 | 已改款并补收 $0.05，与凭证一致 |

两条均有 2 个 FIELDS、1 个 APPROVAL。这里不能只数枚举：v2 同样只有一个正式 APPROVAL，却在 FIELDS 中提前问过执行许可。逐句复核 v3：退款方式选择、修改范围确认均未再要求授权执行；随后才呈现准备好的动作并正式审批。两条也都回答了 10 个可用 T-shirt 选项。此为本次轨迹检查，不替代官方自然语言评分。

## 修复的共同边界

1. Conversation planning 的能力卡显示 Registry 中实际存在的动作；开放目标明确选择是否允许准备动作。查询目标不会因同领域而取得办理目标的动作入口。这不是授予执行批准。
2. 续接恢复原任务的明确工具／Skill／Action 范围，经 owner、Registry 与目标 revision 校验，不通过“有没有 Action”猜测工具范围。
3. 原 ToolMessage 在 SDK 工作上下文中与已消费回复、匹配的 COMMITTED 凭证关联；原归档不变。未完成的其他目标仍继续，不把所有 BLOCKED 强改成成功。
4. 补信息与审批的不同用途进入已有领域工具描述、公开回复和核验调用。Question hint 不拥有公开问句；真正缺失的选择必须保留，已明确目标不重复征求执行许可。没有增加第二条 Agent 循环或新审核模型。

## 证据及验证边界

- PostgreSQL 范围回归 215 项通过，含连续两次补信息、批准／拒绝／修正、恢复与进程退出；后续回复／核验范围 145 项通过。两套有重叠，不相加宣传。
- 独立审查发现并帮助验证了只读续接丢非 Skill 工具、纯许可追问不能静默消失的边界。
- 独立轨迹复核确认本轮未重复请求执行授权，但不证明每个澄清都必要或最简。任务 2 的正式审批未列退款总额，因此也不宣称全部支付条款质量已验收。
- 五条固定组件反例及所有中间模型输出在 action-dialogue-adversarial-2026-09-07-*。其中对正式审批文案仍有误拒：模型说缺少的条款实际上已在文本中。不能宣称语义核验全部通过或永不重复追问。
- 纯许可型无效 FIELDS 会被拒绝，但原等待状态不会自动变成合法审批；现有回复修订不能完成这种交互修复。该更广的恢复边界仍未关闭，不能用本次两条成功掩盖。
- ACTION 辅助评分仍为 0；不为参考调用轨迹补无意义查询。ALL 官方总分仍为 null，原因是默认官方裁判缺少 OpenAI 凭证；不是业务得分 0。ENV=1 与官方总分分开报告。
- HTTP/SSE、多领域路由和真实外部写接口的原子性未由这些开发任务验证。无全局关闭声明。

云端会话：

- https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/sessions/tau3-b639f76c4e734a94a2c40c60923d0b54
- https://cloud.langfuse.com/project/cmtqihxgw0yo1ad0ckfdxwslm/sessions/tau3-5ef9d744d2504c29b43eb963bfb30f42

状态：这两条原始故障已通过修复后的整体验证；更广的交互语义与恢复收敛仍为部分验证，不标为完全闭环。
