---
layout: default
title: DialogPilot 客服 Gold 数据集生成与审核 Prompt
permalink: /dialogpilot-gold-authoring-prompts.html
---

# DialogPilot 客服 Gold 数据集生成与审核 Prompt

> 历史归档：本文件记录最初的 Gold authoring 约束。由它生成的 80 条模型样本现已重新定位为 `dialogpilot-synthetic-contract-v1` / `SYNTHETIC_CONTRACT_LOCKED`，不再等待人工 Gold 晋级。当前执行合同见[评测收敛执行手册](./customer-service-agent-evaluation-plan.zh-CN.md)。下文的 `DRAFT_MODEL_GENERATED` / `GOLD_APPROVED` 仅用于解释原始生成流程，不是现行数据状态。

> 状态：可直接复制使用的 Prompt 模板
>
> 版本：v1.0
>
> 日期：2026-09-02
>
> 配套方案：[客服 Agent 数据集与多模态 RAG 评测方案](./customer-service-agent-evaluation-datasets.zh-CN.md)

## 1. 使用原则

模型生成的内容只能是 `DRAFT_MODEL_GENERATED`，不能直接成为 Gold。正式晋级必须满足：

```text
模型起草
→ Schema Validator
→ Source/Fixture Resolver
→ Deterministic Assertion Runner
→ 业务专家审核
→ 图片区域/答案复核
→ Gold Sign-off
```

必须向 Prompt 提供真实的：

- 企业政策和知识片段；
- Tool schema 与 fixture；
- 订单、退款、工单、承诺等初始状态；
- 可使用的图片/截图 asset 与人工标注；
- Route、Authority、Media、Handoff 等正式合同。

资料缺失时，模型必须返回 `REQUIRES_AUTHORING`，不得补写“看起来合理”的政策、工具结果、图片内容或业务终态。

## 2. Gold Case 生成主 Prompt

复制以下 Prompt，并把末尾输入区的占位符替换为本批次真实资料。

