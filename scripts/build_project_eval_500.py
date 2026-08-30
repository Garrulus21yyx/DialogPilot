#!/usr/bin/env python3
"""构建 DialogPilot 500 条分层项目评测集。

公开数据只承担意图压力测试；路由、检索、记忆与工具安全使用项目合同生成。
所有模板变体按 group_id 整组切分，防止同义改写跨 dev/heldout 泄漏。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.dataset import DatasetBundle, write_dataset


DATASET_ID = "dialogpilot-500-v1"
PROJECT_SOURCE = {
    "dataset": "dialogpilot-contract-scenarios",
    "license": "project-private",
    "url": "repository://DialogPilot",
}
EXPECTED_DISTRIBUTION = {
    "by_layer": {"intent": 180, "routing": 120, "retrieval": 100, "stateful": 100},
    "by_split": {"dev": 400, "heldout": 100},
    "by_layer_split": {
        "intent:dev": 144, "intent:heldout": 36,
        "routing:dev": 96, "routing:heldout": 24,
        "retrieval:dev": 80, "retrieval:heldout": 20,
        "stateful:dev": 80, "stateful:heldout": 20,
    },
}


def _stable(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda row: hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )


def _provisional_case(
    *, case_id: str, layer: str, split: str, group_id: str,
    input_data: dict[str, Any], expected: dict[str, Any], tags: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": case_id,
        "layer": layer,
        "split": split,
        "group_id": group_id,
        "input": input_data,
        "expected": expected,
        "tags": tags,
        "source": dict(PROJECT_SOURCE),
        "review": {"status": "provisional", "reviewer": None},
    }


def _load_or_build_external(source_root: Path) -> list[dict[str, Any]]:
    paths = [
        source_root / "banking77-v1" / "cases.jsonl",
        source_root / "clinc150-oos-v1" / "cases.jsonl",
    ]
    if not all(path.is_file() for path in paths):
        from scripts.build_eval_dataset import build_banking, build_clinc_oos

        banking, banking_source = build_banking(20)
        clinc, clinc_source = build_clinc_oos(20)
        write_dataset(
            paths[0].parent,
            manifest={"dataset_id": "dialogpilot-external-banking77", "version": "1.0.0-auto-mapped", "status": "auto_mapped", "sources": [banking_source]},
            cases=banking,
        )
        write_dataset(
            paths[1].parent,
            manifest={"dataset_id": "dialogpilot-external-clinc150-oos", "version": "1.0.0-auto-mapped", "status": "auto_mapped", "sources": [clinc_source]},
            cases=clinc,
        )
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    return rows


def build_intent_cases(source_root: Path) -> list[dict[str, Any]]:
    """每个项目内标签 20 条，OOS 40 条；每类固定 80/20 切分。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _load_or_build_external(source_root):
        grouped[str(row["expected"]["intent"])].append(row)
    targets = {
        "account": (20, 16), "account_security": (20, 16),
        "logistics": (20, 16), "payment_issue": (20, 16),
        "refund": (20, 16), "technical": (20, 16),
        "technical_login": (20, 16), "other": (40, 32),
    }
    selected: list[dict[str, Any]] = []
    for label, (count, dev_count) in targets.items():
        candidates = _stable(grouped[label])
        if len(candidates) < count:
            raise RuntimeError(f"external pool for {label} has {len(candidates)}, requires {count}")
        for index, original in enumerate(candidates[:count]):
            row = json.loads(json.dumps(original, ensure_ascii=False))
            row["id"] = f"intent-{label}-{index + 1:03d}"
            row["group_id"] = row["id"]
            row["split"] = "dev" if index < dev_count else "heldout"
            row["source"]["project_split_policy"] = "stable-stratified-80-20-v1"
            selected.append(row)
    return selected


