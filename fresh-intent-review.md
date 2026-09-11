# DialogPilot fresh intent candidate — second independent review

## 结论

**第二轮复核通过。** 在本任务允许的审计范围内，`/home/yang/DialogPilot/fresh-intent-candidate.jsonl` 满足数量、结构、slice 分布、标签合同、冲突语义、paraphrase family、多样性与自然度要求，可以进入封存元数据更新阶段。

建议将 `review.status` 从 `synthetic_pending_human_review` 更新为 **`independently_reviewed_synthetic`**。本次复核由未参与生成的独立模型 Reviewer 完成，因此不应使用 `human_reviewed`。状态、reviewer 和 tags 等封存元数据更新后，应对最终文件重新做结构检查，再计算并固定 SHA-256；当前待更新版本的哈希不应作为最终封存哈希。

本次没有运行任何意图识别模型，也没有读取或搜索仓库内的既有测试、Prompt、few-shot、关键词表、模型预测、错误报告、历史 Bad Case 或其他生成者样本。候选 JSONL 未由 Reviewer 修改。

## 第一轮问题的复核结果

- **fresh-intent-084、089、094、099：已通过。** 四条 `code_switch` 均已改为可理解且自然的中英混合表达，不再是全英文。
- **fresh-intent-069：已通过。** `logistics` 已从 secondary_intents 移除；已知到达时间被正确保留为背景，当前主意图为明确要求人工接管，`billing` 是被接管处理的次意图。
- **fresh-intent-053：已通过。** 否定本人支付失败、肯定他人使用账户的表达现在自然清楚，`account_security` 唯一成立。
- **原跨 slice 近重复风险：已通过。** 014/087、016/077、010/099、008/096 已通过交易介质、场景、目标、措辞或中英结构的实质变化拉开；它们仍共享业务类别，但不再属于机械同义替换或近重复消息。

## 全量结构与分布检查

- 文件为合法 JSONL，共恰好 100 行；每行均可独立解析为一个完整对象。
- ID 从 `fresh-intent-001` 连续至 `fresh-intent-100`；100 个 ID 唯一，100 个 message 唯一。
- 四个 slice 各 25 条：business_boundary 25、rejection 25、conflict 25、semantic_similarity 25。
- Slice A 分布准确：account 3、account_security 4、logistics 3、payment_issue 4、refund 4、technical 3、technical_login 4。
- Slice A 中英文/中英混合不少于 5 条，口语或省略表达不少于 8 条，不出现标签对应直接关键词的间接表达不少于 10 条。
- Slice B 准确包含 15 条 other 与 10 条 in-scope。15 条 other 分为明显越域 5、业务词汇相近但非本项目 5、语义不足 5；10 条 in-scope 覆盖“怎么、什么、多久、状态”等正常问句形式。
- Slice C 子类型准确：negation 6、correction 5、quotation 4、multi_intent 6、contextual 4。
- Slice D 为 5 个 family × 5 条；每个 family 均完整包含 direct、implicit、colloquial、code_switch、noisy，group_id 正确且同 family 语义一致、词面差异明显。
- 非 contextual 样本 history 均为空；4 条 contextual 均含 1–3 条格式正确的 history，结合上下文后标签唯一。
- 所有 primary 和 secondary intent 均属于闭合枚举；primary 不在自己的 secondary_intents 中；所有 other 的 secondary_intents 均为空。
- schema_version、layer、slice、generation_contract、source、review 与 tags 的结构和值在待审版本中一致、合法。待审版本的 review.status 仍全部为 `synthetic_pending_human_review`，没有提前宣称通过。

## 标签与冲突语义检查

- Slice A 的关键边界清楚：非本人交易与异常设备访问归 account_security；本人支付失败、重复扣款和本人交易手续费归 payment_issue；PIN、验证码、进入账户和身份认证归 technical_login；卡片、虚拟卡、感应支付及应用功能不可用但不涉及登录或崩溃的归 technical；在途位置和到达时间归 logistics；取消购买和退回款项归 refund。
- Slice B 的 15 条 other 均没有因问句形式或银行、物流、购物等邻近词汇被错误吸入项目内意图。10 条项目内问题均优先采用了可用的细粒度标签。
- 6 条 negation 均未把否定词附近的意图当成当前肯定意图。
- 5 条 correction 均以转折或自我纠正后的最终诉求为 primary。
- 4 条 quotation 均把客服、页面、朋友或系统的说法视为背景，没有把引用自动当成用户当前意图。
- 6 条 multi_intent 均有可由“更急”“先”“最担心”“现在要”等优先级或明确人工接管规则支持的唯一 primary；secondary_intents 是当前仍成立的次诉求或被接管的业务主题，没有重复 primary。
- 4 条 contextual 的当前消息单看具有适度歧义，结合 history 后分别唯一落到 account_security、technical_login、logistics 和 human_handoff。
- 没有发现当前消息与 expected intent 明显不一致、需要主观猜测或无法仲裁的样本。

## 重复、泄漏、自然度与隐私检查

- 没有完全重复 message；第二轮未发现应阻断的跨 slice 近重复。Slice D family 内的语义相似属于规定设计，五种变体在表达结构和词面上有实质区别。
- input.message 中没有“这个意图是……”“标签是……”等元语言，没有内部 intent 枚举名、case 编号或测试说明泄漏。
- review.notes 包含标签边界解释是输出合同的明确要求；该字段必须继续在运行识别器时排除。
- 样本整体符合自然客服输入风格。英文和中英混合表达可理解；noisy 变体仅有轻微错字或空格，不影响唯一判定。
- 未发现电话号码、邮箱、银行卡号、身份证号或真实个人信息。`E502` 是明确系统错误码，符合 technical_crash 合同，不属于个人信息。

## 与既有模板相似度的审计边界

任务同时明确禁止独立 Reviewer 访问既有测试、Prompt、few-shot、关键词表和其他生成样本，因此无法对候选与隐藏的“现有模板”做直接比对，也不能声称完成了基于模板语料库的相似度证明。本轮在允许范围内完成了候选集内部的精确重复、近重复、槽位化表达和机械同义替换检查，未发现阻断问题。

这一限制不阻止本轮独立复核通过，但最终封存说明应保留该审计边界，避免把“未访问模板”表述成“已证明与模板不相似”。