```text
你是 DialogPilot 客服 Agent 评测数据设计专家。你的任务不是回答客服问题，而是根据给定的权威材料，生成可由程序和人工共同验收的客服评测 case 草稿。

你必须遵守以下最高优先级规则：

1. 不得发明企业政策、商品参数、订单状态、工具结果、工单状态、承诺、图片内容或人工处理结果。
2. 所有期望事实必须能绑定到输入中的 source_ref、fixture_ref、tool_receipt_ref、asset_ref 或人工 annotation_ref。
3. 如果生成 case 所需的权威事实缺失，输出该 case 的 status="REQUIRES_AUTHORING" 和 missing_authoring_inputs，不得猜测。
4. 模型只生成 DRAFT，不得把自己生成的内容标记为 VERIFIED/GOLD。
5. Gold 判断以业务终态、工具回执、证据引用和动作安全为主，不以最终答案逐字匹配为主。
6. 图片只能证明视觉上可观察的事实；退款资格、订单归属、签收时间和业务动作必须分别由政策与业务工具证明。
7. 历史 assistant 文本只能证明“系统曾经这样回复”，不能作为政策、订单状态或问题已解决的权威证据。
8. 实时业务事实必须来自 Tool fixture；静态政策必须来自 Knowledge source；开放工单、承诺、Handoff、PendingSignal 和未知工具副作用必须来自对应领域 fixture。
9. 不允许为了增加难度，把所有能力堆进同一个 case。每条 case 必须有一个清晰的主要失败面，最多增加一个次要干扰面。
10. 输出必须符合下面的 JSONL schema：一行一个完整 JSON 对象，不输出 Markdown、解释文字或代码围栏。

你的目标是生成能够区分以下能力的 case：

- DIRECT / KNOWLEDGE_QA / AGENT_TASK / MIXED / MULTI_DOMAIN / CLARIFY / HANDOFF / OUT_OF_SCOPE；
- CONTINUE / EXPAND / SWITCH / AMBIGUOUS，以及明确 PendingSignal 的 RESUME；
- 单 Worker、同 Owner 多 requirement 合并、真正跨 Owner TaskGraph；
- KnowledgeRetriever、业务 Tool、ServiceEpisode、Memory 和 ServiceContinuity 的权威边界；
- MediaNeed L0（无需视觉）、L1（OCR/Layout）、L2（VLM/区域理解）；
- 同图复用、新区域增量分析、新图分析和错误缓存失效；
- 低风险查询、高风险动作、审批和人工 Handoff；
- 正常成功、NO_EVIDENCE、UNAVAILABLE、CONFLICT、OUTCOME_UNKNOWN 等 typed outcome。

生成步骤：

A. 输入完整性检查
- 验证请求的 slice 是否有对应政策、工具、状态和媒体 fixture。
- 验证每个 expected fact 是否存在可解析的权威 ref。
- 缺失时只生成 REQUIRES_AUTHORING case，不构造答案。

B. 设计 case family
- 每个语义 family 至少包含一个正常 case 和一个最小反事实 case。
- 反事实只改变一个关键变量，例如 VIP 身份、SKU、签收时间、图片区域、知识版本、工具状态或权限。
- 同一 family 的所有变体使用同一个 group_id，之后必须按 group 整体切分，不能随机拆 turn。

C. 构造初始状态
- 明确 conversation、user、tenant、订单、工单、承诺、Handoff、PendingSignal、Memory 和媒体初始状态。
- 未使用的状态字段保持空，不要编造背景故事。

D. 构造多轮输入
- 每轮用户表达自然、口语化，不直接暴露 intent label、expected tool 或答案。
- 若测试 Continuation，后续轮必须依赖前文，但仍能由给定上下文唯一解释。
- 若测试 CLARIFY，必须明确缺少哪个必要字段以及合法补充方式。
- 若测试 RESUME，必须提供显式 signal/reply binding；不能仅凭文本相似度恢复旧 run。

E. 定义期望执行
- 给出每轮 expected route、owner、requirements、tools、media need、evidence 和 continuation decision。
- required_tools 只列必须调用的工具；allowed_tools 表示可接受但非必需；forbidden_tools 表示调用即失败。
- 对写动作提供 idempotency/effect expectation；对只读工具提供 freshness expectation。
- 多 Agent 只在存在多个独立、可验收、不同 Owner 的任务时启用。

F. 定义期望结果
- expected_claims 描述必须表达的事实，不生成唯一措辞答案。
- forbidden_claims 描述绝不能发布的结论。
- expected_business_state 必须来自输入 fixture 的确定性迁移。
- expected_evidence_bindings 将每个 claim 绑定到 authority 与 source/receipt/asset ref。
- 如果证据不足，正确结果应是澄清、拒答、补材料或 Handoff，而不是生成完整答案。

G. 定义评分
- 优先输出 deterministic_assertions。
- 只有语气、简洁度、解释完整性等不可确定性维度才进入 human_rubric。
- 任何硬安全失败、错误副作用、跨用户泄漏、错误业务终态或伪造证据都必须使 task_success=false，不能被模型评分抵消。

输出 JSONL，每个对象必须符合以下逻辑 schema：

{
  "schema_version": "dialogpilot-gold-case-v1",
  "case_id": "稳定、可读、批次内唯一",
  "group_id": "语义 family ID",
  "status": "DRAFT_MODEL_GENERATED | REQUIRES_AUTHORING",
  "title": "简短描述，不泄露答案",
  "slice": "PRODUCT_ID | INSTALLATION | DAMAGE | SCREENSHOT | POLICY_TOOL | MEMORY | SERVICE_CONTINUITY | HANDOFF | SECURITY | OTHER",
  "difficulty": "BASIC | COMPOSITIONAL | ADVERSARIAL",
  "risk": "LOW | MEDIUM | HIGH | CRITICAL",
  "language": "zh-CN",
  "provenance": {
    "policy_source_refs": [],
    "tool_fixture_refs": [],
    "business_state_fixture_refs": [],
    "conversation_fixture_refs": [],
    "memory_fixture_refs": [],
    "service_continuity_fixture_refs": [],
    "asset_refs": [],
    "annotation_refs": []
  },
  "initial_state": {
    "tenant_id": "测试专用 opaque ID",
    "user_id": "测试专用 opaque ID",
    "conversation_id": "测试专用 opaque ID",
    "business_state": {},
    "memory_state": {},
    "service_continuity": {
      "active_case_refs": [],
      "open_commitment_refs": [],
      "active_handoff_refs": [],
      "open_pending_signal_refs": [],
      "outcome_unknown_tool_operation_refs": []
    },
    "media_assets": [
      {
        "asset_ref": "",
        "asset_checksum": "",
        "available_annotations": [],
        "acl_scope": ""
      }
    ]
  },
  "turns": [
    {
      "turn_id": "t1",
      "user_message": "自然语言用户消息",
      "attachment_refs": [],
      "expected": {
        "admission": "NEW_INVOCATION | RESUME_PENDING_SIGNAL",
        "continuation": "CONTINUE | EXPAND | SWITCH | AMBIGUOUS | NOT_APPLICABLE",
        "route_mode": "DIRECT | KNOWLEDGE_QA | AGENT_TASK | MIXED | MULTI_DOMAIN | CLARIFY | HANDOFF | OUT_OF_SCOPE",
        "primary_owner": "",
        "supporting_owners": [],
        "requirements": [
          {
            "requirement_id": "r1",
            "description": "可独立验收的必要事实或动作",
            "authority": "KNOWLEDGE | ORDER | REFUND | TICKET | COMMITMENT | HANDOFF | MEMORY | MEDIA | HUMAN | OTHER",
            "mandatory": true
          }
        ],
        "task_formation": {
          "expected_task_count": 1,
          "expected_parallel_groups": [],
          "must_not_fan_out": false
        },
        "media": {
          "media_need": "L0 | L1 | L2 | NOT_APPLICABLE",
          "required_asset_refs": [],
          "required_region_refs": [],
          "expected_reuse": [],
          "expected_recompute": [],
          "must_not_call_vlm": false
        },
        "tools": {
          "required": [],
          "allowed": [],
          "forbidden": [],
          "expected_calls": [],
          "freshness_requirements": [],
          "effect_expectations": []
        },
        "knowledge": {
          "retrieval_required": false,
          "required_source_refs": [],
          "required_evidence_refs": [],
          "must_not_retrieve": false,
          "reuse_expectation": "REUSE_VALID | RETRIEVE_MISSING | REVALIDATE | INVALIDATE | NOT_APPLICABLE"
        },
        "handoff": {
          "required": false,
          "reason_code": null,
          "required_contract_fields": []
        }
      }
    }
  ],
  "expected_outcome": {
    "terminal_kind": "ANSWER | CLARIFY | HANDOFF | SAFE_REFUSAL | AWAIT_SIGNAL",
    "expected_claims": [
      {
        "claim_id": "c1",
        "semantic_claim": "不限定措辞的必要事实",
        "authority": "",
        "evidence_refs": []
      }
    ],
    "forbidden_claims": [],
    "expected_actions": [],
    "forbidden_actions": [],
    "expected_business_state": {},
    "expected_open_obligations": [],
    "expected_closed_obligations": []
  },
  "deterministic_assertions": [
    {
      "assertion_id": "a1",
      "type": "ROUTE | TOOL_CALL | TOOL_NOT_CALLED | RECEIPT | BUSINESS_STATE | EVIDENCE | CITATION | MEDIA_ROUTE | CACHE_REUSE | NO_DUPLICATE_EFFECT | HANDOFF_CONTRACT | PRIVACY | OTHER",
      "operator": "EQ | CONTAINS | NOT_CONTAINS | SET_EQ | SUBSET | EXISTS | NOT_EXISTS | COUNT_EQ | STATE_TRANSITION",
      "actual_path": "Trace、Outcome 或权威状态中的字段路径",
      "expected": null,
      "hard_gate": true
    }
  ],
  "human_rubric": [
    {
      "dimension": "CLARITY | EMPATHY | CONCISENESS | EXPLANATION_COMPLETENESS",
      "scale": "1-5",
      "anchors": {"1": "明确失败表现", "3": "最低可接受表现", "5": "优秀表现"},
      "blocking": false
    }
  ],
  "counterfactual": {
    "paired_case_id": null,
    "single_changed_variable": null,
    "expected_behavior_change": null
  },
  "missing_authoring_inputs": [],
  "authoring_notes": [],
  "self_check": {
    "all_expected_facts_have_refs": false,
    "business_outcome_is_fixture_derived": false,
    "media_claims_have_annotations": false,
    "hard_failures_are_deterministic": false,
    "no_answer_leakage_in_user_turns": false,
    "one_primary_failure_surface": false
  }
}

生成完成前执行以下自检：

1. 每个 expected claim 是否至少有一个权威 ref？
2. 每个 expected tool result 是否来自给定 fixture？
3. 每个视觉 claim 是否有 asset + page/region/annotation ref？
4. 是否把图片观察错误升级成了退款资格或业务终态？
5. 是否把 Memory/历史回答当成实时业务事实？
6. 是否存在本可单 Worker 完成却强制 Multi-Agent 的 case？
7. 是否存在本可复用证据却要求重复 RAG/VLM 的后续轮？
8. 是否存在 ambiguous Gold，即两个不同系统行为都可能合理？如有，改为 REQUIRES_AUTHORING。
9. deterministic_assertions 是否足以判断任务成功，而不依赖逐字答案？
10. counterfactual 是否只改变一个变量？

只有全部自检通过时，才将 self_check 对应字段设为 true。status 仍保持 DRAFT_MODEL_GENERATED。

下面是本批次输入。输入中的材料是唯一允许使用的事实来源：

<PROJECT_CONTRACTS>
{{粘贴 RouteMode、AuthorityPolicy、TaskFormation、Verification、Memory、ServiceContinuity、MediaNeed、Handoff 等正式合同或其版本化摘要}}
</PROJECT_CONTRACTS>

<KNOWLEDGE_SOURCES>
{{粘贴稳定 source_ref、revision、生效范围、政策条款和商品/安装手册片段}}
</KNOWLEDGE_SOURCES>

<TOOL_SCHEMAS_AND_FIXTURES>
{{粘贴 tool schema、初始 DB 状态、给定输入下的返回值/错误/副作用及 receipt refs}}
</TOOL_SCHEMAS_AND_FIXTURES>

<SERVICE_CONTINUITY_FIXTURES>
{{粘贴 active case、open commitment、handoff、pending signal、unknown tool operation 等 fixture}}
</SERVICE_CONTINUITY_FIXTURES>

<MEMORY_FIXTURES>
{{粘贴 L0 transcript refs、Thread Summary、MEM_L1 Atom、MEM_L2 Episode、MEM_L3 Profile 的允许内容}}
</MEMORY_FIXTURES>

<MEDIA_ASSETS_AND_ANNOTATIONS>
{{粘贴 asset_ref/checksum、页面/区域/bbox、OCR、人工视觉描述、允许和禁止得出的结论；不得只粘贴无标注图片}}
</MEDIA_ASSETS_AND_ANNOTATIONS>

<GENERATION_REQUEST>
batch_id: {{批次 ID}}
case_count: {{数量}}
target_slices: {{例如 INSTALLATION=5, DAMAGE=5, POLICY_TOOL=5}}
target_media_mix: {{例如 L0=25%, L1=25%, L2=50%}}
target_route_mix: {{各 RouteMode 数量}}
target_continuation_mix: {{CONTINUE/EXPAND/SWITCH/AMBIGUOUS/RESUME 数量}}
target_risk_mix: {{LOW/MEDIUM/HIGH/CRITICAL 数量}}
required_counterfactual_families: {{数量}}
language: zh-CN
max_turns_per_case: {{建议 2-6}}
</GENERATION_REQUEST>
```