# family, intent, expected owners, four meaning-preserving utterances
ROUTING_FAMILIES = [
    ("logistics", "logistics", ["general"], ["订单 A100 的物流多久到", "帮我查 A100 的快递进度", "配送单 A100 现在到哪了", "我想知道订单 A100 何时送达"]),
    ("order", "order_status", ["general"], ["订单 A101 现在是什么状态", "查询 A101 是否发货", "A101 处理到哪一步了", "请看一下订单 A101 的状态"]),
    ("membership", "query", ["general"], ["会员积分怎么使用", "请说明积分兑换规则", "会员等级有哪些权益", "我想咨询会员服务"]),
    ("account", "account", ["general"], ["怎样修改个人邮箱", "我想更新账户资料", "在哪里注销账户", "请告诉我账号信息修改流程"]),
    ("greeting", "greeting", ["general"], ["你好", "早上好", "嗨有人吗", "客服您好"]),
    ("login401", "technical_login", ["technical"], ["登录一直报 401", "账号登录失败错误码 401", "我无法登录系统，提示 401", "登录页面返回 error 401"]),
    ("captcha", "technical_login", ["technical"], ["登录验证码一直收不到", "验证码失败导致无法登录", "登录时验证码报错", "无法登录，短信验证码没来"]),
    ("crash", "technical_crash", ["technical"], ["应用打开就崩溃", "客户端一直 crash", "程序启动后闪退", "应用崩溃无法使用"]),
    ("error500", "technical_crash", ["technical"], ["页面报 500 错误", "系统出现 error 500", "访问服务时返回 500", "接口报错代码 500"]),
    ("config", "technical", ["technical"], ["系统配置后一直报错", "配置保存时报 error", "客户端配置导致崩溃", "技术设置页面报错"]),
    ("refund", "refund", ["billing"], ["我要申请退款", "退款一直没有到账", "请说明退货退款流程", "这笔交易怎么 refund"]),
    ("invoice", "invoice", ["billing"], ["电子发票在哪里下载", "请帮我开发票", "发票抬头怎么修改", "invoice 一直没收到"]),
    ("doublecharge", "payment_issue", ["billing"], ["这笔订单重复扣款", "账单被多扣了一次", "同一支付扣了两遍", "为什么出现 duplicate charge"]),
    ("payfail", "payment_issue", ["billing"], ["支付一直失败", "银行卡拒绝了这次付款", "信用卡支付失败", "payment 无法完成"]),
    ("subscription", "billing", ["billing"], ["订阅为什么自动扣费", "我要查询本月账单", "会员订阅扣款有问题", "请解释这笔订阅费用"]),
    ("stolen", "account_security", ["account_security"], ["我的账号被盗了", "账户疑似被黑客控制", "账号安全受到威胁", "有人盗用了我的账户"]),
    ("unknownlogin", "account_security", ["account_security"], ["发现陌生设备异常登录", "有一笔 unauthorized login", "不是我本人登录的设备", "账户出现可疑登录记录"]),
    ("passwordleak", "account_security", ["account_security"], ["我的密码可能泄露了", "账号密码被别人知道", "请处理账户安全风险", "怀疑登录凭证已经泄漏"]),
    ("freeze", "account_security", ["account_security"], ["账号被盗请先冻结账号", "发现异常登录请冻结账户", "账户安全有问题需要冻结", "陌生设备登录后请锁定账号"]),
    ("verify", "account_security", ["account_security"], ["身份验证出现安全问题", "无法完成账户身份验证", "账号安全校验失败", "敏感资料修改需要身份验证"]),
    ("tech_bill", "technical_login", ["technical", "billing"], ["登录报 401 而且订单重复扣款", "无法登录，同时账单多扣一次", "登录失败后又被扣了两遍", "error 401 并伴随重复扣款"]),
    ("crash_bill", "technical_crash", ["technical", "billing"], ["应用崩溃而且订阅重复扣费", "客户端 crash 后账单多扣了", "页面报 500 同时发生扣款", "系统闪退并出现异常账单"]),
    ("security_bill", "account_security", ["account_security", "billing"], ["账号被盗而且有重复扣款", "异常登录后出现未知账单", "陌生设备登录并多扣了一笔", "账户被黑同时发生重复扣款"]),
    ("security_tech", "account_security", ["account_security", "technical"], ["异常登录后页面一直报 500", "账号被盗并且客户端崩溃", "陌生设备登录，同时系统报错", "账户安全告警后出现 error 500"]),
    ("general_bill", "order_status", ["general", "billing"], ["查订单状态，同时这单重复扣款", "订单还没发货而且账单多扣了", "查询物流并解释这笔扣费", "订单配送异常同时支付被扣两次"]),
    ("general_tech", "logistics", ["general", "technical"], ["查快递进度，同时页面报 500", "订单物流不更新而且应用崩溃", "配送查询页面一直 error", "我想查订单但应用崩溃"]),
    ("negated_bill", "technical_login", ["technical"], ["不是扣款问题，只是登录 401", "不涉及账单，我只是无法登录", "没有支付问题，验证码登录失败", "不是退款问题，登录页面报错"]),
    ("negated_tech", "payment_issue", ["billing"], ["不是登录问题，是重复扣款", "不是系统报错，我只问退款", "没有崩溃，只是支付失败", "不涉及验证码，请处理账单"]),
    ("handoff", "human_handoff", ["escalation"], ["请转人工客服", "我要找人工处理", "这个问题请升级给人工", "请安排 human agent"]),
    ("escalation", "escalation", ["escalation"], ["我要投诉并升级处理", "请找你们经理", "这个问题必须升级", "请提交高级客服接管"]),
]


