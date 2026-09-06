# 编号类型边界迁移与混合入口验证

## 修复的因果边界

旧 EntityBindingResolver 对当前消息、历史和摘要的全部正则编号赋予 order_id 字段。编号出现这一观察被过早赋予了业务类型。新 resolver 统一产出 reference；来源、值和出现序号保留。UNIQUE 只表示同优先级候选唯一，不证明它是订单、商品或用户拥有的业务记录。

ConversationAgent 对 reference 必须同时提供准确 value 和 source_ref，才能将其选为 order_id/asset_id。该应用编译边界保存 `conversation-agent-reference-selection-v1` 类型选择依据；原值与来源不变。省略类型选择时不自动使用唯一 reference。结构化输入、明确字段的补输入和工作流声明槽位保留已有类型合同。类型选择只是参数解释，订单工具仍通过 tenant/user/order 验证实际归属。

模型 prompt 与 request-v2 明确 reference 的未分类语义。encoder 因缺少 typed 参数绑定退回 ConversationAgent；结构化 order_id 的已支持快速路径仍可使用。生产 planner 继续文本 JSON，未切换模型或推广前面的结构化输出实验。

## 持久化与兼容

- PG codec 保存 type_selection；工作流继承保留槽位已有依据；状态及工作项指纹覆盖该字段。
- 新 EntityBinding 构造拒绝缺少类型选择依据的 CURRENT_MESSAGE/RECENT_MESSAGE/SUMMARY typed order_id/asset_id。旧原始文本绑定必须重新解释，不能补一个标记伪装成已完成选择。
- JsonPlus 可吞掉构造失败变 None，故 Target checkpoint 在 msgpack 正常解码前只扫描结构并校验 EntityBinding。非法嵌套绑定抛 TargetCheckpointContractError。
- 旧 Target constructor JSON 明确不支持自动迁移；识别 dotted 与拆分 module id 并拒绝。普通 JSON 数据保持可读。
- 已声明的结构化/工作流槽位类型仍由其现有 owner 负责，不因此获得业务归属认证。旧含文本 typed binding 的活跃快照会拒绝恢复；不得把本次变更描述成无感兼容。指纹变化也要求按新合同重新准备相关执行状态。

历史评测文件不修改。回放可显式使用 `--unclassify-legacy-references` 将旧文本候选改为 reference，manifest 记录输入迁移；混合来源或需合并的历史分组要求重新捕获，不能声称完全复原旧系统。

## 验证

最终本地相关测试 133 项通过；实际 PG 执行/恢复测试 9 项通过。独立最终限定复查 37 项通过，并确认先前指出的工作流继承、指纹、msgpack 静默 None 和 JSON 身份归一化缺口已处理。不同前缀与上下文的组合测试覆盖：观察不赋型、原值/来源选择、范围不越权、快路径退让、codec/checkpoint 与旧绑定拒绝。

独立审查结论只限类型与恢复合同，不代表查询语义或整个 RAG 闭合。

## 六条实际混合入口

复用已消费的六条合成问题，实际统一 ConversationAgent、订单工具、知识检索、工具输出、回答合成和 Publication；19 篇合成政策和三个模拟订单。Flash/low/4096 为评测配置，非默认变更。共 22 API 调用：5/6 同时覆盖订单与政策工具，5/6 预期来源可见、引用并通过知识支持检查。

- 拆封耳机：直接给出非质量原因不适用规则。
- 赠品：保留已使用条件，说明需核实活动规则，不能直接承诺全额退款。
- B20：查询保留机器型号未确认，答案要求先核对铭牌而非试装。
- 预售：区分付尾款与已发货。
- 省略追问：保留耳机、拆封、非质量条件，回答包含相应规则。
- 质量审核：仍只查询订单和退款状态，后者失败；没有获取审核与到账的政策证据。

部分查询仍改用英文；部分答案过度解释“订单快照”和签收数据语义。B20 的 applicable_product 字符串来源仍需独立审查，提及型号不必然等于知识库的 scope ID。来源可见与 verifier PASS 不是最终准确率。

本轮包含此前已提交的业务字段语义与 goal 描述变化，且只有一次六条开发运行，不能将收益单独归因于 reference 迁移。无 HTTP/持久化 worker/业务写入/跨会话记忆压力验收。类型修复与查询语义收益分开报告。

输入：`artifacts/eval/rag-reference-mixed6-input-2026-09-06/cases.json`。完整捕获、summary、manifest、completion：`artifacts/eval/rag-reference-mixed6-2026-09-06/`。summary 固定输入和捕获 hash。下一步仍需解决审核类混合目标覆盖、模型范围过滤依据、答案冗余和部分目标执行，并在更广数据上验证。
