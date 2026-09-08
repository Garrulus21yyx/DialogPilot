# Conversation Agent 动作接口收敛

基点 `d2287a2`；日期2026-09-08。本次减少模型协议负担，不重做路由或执行引擎，不宣布R01语义错误已经修复。

## 唯一主链

```text
Context / 确定性续接 / Encoder
    → 需要语义理解时：Conversation Agent 一次模型调用
        ├─ 原生动作调用（可以多个）
        └─ 普通回复候选
    → 动作批次整体转换、内部编译与 RoutePolicy
    → 原 TurnPlanCompiler / TaskGraph / Runtime
        ├─ 明确查询直接执行
        ├─ 开放任务由领域 Agent 调查
        └─ 原任务按已有状态恢复
    → 既有结果组织、核验、Publication
```

模型选择待执行请求，不在provider内执行工具。一个批次全部校验后才进入原业务链。
“我帮你查”等工具调用前文本不作为完成回复发布。没有新增第二个规划模型。

## 职责

| 位置 | 责任 |
|---|---|
| `application/conversation_actions.py` | 按支持能力和当前状态生成动作参数；将SDK已解析调用转换为内部计划 |
| `infrastructure/target_conversation_provider.py` | LangChain bind_tools、一次模型调用、原生消息、SDK callbacks和既有预算 |
| `application/conversation_agent.py` | 上下文、内部计划编译、参数来源检查；内部schema不再作为一个大模型工具暴露 |
| Policy / Compiler / TaskGraph | 权限、依赖、审批版本、执行顺序和恢复 |

减负具体体现在：

- 不再填写status＋goals多分支大对象；回复用文本，行动用相应参数。
- 没有待审批提案不暴露review_action；没有待补字段不暴露supply_input；只有resumable_work中的任务可恢复，active control本身不等于可恢复。
- 审批ID、订单/媒体原始值及来源、字段对应WorkItem由上下文绑定。多个实体/任务仍需选择，系统不猜用户指哪一个。
- 委派可同时携带订单、媒体和地址来源，不以objective中的文字替代结构化绑定。
- 简单调用不必填写任务编号；跨动作依赖才使用可选goal_id/depends_on。
- SDK工具已有能力描述，不再向模型重复旧goal descriptions和完整规划schema文案。

退款、发票、商品知识快捷动作保留各自owner，底层仍使用同一个知识工具。没有静默改归General或新增商品分类Skill。

## 混合输入与失败

- 确认加运费问题：review_action加知识动作，仍是一份整体计划。
- 部分补答加新问题：只填选中字段，新目标独立保留。
- 两任务同名字段：不同选择键绑定不同WorkItem，不广播答案。
- 多次补答调用可合并不相交字段；重复字段或重复审批整批拒绝。
- 修正不能批准旧参数；未知依赖、过期状态仍由compiler/Policy拒绝。
- 未知工具、无效参数、截断和空输出不当成功；标准JSON序列化拒绝NaN/Infinity。
- 普通文本不解析成旧JSON计划；最终文本质量仍由既有回复核验负责。

## 验证和回放

性质测试覆盖审批/缺信息/active/resumable的16种组合；混合审批矩阵同时经过内部合同与真实SDK模型替身→native actions→Manager。
订单/媒体/地址绑定、DAG、知识owner、历史日期、批次排列和失败无副作用另有断言。
HTTP Mock使用实际集成包验证工具参数、历史、callbacks、缓存usage及不同推理配置，不调用远程模型。

run_conversation_plan_replay仅调用当前provider，记录原输入、动作定义、SDK输出和编译结果。
删除旧文本规划provider及可切换运行模式。两份依赖v18提示/输出的冻结实验拒绝在v19静默重跑；复现历史使用记录的旧提交。旧gzip读取不是生产fallback。

扩大回归的三项失败已在干净d2287a2复现：内部目标列表测试漏continuation；参数长度测试漏必需query；DeterministicResolution中EntityBinding注解缺导入。修正断言/导入，没有放宽业务合同。
独立fresh-context审查发现委派来源遗漏、非有限数字和知识owner风险后，修正并再次复核无阻塞。独立暂存树检查525通过、7项依赖PostgreSQL跳过；CLI及diff检查通过。复现范围及交付状态见实施计划。

## 未关闭边界

1. 应查必查、用户条件保持仍需新鲜语义验收。本次新增付费模型调用0，不沿用112次失败选型声称新接口提高准确率。
2. 过滤参数来源涉及主/子Agent的共享工具入口。本次保留历史日期/DST语义，没有删日期而悄悄查当前政策；来源缺口仍开放。
3. 组件和状态检查不代表真实客服闭环或生产部署通过。

参考核对（2026-09-08）：[LangChain模型工具调用](https://docs.langchain.com/oss/python/langchain/models#tool-calling)区分模型返回调用请求与应用执行请求。这里复用SDK解析、原TaskGraph执行，不再写模型—工具循环。