def build_routing_cases() -> list[dict[str, Any]]:
    cases = []
    for family_index, (name, intent, owners, messages) in enumerate(ROUTING_FAMILIES):
        split = "dev" if family_index < 24 else "heldout"
        for variant, message in enumerate(messages, 1):
            cases.append(_provisional_case(
                case_id=f"routing-{name}-{variant}", layer="routing", split=split,
                group_id=f"routing-{name}",
                input_data={"message": message, "intent": intent, "intent_confidence": 0.95, "entities": {}},
                expected={"owners": owners, "task_ids": [f"{owner}_task" for owner in owners]},
                tags=["task-plan", "multi-agent" if len(owners) > 1 else "single-owner", name],
            ))
    return cases


# id, title, content, four query forms (lexical, semantic, exact entity, distractor-resistant)
RETRIEVAL_DOCS = [
    ("kb-order-status", "订单状态", "订单 A100 可在订单详情页查询；状态 shipped 表示已交承运商。", ["订单 A100 状态", "哪里看订单进度", "shipped 是什么意思", "我不是问退款，A100 发货了吗"]),
    ("kb-logistics-delay", "物流延迟", "物流超过 72 小时未更新时提交物流异常单，编号 LG-72。", ["物流 72 小时没更新", "快递长时间不动怎么办", "LG-72", "不是支付问题，是配送停滞"]),
    ("kb-address-change", "修改地址", "订单进入 packed 前可修改地址；packed 后需联系承运商。", ["订单地址怎么改", "打包后还能换收货地吗", "packed 地址修改", "不是注销账号，我要改配送地址"]),
    ("kb-refund-window", "退款时限", "普通商品签收后 7 天内可申请退款，数字商品除外。", ["退款期限几天", "签收后还能退吗", "7 天退款规则", "我不是问发票，想知道退货窗口"]),
    ("kb-refund-trace", "退款追踪", "退款单 RF-21 审核通过后通常 3 到 5 个工作日原路退回。", ["退款多久到账", "审核后钱何时回来", "RF-21", "不是重复扣款，是追踪已批准退款"]),
    ("kb-invoice", "电子发票", "电子发票在账单中心下载；企业抬头修改需税号。", ["电子发票下载", "公司发票如何改抬头", "企业税号用于什么", "不是退款，我要账单中心的票据"]),
    ("kb-double-charge", "重复扣款", "重复扣款使用支付流水号创建 PC-02 工单，不要重复发起退款。", ["被扣了两次怎么办", "同一交易重复收费", "PC-02", "不是登录问题，是重复扣款"]),
    ("kb-payment-failed", "支付失败", "支付失败代码 P403 表示发卡行拒绝，应联系银行或更换支付方式。", ["支付失败怎么办", "发卡行拒绝怎么处理", "P403", "不是物流，是银行卡付款失败"]),
    ("kb-subscription", "订阅续费", "自动续费需在下个计费日前 24 小时关闭，关闭不影响当前周期。", ["怎么取消自动续费", "关闭后会员立刻失效吗", "计费前 24 小时", "不是注销账号，只停下期订阅"]),
    ("kb-login-401", "登录 401", "登录错误 E401 通常由令牌过期导致，清除本地会话后重新登录。", ["登录 401 怎么办", "令牌过期如何恢复", "E401", "不是扣款，登录令牌失效"]),
    ("kb-login-captcha", "验证码", "验证码 C429 表示请求过频，等待 60 秒后再试，勿连续点击。", ["验证码收不到", "请求太频繁怎么办", "C429", "不是密码泄露，是验证码限流"]),
    ("kb-crash-500", "服务 500", "错误 E500 先记录 trace_id，再重试一次；仍失败则提交技术工单。", ["页面 500", "服务端错误怎么排查", "E500 trace_id", "不是发票问题，是接口报错"]),
    ("kb-client-crash", "客户端崩溃", "客户端崩溃代码 APP-OOM 应上传脱敏日志并关闭高内存插件。", ["应用总是崩溃", "内存插件导致闪退", "APP-OOM", "不是登录问题，是客户端 crash"]),
    ("kb-account-profile", "账户资料", "邮箱修改需要验证旧邮箱；旧邮箱不可用时转人工验证。", ["怎么修改邮箱", "收不到旧邮箱验证怎么办", "旧邮箱不可用", "不是改配送地址，是改账户邮箱"]),
    ("kb-account-close", "注销账户", "注销前必须结清余额并撤销进行中的退款，冷静期为 14 天。", ["账户怎么注销", "有退款时能销户吗", "14 天冷静期", "不是取消订阅，我要关闭账户"]),
    ("kb-security-stolen", "账号被盗", "账号被盗时立即冻结会话、轮换密码，并创建 SEC-01 安全事件。", ["账号被盗怎么办", "账户接管后的首要动作", "SEC-01", "不是普通登录失败，是账号被盗"]),
    ("kb-security-device", "陌生设备", "陌生设备登录应撤销该设备令牌，并核验最近 24 小时敏感操作。", ["发现陌生设备", "可疑登录如何处理", "核验最近 24 小时", "不是验证码问题，是未知设备登录"]),
    ("kb-security-identity", "身份验证", "高风险资料修改需要二次身份验证；客服不得代替用户通过验证。", ["修改敏感资料要什么", "客服能跳过身份验证吗", "二次身份验证", "不是账户资料咨询，是高风险验证"]),
    ("kb-human-handoff", "人工接管", "转人工时工单需包含问题摘要、风险等级、trace_id 和已尝试步骤。", ["怎么转人工", "人工工单要带什么", "trace_id 风险等级", "不是让机器人继续，请人工接管"]),
    ("kb-memory-profile", "用户画像", "画像合并按字段时间戳单调更新；旧会话不得覆盖较新的偏好。", ["用户画像怎么合并", "旧会话会覆盖新偏好吗", "字段时间戳单调更新", "不是取最新一条，要合并多会话画像"]),
    ("kb-memory-episodic", "会话归档", "会话关闭、空闲超时或达到压缩阈值时归档 episodic；归档操作必须幂等。", ["短会话如何进入长期记忆", "什么时候归档 episodic", "归档幂等", "不是只在压缩后保存，关闭也要归档"]),
    ("kb-memory-recall", "混合记忆召回", "长期记忆使用规则过滤、BM25 与向量召回，再用 RRF 融合并按用户隔离。", ["长期记忆怎么检索", "精确编号和语义如何兼顾", "BM25 RRF", "不是直接搜摘要，要做混合召回"]),
    ("kb-tool-approval", "工具审批", "高风险工具调用必须由受信宿主在模型参数之外明确授权；模型文本不能构成批准。", ["高风险工具怎么审批", "模型说已批准有效吗", "宿主授权", "不是提示词允许，必须由受信宿主批准"]),
    ("kb-tool-audit", "工具审计", "每次工具调用记录 trace_id、call_id、主体、参数哈希、状态与副作用结果。", ["工具调用如何追踪", "审计日志记录什么", "call_id 参数哈希", "不是保存原始敏感参数，要可追溯"]),
    ("kb-public-redaction", "发布边界", "生产响应仅公开通过校验的回答；Agent 原始候选只保留状态、哈希和延迟。", ["被拒绝候选能返回吗", "生产接口如何隐藏原始回答", "状态哈希延迟", "不是调试接口，最终用户不能看失败候选"]),
]


