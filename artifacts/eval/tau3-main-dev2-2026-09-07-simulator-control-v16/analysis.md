# v16：真实初始化边界失败，评分不可用

固定 a21568c 两条任务均 ERROR/null；初始化 UserSimulator 时发生
`deepcopy(llm_args) -> cannot pickle '_thread.RLock' object`。
未发生模型调用或业务写入。不是之前 thinking 参数过滤再次失败。

根因：把带锁的诊断 callback 运行时对象放入了模型配置值对象。
此前单调用测试绕过了官方 UserSimulator 初始化，验收边界不完整。
修复在诊断 owner：使用 LiteLLM 官方 callback registry 管理生命周期；
llm_args 只携带可复制关联 metadata。没有 __deepcopy__ 特例或初始化后补塞参数。

新验收直接创建官方 UserSimulator、设置 seed、生成一次用户消息并检查捕获。
v16 原记录保留；修复版本另开 v17，不覆盖本轮失败，也不报告业务得分。