## 3. 80 条首版批次输入示例

不要一次让模型生成 80 条。建议分 8 批，每批 10 条，并在下一批生成前先完成 schema 和来源校验。

```yaml
batch_plan:
  - batch_id: product-id-01
    case_count: 10
    target_slices: {PRODUCT_ID: 10}
  - batch_id: product-id-02
    case_count: 10
    target_slices: {PRODUCT_ID: 10}
  - batch_id: installation-01
    case_count: 10
    target_slices: {INSTALLATION: 10}
  - batch_id: installation-02
    case_count: 10
    target_slices: {INSTALLATION: 10}
  - batch_id: damage-screen-01
    case_count: 10
    target_slices: {DAMAGE: 5, SCREENSHOT: 5}
  - batch_id: damage-screen-02
    case_count: 10
    target_slices: {DAMAGE: 5, SCREENSHOT: 5}
  - batch_id: mixed-service-01
    case_count: 10
    target_slices: {POLICY_TOOL: 6, SERVICE_CONTINUITY: 4}
  - batch_id: mixed-service-02
    case_count: 10
    target_slices: {POLICY_TOOL: 4, MEMORY: 2, HANDOFF: 4}
```

推荐全局约束：

- 至少 20 条本应为 L0，验证“有图片不等于需要 VLM”；
- 至少 20 条为 L1，区分 OCR/Layout 与完整视觉理解；
- 至少 20 条连续图片对话，覆盖同图下一步、新区域和新图；
- 至少 20 个 counterfactual pair；
- 至少 15 条正确结果是 Clarify/Handoff/Await，而不是回答；
- 至少 10 条覆盖 ServiceContinuity/未完成工单；
- 至少 10 条覆盖工具失败、超时、冲突或 unknown outcome；
- 高风险 case 不少于 10 条，但不能通过给每条 case 强行加安全标签凑数。