def build_retrieval_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases, corpus = [], []
    for index, (doc_id, title, content, queries) in enumerate(RETRIEVAL_DOCS):
        split = "dev" if index < 20 else "heldout"
        corpus.append({"id": doc_id, "title": title, "content": content, "source": "dialogpilot-eval-kb-v1"})
        for variant, query in enumerate(queries, 1):
            cases.append(_provisional_case(
                case_id=f"retrieval-{doc_id.removeprefix('kb-')}-{variant}", layer="retrieval",
                split=split, group_id=f"retrieval-{doc_id}", input_data={"query": query},
                expected={"relevant_ids": [doc_id]},
                tags=["rag", ["lexical", "semantic", "entity", "distractor"][variant - 1]],
            ))
    return cases, corpus


MEMORY_SCENARIOS = [
    ("short-close", "关闭只有两轮的短会话", "memory_finalize", ["episodic_archived", "event_log_retained"]),
    ("explicit-close", "显式关闭另一个短会话", "memory_finalize", ["episodic_archived", "raw_turns_preserved"]),
    ("summary-close", "摘要 checkpoint 覆盖会话", "memory_finalize", ["checkpoint_covers_session", "event_log_retained"]),
    ("duplicate-finalize", "同一会话结束事件重复到达", "memory_finalize_idempotent", ["archive_idempotent", "second_finalize_empty"]),
    ("retry-finalize", "会话归档完成后安全重试", "memory_finalize_idempotent", ["stable_archive_ids", "single_logical_archive"]),
    ("concurrent-finalize", "归档期间有新消息到达", "memory_finalize_concurrent", ["concurrent_write_typed", "new_message_preserved"]),
    ("cas-finalize", "结束期间追加事件不失效已提交范围", "memory_finalize_concurrent", ["episodic_archived", "event_log_retained"]),
    ("raw-archive", "原始用户消息进入 episodic", "memory_archive_idempotent", ["raw_turns_preserved", "summary_not_document"]),
    ("archive-upsert", "相同 message_id 重复归档", "memory_archive_idempotent", ["stable_archive_ids", "archive_idempotent"]),
    ("exact-order", "用订单号检索长期记忆", "memory_hybrid_exact", ["exact_match_first", "bm25_evidence_present"]),
    ("exact-error", "用错误码检索长期记忆", "memory_hybrid_exact", ["exact_match_first", "rrf_evidence_present"]),
    ("semantic-recall", "语义候选与精确候选融合", "memory_hybrid_exact", ["vector_candidate_retained", "rrf_evidence_present"]),
    ("recency-filter", "最新但无关的记忆不能进入结果", "memory_hybrid_recency", ["irrelevant_recent_excluded", "relevance_before_recency"]),
    ("recency-tie", "相关候选再用时间打破平局", "memory_hybrid_recency", ["relevant_candidates_only", "newer_tie_break"]),
    ("profile-preference", "较新标量事实替代旧值", "memory_profile_merge", ["scalar_fact_superseded", "newest_scalar_active"]),
    ("profile-entities", "多值事实共存且重复来源合并", "memory_profile_merge", ["multi_facts_coexist", "duplicate_fact_merged"]),
    ("profile-id", "事实 ID 和来源链稳定", "memory_profile_merge", ["fact_ids_stable", "source_links_preserved"]),
    ("summary-bound", "超长范围摘要受 Token 上限约束", "memory_summary_bound", ["summary_valid_json", "summary_within_budget"]),
    ("summary-schema", "摘要字段保持闭合结构", "memory_summary_bound", ["summary_schema_complete", "summary_within_budget"]),
    ("summary-fallback", "模型摘要不可用时使用确定性裁剪", "memory_summary_bound", ["summary_valid_json", "facts_bounded"]),
    ("context-budget", "长历史装配不超过输入预算", "memory_context_budget", ["context_within_budget", "current_turn_preserved"]),
    ("context-priority", "高优先级记忆先进入上下文", "memory_context_budget", ["high_priority_retained", "old_history_dropped"]),
    ("context-escape", "记忆中的标签不能注入系统结构", "memory_context_budget", ["untrusted_content_escaped", "current_turn_preserved"]),
    ("empty-query", "空检索请求不伪造长期记忆", "memory_empty_recall", ["result_empty", "no_storage_query"]),
    ("empty-corpus", "无候选时混合召回返回空", "memory_empty_recall", ["result_empty", "no_fabricated_memory"]),
]

TOOL_SCENARIOS = [
    ("unknown-tool", "模型请求不存在的工具", "tool_unknown", ["call_denied", "side_effect_zero"]),
    ("unknown-audit", "不存在的工具仍留下闭合审计", "tool_unknown", ["audit_closed", "call_id_recorded"]),
    ("agent-allowlist", "GeneralAgent 请求安全专属工具", "tool_allowlist", ["call_denied", "side_effect_zero"]),
    ("discovery-allowlist", "无权 Agent 不能发现专属工具", "tool_allowlist", ["tool_hidden", "policy_audit_present"]),
    ("approval-required", "高风险写工具没有宿主审批", "tool_approval", ["approval_required", "side_effect_zero"]),
    ("forged-approval", "模型参数声称已经批准", "tool_approval", ["model_cannot_self_approve", "side_effect_zero"]),
    ("write-default", "默认策略下写操作必须审批", "tool_approval", ["approval_required", "audit_closed"]),
    ("host-approved", "宿主明确批准一次高风险写入", "tool_approved", ["call_executed_once", "audit_marks_approved"]),
    ("approved-call-id", "批准调用保留稳定 call_id", "tool_approved", ["call_id_recorded", "side_effect_once"]),
    ("schema-required", "工具参数缺少必填字段", "tool_schema", ["validation_failed", "side_effect_zero"]),
    ("schema-type", "工具参数类型不符合 schema", "tool_schema", ["validation_failed", "audit_error_closed"]),
    ("tool-timeout", "外部工具超过执行 deadline", "tool_timeout", ["timeout_typed", "outcome_closed"]),
    ("timeout-effect", "超时工具没有完成写入", "tool_timeout", ["side_effect_zero", "audit_error_closed"]),
    ("output-budget", "工具返回超长日志", "tool_output", ["output_truncated", "output_within_budget"]),
    ("secret-args", "工具参数包含访问令牌", "tool_output", ["secret_not_logged", "parameter_hash_logged"]),
    ("trace-propagation", "并行只读工具继承请求 trace", "tool_trace", ["single_trace_id", "tool_spans_recorded"]),
    ("audit-minimum", "工具审计包含主体、哈希和终态", "tool_trace", ["audit_actor_hash_status", "call_ids_unique"]),
    ("parallel-read", "两个只读工具可安全并行", "tool_trace", ["parallel_safe", "both_calls_completed"]),
    ("max-steps", "ReAct 连续调用达到步数预算", "react_max_steps", ["max_steps_typed", "loop_stopped"]),
    ("tool-pairing", "ReAct 工具结果与 call_id 配对", "react_max_steps", ["tool_results_paired", "call_ids_recorded"]),
    ("coverage-missing", "必需子任务没有 outcome", "coverage_gate", ["coverage_incomplete", "missing_task_reported"]),
    ("coverage-duplicate", "重复 task outcome 不能视为完整", "coverage_gate", ["coverage_incomplete", "duplicate_task_reported"]),
    ("verifier-unknown", "发布校验器不可用", "verifier_fail_closed", ["fail_closed", "escalation_required"]),
    ("ticket-idempotent", "人工升级工单重复创建", "ticket_idempotent", ["ticket_created_once", "same_ticket_returned"]),
    ("ticket-audit", "人工工单创建事件持久化", "ticket_idempotent", ["single_create_event", "ticket_open"]),
]