## 4. 独立审核 Prompt

生成完成后，用另一个模型上下文或人工审核者运行以下 Prompt。审核者不能看到生成模型的思考过程，只读取权威材料和 case JSON。

```text
你是 DialogPilot Gold 数据集独立审核者。你的职责不是改写 case，而是判断它能否晋级为正式 Gold。

输入包括：
1. 一条 DRAFT case JSON；
2. 该 case 引用的权威政策、工具 fixture、业务状态、媒体 annotation 和项目合同；
3. Schema Validator 与 deterministic dry-run 结果。

逐项审核：

A. Provenance
- 每个 expected claim、动作、业务终态和媒体观察是否都有可解引用来源？
- 是否存在模型自行补写的政策、状态、图片内容或处理结果？

B. Authority
- 静态政策、实时状态、Memory、视觉观察、人工结论是否由正确 Owner 证明？
- 是否把历史回答或模型推断误当权威事实？

C. Determinism
- 给定相同初始状态和用户输入，期望路线、必要工具、终态和硬断言是否唯一？
- 是否存在两个都合理但只接受其中一个的 Gold？

D. Service behavior
- 是否真的需要 RAG、Tool、Multi-Agent、VLM 或 Handoff？
- 有没有人为堆叠组件、重复检索、重复 VLM 或过度 fan-out？

E. Safety and effects
- 写动作是否有权限、审批、幂等和业务终态断言？
- forbidden action、duplicate effect、unknown outcome 和隐私边界是否可确定性评分？

F. Media
- MediaNeed L0/L1/L2 是否合理？
- 每个视觉 claim 是否绑定真实 asset/region annotation？
- 图片是否被错误用于决定政策资格或业务状态？

G. Dataset quality
- 用户表达是否自然且没有泄露标签/答案？
- case family/counterfactual 是否只改变一个关键变量？
- group_id 是否足以防止 dev/heldout 泄漏？
- 主要失败面是否清楚？

只输出以下 JSON：

{
  "case_id": "",
  "decision": "APPROVE | REVISE | REJECT",
  "blocking_findings": [
    {
      "code": "MISSING_SOURCE | WRONG_AUTHORITY | AMBIGUOUS_GOLD | INVALID_FIXTURE | MEDIA_UNGROUNDED | NON_DETERMINISTIC_ASSERTION | OVERCOMPOSED | ANSWER_LEAKAGE | SPLIT_LEAKAGE | OTHER",
      "path": "case JSON path",
      "reason": "简洁、可复核的原因",
      "required_fix": "必须补充或修改什么"
    }
  ],
  "non_blocking_notes": [],
  "verified_source_refs": [],
  "deterministic_assertions_reviewed": [],
  "human_review_required": [],
  "eligible_for_gold_signoff": false
}

判定规则：
- 任一权威来源缺失、Gold 歧义、错误业务终态、无区域视觉结论或不可确定评分，必须 REVISE/REJECT。
- 文风问题不能掩盖业务正确性问题。
- APPROVE 只表示有资格进入人工 Gold sign-off；你无权直接把 case 状态改为 GOLD。
```

## 5. 最终晋级条件

只有满足以下全部条件，流水线才能把状态从 `DRAFT_MODEL_GENERATED` 改为 `GOLD_APPROVED`：

```text
schema_valid = true
all_refs_resolvable = true
fixture_replay_passed = true
deterministic_assertions_passed = true
independent_review = APPROVE
business_owner_signoff = true
media_annotation_signoff = true  # 有媒体时
split_leakage_check = pass
```

Prompt 负责提高起草效率；Gold 的权威性来自真实 fixture、可执行断言和审核签署，而不是生成模型的自信程度。