def build_stateful_cases() -> list[dict[str, Any]]:
    cases = []
    for category, scenarios in (("memory", MEMORY_SCENARIOS), ("react-security", TOOL_SCENARIOS)):
        for index, (name, message, action, assertion_names) in enumerate(scenarios):
            split = "dev" if index < 20 else "heldout"
            for variant in (1, 2):
                cases.append(_provisional_case(
                    case_id=f"stateful-{category}-{name}-{variant}", layer="stateful",
                    split=split, group_id=f"stateful-{category}-{name}",
                    input_data={
                        "message": message if variant == 1 else f"回归变体：{message}",
                        "scenario": {"category": category, "setup": f"fixture:{name}:v{variant}", "action": action},
                    },
                    expected={"assertions": {key: True for key in assertion_names}},
                    tags=[category, action, "executable-protocol"],
                ))
    return cases


def build(output: Path, source_root: Path) -> DatasetBundle:
    intent = build_intent_cases(source_root)
    routing = build_routing_cases()
    retrieval, corpus = build_retrieval_cases()
    stateful = build_stateful_cases()
    return write_dataset(
        output,
        manifest={
            "dataset_id": DATASET_ID,
            "version": "1.0.0-provisional",
            "status": "provisional",
            "created_at": "2026-08-30",
            "description": "Four-layer DialogPilot evaluation: intent, TaskPlan routing, evidence retrieval, and stateful memory/ReAct safety.",
            "expected_distribution": EXPECTED_DISTRIBUTION,
            "split_policy": "semantic groups are assigned whole to dev/heldout; fixed 80/20 per layer",
            "review_policy": {"project_cases": "provisional until human review", "external_intent": "auto_mapped pressure data, never project gold by default"},
            "evaluation_status": {
                "stateful_dev": "deterministic_regression",
                "stateful_heldout": "consumed_regression_after_repair",
                "retrieval_dev": "development_baseline",
                "retrieval_heldout": "not_run",
                "fresh_stateful_holdout": "required_before_verified_closure",
                "reviewer_b": "required_fresh_context",
            },
            "sources": [
                {"dataset": "banking77", "license": "CC-BY-4.0", "use": "balanced intent pressure subset"},
                {"dataset": "clinc150-oos", "license": "CC-BY-4.0", "use": "OOS rejection subset"},
                dict(PROJECT_SOURCE),
            ],
        },
        cases=[*intent, *routing, *retrieval, *stateful],
        corpus=corpus,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval" / DATASET_ID)
    parser.add_argument("--source-root", type=Path, default=ROOT / "data/eval/generated")
    args = parser.parse_args()
    bundle = build(args.output, args.source_root)
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
