"""
亮点：端到端意图识别

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，理解复杂语义和上下文
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底

三路结果通过加权投票合并，置信度低于阈值时降级为 OTHER。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content
from core.llm_metrics import create_message
from core.model_policy import ModelProfile, ModelRole
from services.evolution.bundle import AgentBundle

logger = logging.getLogger(__name__)


class IntentCategory(Enum):
    """路由与业务分支共同使用的闭合意图集合。"""
    QUERY      = "query"       # 查询信息
    COMPLAINT  = "complaint"   # 投诉不满
    REQUEST    = "request"     # 请求操作
    GREETING   = "greeting"    # 问候
    ESCALATION = "escalation"  # 要求升级/转人工
    TECHNICAL  = "technical"   # 技术问题
    BILLING    = "billing"     # 账单/退款
    ACCOUNT    = "account"     # 账户管理
    FEEDBACK   = "feedback"    # 正面反馈
    ORDER_STATUS = "order_status"        # 订单状态
    LOGISTICS = "logistics"              # 物流配送
    REFUND = "refund"                    # 退款/退货
    INVOICE = "invoice"                  # 发票
    PAYMENT_ISSUE = "payment_issue"      # 支付/扣款异常
    ACCOUNT_SECURITY = "account_security" # 账户安全
    TECHNICAL_LOGIN = "technical_login"  # 登录认证故障
    TECHNICAL_CRASH = "technical_crash"  # 崩溃/错误码
    HUMAN_HANDOFF = "human_handoff"      # 转人工
    OTHER      = "other"


class EvidencePolarity(Enum):
    """Pattern 对某个意图的支持方向，而不是句子的情感倾向。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNCERTAIN = "uncertain"
    QUOTED = "quoted"


@dataclass(frozen=True)
class PatternEvidence:
    intent: IntentCategory
    keyword: str
    span: str
    start: int
    end: int
    polarity: EvidencePolarity
    specific: bool


# 这是 IntentCategory 的业务语义 owner。Prompt、规则和评测文档都应引用同一份
# 定义，避免把“客服领域外的一般问句”误当成 QUERY，或把安全事件误当支付失败。
_INTENT_DEFINITIONS: Dict[IntentCategory, str] = {
    IntentCategory.ORDER_STATUS: "本项目订单的处理、发货状态；不含承运商的一般信息",
    IntentCategory.LOGISTICS: "本项目订单或银行卡的寄送、到达时间、配送方式",
    IntentCategory.REFUND: "取消购买、退货退款、退款进度或退款时限",
    IntentCategory.INVOICE: "发票开具、抬头、税号或电子发票",
    IntentCategory.PAYMENT_ISSUE: "本人发起的银行卡/借记卡支付失败或被拒、重复扣款、支付手续费；不含非本人交易",
    IntentCategory.ACCOUNT_SECURITY: "非本人交易/取现/直接借记、卡片或手机丢失、盗号、异常登录、未主动请求却收到认证码等安全事件",
    IntentCategory.TECHNICAL_LOGIN: "PIN、验证码、登录、卡片解锁或认证代码问题",
    IntentCategory.TECHNICAL_CRASH: "应用崩溃、闪退、HTTP 500 或明确错误码",
    IntentCategory.HUMAN_HANDOFF: "明确要求本项目人工客服或升级处理",
    IntentCategory.TECHNICAL: "银行卡本体、虚拟卡、非接触支付或应用功能不可用；不含卡片丢失、非本人交易和付款被拒",
    IntentCategory.BILLING: "无法细分到退款、发票或支付异常的本项目账单问题",
    IntentCategory.ACCOUNT: "账户资料、地址、邮箱、销户等非安全账户管理",
    IntentCategory.QUERY: "在线银行/银行卡项目内但无法细分的产品支持查询，如支持币种、汇率、ATM、支持国家、卡片受理范围或 Visa/Mastercard",
    IntentCategory.REQUEST: "本项目范围内但无法细分的普通操作请求",
    IntentCategory.COMPLAINT: "对本项目服务表达不满，但未明确要求人工升级",
    IntentCategory.GREETING: "问候或开始对话",
    IntentCategory.ESCALATION: "投诉升级、找经理等升级诉求",
    IntentCategory.FEEDBACK: "对本项目服务的正面评价或建议",
    IntentCategory.OTHER: "在线银行/银行卡客服范围外，或缺少可解析业务指代；一般知识、股票、航班、天气、购物查询均在此类",
}


class UrgencyLevel(Enum):
    """升级优先级；数值越大表示越需要及时人工介入。"""
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    """三路识别融合后的有类型结果及可诊断证据。"""
    intent:     IntentCategory
    confidence: float
    urgency:    UrgencyLevel
    intent_group: str
    entities:   Dict[str, List[str]]   # 从消息中提取的实体
    reasoning:  str
    latency_ms: float
    source_scores: Dict[str, float] = field(default_factory=dict)
    classifier_fingerprint: str = ""
    input_fingerprint: str = ""


# ── V1 Few-shot 模板 ──────────────────────────────────────────────────────────
# V1 同时把这份历史模板用于 Prompt 和字符 n-gram。保留别名以维持 V1 行为和
# 指纹；V2 语义 Provider 只消费下面独立、无跨标签重复的原型合同。
_LLM_FEW_SHOTS: Mapping[IntentCategory, Tuple[str, ...]] = MappingProxyType({
    IntentCategory.QUERY: ("你们支持哪些币种？", "哪些国家可以使用这张卡？", "支持哪些 ATM？"),
    IntentCategory.COMPLAINT: ("等了好几个小时！", "服务太差了！", "一直没人处理！"),
    IntentCategory.REQUEST: ("帮我取消订单", "我需要修改地址", "请协助退款"),
    IntentCategory.GREETING: ("你好", "嗨，有人吗", "早上好"),
    IntentCategory.ESCALATION: ("我要投诉！", "转人工客服", "找你们经理"),
    IntentCategory.TECHNICAL: ("感应支付功能用不了", "虚拟卡本身无法使用", "银行卡磁条功能坏了"),
    IntentCategory.BILLING: ("我看不懂这期账单", "请解释账单构成", "这项账单费用是什么"),
    IntentCategory.ACCOUNT: ("修改邮箱", "注销账户", "更新个人信息"),
    IntentCategory.FEEDBACK: ("服务很棒！", "非常满意", "给个好评"),
    IntentCategory.ORDER_STATUS: ("我的订单现在是什么状态？", "订单有没有发货？", "订单处理到哪一步了？"),
    IntentCategory.LOGISTICS: ("快递什么时候到？", "物流一直不更新", "配送要多久？"),
    IntentCategory.REFUND: ("我要申请退款", "退货退款怎么处理？", "退款多久到账？"),
    IntentCategory.INVOICE: ("帮我开发票", "发票抬头怎么改？", "电子发票在哪里？"),
    IntentCategory.PAYMENT_ISSUE: ("为什么重复扣款？", "支付失败怎么办？", "这个月多扣了钱"),
    IntentCategory.ACCOUNT_SECURITY: ("这笔交易不是我操作的", "银行卡丢了", "没操作却收到认证码"),
    IntentCategory.TECHNICAL_LOGIN: ("登录一直报401", "验证码收不到", "无法登录账号"),
    IntentCategory.TECHNICAL_CRASH: ("应用一直崩溃", "页面报500错误", "系统闪退"),
    IntentCategory.HUMAN_HANDOFF: ("转人工客服", "我要找人工", "请升级处理"),
})
_TEMPLATES = _LLM_FEW_SHOTS


# V2 semantic prototype owner。泛化类不包含已支持的退款、登录、崩溃等细粒度
# 表达；标准化后的同一句原型只能属于一个标签。OTHER 由距离和 margin 拒识，
# 不通过伪造一个万能 OTHER 向量来表达。
_SEMANTIC_PROTOTYPES: Mapping[IntentCategory, Tuple[str, ...]] = MappingProxyType({
    IntentCategory.QUERY: (
        "会员服务都包含哪些内容", "我想了解平台的一般业务规则", "你们目前支持哪些服务",
    ),
    IntentCategory.COMPLAINT: (
        "你们的处理体验太差了", "这个服务让我非常不满意", "等了很久还是没人解决",
    ),
    IntentCategory.REQUEST: (
        "请帮我办理一项业务", "麻烦协助完成这个操作", "我需要你们处理一下",
    ),
    IntentCategory.GREETING: ("你好", "早上好客服", "嗨我来咨询一下"),
    IntentCategory.ESCALATION: (
        "我要向主管投诉", "请把问题升级给经理", "这个问题需要上级处理",
    ),
    IntentCategory.TECHNICAL: (
        "我的银行卡刷不了", "非接触支付功能不能使用", "虚拟卡在商户处不可用",
    ),
    IntentCategory.BILLING: (
        "我看不懂这期账单", "请解释月度账单构成", "账单上的项目是什么意思",
    ),
    IntentCategory.ACCOUNT: (
        "我要更新账户资料", "怎样修改注册邮箱", "请帮我注销账号",
    ),
    IntentCategory.FEEDBACK: (
        "这次服务体验很好", "我想提一个产品建议", "客服处理得很专业",
    ),
    IntentCategory.ORDER_STATUS: (
        "订单目前处理到哪一步", "我的订单发货了吗", "查询订单的处理状态",
    ),
    IntentCategory.LOGISTICS: (
        "包裹预计什么时候送到", "物流轨迹一直没有更新", "银行卡寄送到哪里了",
    ),
    IntentCategory.REFUND: (
        "我想取消购买并把钱退回", "退货以后多久能收到钱", "申请的退款还没有到账",
    ),
    IntentCategory.INVOICE: (
        "我要开电子发票", "怎样修改发票抬头", "开票时税号怎么填写",
    ),
    IntentCategory.PAYMENT_ISSUE: (
        "同一笔付款被扣了两次", "我本人发起的支付失败了", "付款时被收了额外手续费",
    ),
    IntentCategory.ACCOUNT_SECURITY: (
        "这笔交易不是我操作的", "账户出现陌生设备登录", "我的账号可能被盗了",
    ),
    IntentCategory.TECHNICAL_LOGIN: (
        "登录验证码一直收不到", "输入 PIN 后无法进入账户", "应用认证代码不工作",
    ),
    IntentCategory.TECHNICAL_CRASH: (
        "应用打开后立刻闪退", "页面返回 HTTP 500", "客户端启动时一直崩溃",
    ),
    IntentCategory.HUMAN_HANDOFF: (
        "请转接人工客服", "我要真人客服接管", "请安排人工处理这个问题",
    ),
})

_SPECIFIC_INTENTS = {
    IntentCategory.ORDER_STATUS,
    IntentCategory.LOGISTICS,
    IntentCategory.REFUND,
    IntentCategory.INVOICE,
    IntentCategory.PAYMENT_ISSUE,
    IntentCategory.ACCOUNT_SECURITY,
    IntentCategory.TECHNICAL_LOGIN,
    IntentCategory.TECHNICAL_CRASH,
    IntentCategory.HUMAN_HANDOFF,
}

_GENERIC_INTENTS = {
    IntentCategory.QUERY,
    IntentCategory.BILLING,
    IntentCategory.TECHNICAL,
    IntentCategory.ACCOUNT,
    IntentCategory.ESCALATION,
}

_INTENT_GROUPS: Dict[IntentCategory, IntentCategory] = {
    IntentCategory.ORDER_STATUS: IntentCategory.QUERY,
    IntentCategory.LOGISTICS: IntentCategory.QUERY,
    IntentCategory.REFUND: IntentCategory.BILLING,
    IntentCategory.INVOICE: IntentCategory.BILLING,
    IntentCategory.PAYMENT_ISSUE: IntentCategory.BILLING,
    IntentCategory.ACCOUNT_SECURITY: IntentCategory.ACCOUNT,
    IntentCategory.TECHNICAL_LOGIN: IntentCategory.TECHNICAL,
    IntentCategory.TECHNICAL_CRASH: IntentCategory.TECHNICAL,
    IntentCategory.HUMAN_HANDOFF: IntentCategory.ESCALATION,
}

# 紧急关键词
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],
    UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now"],
    UrgencyLevel.MEDIUM:   ["这周", "soon", "快点"],
}

# 分类规则和融合代数是分类器版本的一部分。把它们放在模块级常量中，既避免
# 运行时临时构造，也让 classifier_fingerprint() 能覆盖真实生效的策略。
_SPECIFIC_PATTERNS: Dict[IntentCategory, List[str]] = {
    IntentCategory.HUMAN_HANDOFF: ["转人工", "人工客服", "找人工"],
    IntentCategory.ORDER_STATUS: ["订单状态", "发货了吗", "处理到哪", "order status"],
    IntentCategory.LOGISTICS: ["物流", "快递", "配送", "运单", "delivery", "shipping", "card fast"],
    IntentCategory.REFUND: ["退款", "退货", "refund", "return"],
    IntentCategory.INVOICE: ["发票", "抬头", "税号", "invoice"],
    IntentCategory.PAYMENT_ISSUE: ["重复扣款", "多扣", "支付失败", "扣费", "payment failed", "extra fee"],
    IntentCategory.ACCOUNT_SECURITY: [
        "被盗", "异常登录", "两步验证", "安全", "非本人", "不是我操作", "不是本人操作",
        "陌生交易", "银行卡丢", "卡丢了", "没操作却收到", "didn't buy", "did not make",
        "don't recognize", "do not recognize", "fraudulent", "verify my id", "identity check",
        "didn't take out", "did not get", "non-received cash",
    ],
    IntentCategory.TECHNICAL_LOGIN: [
        "无法登录", "登录失败", "401", "验证码", "reset my pin", "unblock my card",
        "code for the app", "pin is unlocked",
    ],
    IntentCategory.TECHNICAL_CRASH: ["崩溃", "闪退", "500", "报错", "crash"],
    IntentCategory.TECHNICAL: [
        "virtual card", "contactless", "card broke", "card broken", "card no longer works",
        "card hasn't been working", "card gets rejected", "disposable virtual card",
    ],
}

_GENERIC_PATTERNS: Dict[IntentCategory, List[str]] = {
    IntentCategory.ESCALATION: ["投诉", "经理", "supervisor"],
    IntentCategory.COMPLAINT: ["太差", "糟糕", "horrible", "等了很久"],
    IntentCategory.QUERY: ["?", "？", "怎么", "什么", "status"],
    IntentCategory.REQUEST: ["帮我", "需要", "please", "help"],
    IntentCategory.GREETING: ["你好", "嗨", "hello", "hi"],
    IntentCategory.BILLING: ["退款", "扣款", "发票", "refund"],
    IntentCategory.TECHNICAL: ["崩溃", "报错", "error", "crash"],
    IntentCategory.ACCOUNT: ["密码", "邮箱", "账户", "password"],
}

_VOTE_WEIGHTS = {
    "ngram": {"llm": 0.7, "embedding": 0.2, "pattern": 0.1},
    "disabled": {"llm": 0.85, "pattern": 0.15},
}

_INTENT_PROMPT_POLICY = """本项目是在线银行与银行卡客服。银行卡受理范围、ATM 支持、支持国家/币种、汇率、Visa 或 Mastercard 等产品规则都属于项目内 query，不得判为 other。
先判断当前消息及最近对话是否提供了可解析的银行业务指代；没有业务指代的“还是没变化”“之前的事”“怎么又这样”属于信息不足，返回 other。真正范围外的一般问句也返回 other，不能因为它是问句就返回 query。
陌生交易、非本人取现或直接借记、银行卡/手机丢失、未主动请求却收到认证码属于 account_security；本人发起的银行卡付款失败、被拒、重复扣款或支付手续费属于 payment_issue。
银行卡本体、虚拟卡、非接触支付功能不可用属于 technical；PIN、用户主动请求但收不到/不能使用的验证码和解锁故障属于 technical_login。"""

# 任何没有被上述数据常量表达、但会改变输出代数的实现变更都必须升级该值。
_CLASSIFIER_CONTRACT_VERSION = "intent-classifier-v4-pattern-polarity"


_PATTERN_NEGATORS = (
    "不是", "并非", "不属于", "不涉及", "不需要", "不要", "没要求", "没有要求", "未要求",
    "not ", "no ", "don't need", "do not need", "isn't ", "without ",
)
_PATTERN_HEDGES = (
    "可能", "好像", "不确定", "也许", "听说", "maybe ", "perhaps ", "not sure",
)
_PATTERN_QUOTE_PAIRS = (
    ('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"),
)


def _inside_pattern_quotes(message: str, start: int, end: int) -> bool:
    for left, right in _PATTERN_QUOTE_PAIRS:
        if left == right:
            if message[:start].count(left) % 2 == 1:
                return True
            continue
        left_index = message.rfind(left, 0, start + 1)
        if left_index >= 0 and message.find(right, end) >= 0:
            return True
    return False


def _positive_negation_expression(
    message: str,
    intent: IntentCategory,
    keyword: str,
    start: int,
    end: int,
) -> bool:
    """识别“含否定词但实际正向支持意图”的业务表达。"""
    normalized_keyword = keyword.lower()
    if intent is IntentCategory.ACCOUNT_SECURITY and any(token in normalized_keyword for token in (
        "不是我", "不是本人", "非本人", "didn't", "did not", "don't recognize",
        "do not recognize", "non-received",
    )):
        return True
    if intent is IntentCategory.REFUND:
        window = message[max(0, start - 10):min(len(message), end + 10)].lower()
        if re.search(r"(?:没有|没|未).{0,4}(?:收到|到账|拿到).{0,5}(?:退款|退货)", window):
            return True
        if re.search(r"(?:退款|退货).{0,6}(?:没有|没|未).{0,4}(?:收到|到账|拿到)", window):
            return True
    return False


def _pattern_polarity(
    message: str,
    intent: IntentCategory,
    keyword: str,
    start: int,
    end: int,
) -> EvidencePolarity:
    if _inside_pattern_quotes(message, start, end):
        return EvidencePolarity.QUOTED
    if _positive_negation_expression(message, intent, keyword, start, end):
        return EvidencePolarity.POSITIVE
    prefix = message[max(0, start - 14):start].lower()
    # 否定只支配当前局部子句，不能跨过逗号或“但/而是”污染后一个意图。
    prefix = re.split(r"[，,。！？!?；;]|(?:但是|但|而是|不过|只是)", prefix)[-1]
    suffix = message[end:min(len(message), end + 8)].lower()
    if any(hedge in prefix for hedge in _PATTERN_HEDGES):
        return EvidencePolarity.UNCERTAIN
    if any(negator in prefix for negator in _PATTERN_NEGATORS):
        return EvidencePolarity.NEGATIVE
    if re.search(r"(?:没|没有|无).{0,2}问题", suffix):
        return EvidencePolarity.NEGATIVE
    return EvidencePolarity.POSITIVE


def extract_pattern_evidence(message: str) -> tuple[PatternEvidence, ...]:
    """返回全部 Pattern span 及其局部极性；细粒度命中拥有重复 span。"""
    raw = str(message)
    normalized = raw.lower()
    evidence: list[PatternEvidence] = []
    claimed_specific: set[tuple[int, int, str]] = set()
    for specific, patterns in ((True, _SPECIFIC_PATTERNS), (False, _GENERIC_PATTERNS)):
        for intent, keywords in patterns.items():
            for keyword in keywords:
                normalized_keyword = keyword.lower()
                cursor = 0
                while True:
                    index = normalized.find(normalized_keyword, cursor)
                    if index < 0:
                        break
                    end = index + len(normalized_keyword)
                    identity = (index, end, normalized_keyword)
                    if not specific and identity in claimed_specific:
                        cursor = end
                        continue
                    evidence.append(PatternEvidence(
                        intent=intent,
                        keyword=keyword,
                        span=raw[index:end],
                        start=index,
                        end=end,
                        polarity=_pattern_polarity(raw, intent, keyword, index, end),
                        specific=specific,
                    ))
                    if specific:
                        claimed_specific.add(identity)
                    cursor = end
    return tuple(evidence)


def pattern_supports(evidence: List[PatternEvidence] | Tuple[PatternEvidence, ...], intent: IntentCategory) -> bool:
    relevant = [item for item in evidence if item.intent is intent]
    return bool(relevant) and any(
        item.polarity is EvidencePolarity.POSITIVE for item in relevant
    ) and not any(
        item.polarity is not EvidencePolarity.POSITIVE for item in relevant
    )


def _pattern_polarity_scores(evidence: Tuple[PatternEvidence, ...]) -> Dict[IntentCategory, float]:
    """把同一意图的 Pattern 证据压成 [-1, 1]，冲突或不确定时弃权。"""
    grouped: Dict[IntentCategory, Dict[EvidencePolarity, int]] = {}
    for item in evidence:
        counts = grouped.setdefault(item.intent, {})
        counts[item.polarity] = counts.get(item.polarity, 0) + 1
    scores: Dict[IntentCategory, float] = {}
    for intent, counts in grouped.items():
        positives = counts.get(EvidencePolarity.POSITIVE, 0)
        negatives = counts.get(EvidencePolarity.NEGATIVE, 0)
        uncertain = counts.get(EvidencePolarity.UNCERTAIN, 0) + counts.get(EvidencePolarity.QUOTED, 0)
        if positives and not negatives and not uncertain:
            scores[intent] = min(1.0, 0.5 + 0.25 * (positives - 1))
        elif negatives and not positives:
            scores[intent] = -min(1.0, 0.5 + 0.25 * (negatives - 1))
        else:
            scores[intent] = 0.0
    return scores


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """
    端到端意图识别器。

    初始化时不加载任何本地模型，所有 AI 能力通过 Anthropic API 调用。
    模板 Embedding 在首次请求时懒加载并缓存，后续复用。
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
        similarity_mode: str = "ngram",
        model_profile: Optional[ModelProfile] = None,
        cache_ttl_seconds: float = 3600.0,
        fusion_weights: Optional[Mapping[str, Mapping[str, float]]] = None,
        fusion_policy_version: str = "intent-fusion-v1",
    ):
        """创建模型客户端，并初始化模板向量与结果缓存。"""
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client    = AsyncAnthropic(**kwargs)
        self._model_profile = model_profile or ModelProfile(model)
        self.model     = self._model_profile.model
        self.threshold = confidence_threshold
        normalized_mode = similarity_mode.strip().lower()
        if normalized_mode not in {"ngram", "disabled"}:
            raise ValueError("INTENT_SIMILARITY_MODE must be 'ngram' or 'disabled'")
        # 相似度策略属于路由配置，不由模型 API 地址猜测。ngram 模式在远端
        # embedding 不存在或失败时稳定退回本地字符向量。
        self._embedding_enabled = normalized_mode == "ngram"
        self.similarity_mode = normalized_mode
        configured_weights = fusion_weights or _VOTE_WEIGHTS
        if normalized_mode not in configured_weights:
            raise ValueError("intent fusion policy does not support similarity mode")
        self._vote_weights = {
            mode: {str(source): float(weight) for source, weight in weights.items()}
            for mode, weights in configured_weights.items()
        }
        self._fusion_policy_version = str(fusion_policy_version)
        self._cache_ttl_seconds = max(1.0, float(cache_ttl_seconds))

        self._tpl_embeddings: Dict[IntentCategory, List[List[float]]] = {}
        self._cache: Dict[str, Tuple[float, IntentResult]] = {}
        self.cache_hits   = 0
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        bundle: Optional[AgentBundle] = None,
    ) -> IntentResult:
        """
        识别用户意图。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]
        """
        classifier_fingerprint = self.classifier_fingerprint(bundle)
        input_fingerprint = self.input_fingerprint(message, history)
        key = hashlib.sha256(
            f"{classifier_fingerprint}:{input_fingerprint}".encode("utf-8")
        ).hexdigest()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > time.monotonic():
            self.cache_hits += 1
            return cached[1]
        if cached is not None:
            self._cache.pop(key, None)
        self.cache_misses += 1

        t0 = time.monotonic()

        # LLM 和 Embedding 并行（Embedding 不可用时跳过）
        llm_task = asyncio.create_task(self._llm_recognize(message, history, bundle=bundle))
        emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        pat      = self._pattern_recognize(message)

        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}

        intent, confidence, source_scores = self._vote(llm, emb, pat)
        entities = self._extract_entities(message)
        urgency  = self._urgency(message, intent)

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            urgency=urgency,
            intent_group=self._intent_group(intent),
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
            source_scores=source_scores,
            classifier_fingerprint=classifier_fingerprint,
            input_fingerprint=input_fingerprint,
        )

        # 可删除、可重建的进程内加速层。低置信度和账户安全结果使用短 TTL；
        # 正确性由完整输入哈希和分类器指纹保证，而不是依赖手工清缓存。
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]
        ttl = self._cache_ttl_seconds
        if result.confidence < self.threshold or result.intent is IntentCategory.ACCOUNT_SECURITY:
            ttl = min(ttl, 300.0)
        self._cache[key] = (time.monotonic() + ttl, result)
        return result

    # ── 三路识别策略 ──────────────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        bundle: Optional[AgentBundle] = None,
    ) -> Dict[str, Any]:
        """策略 1：LLM 语义理解（Few-shot + 上下文）。"""
        message = self._clean_text(message)
        # 构建 Few-shot 示例和唯一业务标签合同。
        examples = "\n".join(
            f'  消息: "{t}" → 意图: {cat.value}'
            for cat, tpls in _TEMPLATES.items()
            for t in tpls[:1]  # 每类取 1 条，控制 prompt 长度
        )
        if bundle is not None:
            candidate_examples = bundle.few_shot_examples("intent")
            if candidate_examples:
                allowed_intents = {category.value for category in IntentCategory}
                examples = examples + "\n" + "\n".join(
                    f"  消息: {json.dumps(str(item.get('message') or ''), ensure_ascii=False)}"
                    f" → 意图: {str(item.get('intent') or 'other')}"
                    for item in candidate_examples
                    if isinstance(item, Mapping)
                    and str(item.get("message") or "").strip()
                    and str(item.get("intent") or "") in allowed_intents
                )
        definitions = "\n".join(
            f"  - {category.value}: {description}"
            for category, description in _INTENT_DEFINITIONS.items()
        )
        # 最近 3 轮对话上下文
        ctx = ""
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                for m in history[-3:]
            )

        policy_fragment = bundle.prompt_fragment("intent") if bundle is not None else ""
        prompt = f"""你是 DialogPilot 客服系统的意图分类器。根据业务标签合同判断用户意图，返回 JSON。
如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
例如退款优先返回 refund，发票优先返回 invoice，登录故障优先返回 technical_login。
{_INTENT_PROMPT_POLICY}
{policy_fragment}

业务标签合同:
{definitions}

示例:
{examples}

{ctx}
用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory)}"""
        prompt = self._clean_text(prompt)

        try:
            resp = await create_message(self.client, self._model_profile, ModelRole.INTENT,
                max_tokens=256,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                data["intent"] = IntentCategory(data["intent"])
            except ValueError:
                data["intent"] = IntentCategory.OTHER
            return data
        except Exception as ex:
            logger.warning(f"LLM 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0, "reasoning": "LLM 失败", "failed": True}

    async def _embedding_recognize(self, message: str) -> Dict[str, Any]:
        """策略 2：Embedding 向量相似度匹配。"""
        try:
            await self._load_template_embeddings()
            msg_vec = await self._embed_text(message)

            best_cat, best_score = IntentCategory.OTHER, 0.0
            for cat, vecs in self._tpl_embeddings.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_cat = score, cat

            return {"intent": best_cat, "confidence": best_score}
        except Exception as ex:
            logger.warning(f"Embedding 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0}

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """策略 3：输出带正向、负向、不确定/引用极性的 Pattern 证据。"""
        evidence = extract_pattern_evidence(message)
        polarity_scores = _pattern_polarity_scores(evidence)
        for specific in (True, False):
            candidates = {
                item.intent
                for item in evidence
                if item.specific is specific and polarity_scores.get(item.intent, 0.0) > 0
            }
            if candidates:
                order = _SPECIFIC_PATTERNS if specific else _GENERIC_PATTERNS
                best_cat = min(
                    candidates,
                    key=lambda intent: (-polarity_scores[intent], list(order).index(intent)),
                )
                return {
                    "intent": best_cat,
                    "confidence": polarity_scores[best_cat],
                    "polarity": EvidencePolarity.POSITIVE.value,
                    "polarity_scores": polarity_scores,
                    "evidence": evidence,
                }
        return {
            "intent": IntentCategory.OTHER,
            "confidence": 0.0,
            "polarity": EvidencePolarity.UNCERTAIN.value if evidence else "none",
            "polarity_scores": polarity_scores,
            "evidence": evidence,
        }

    # ── 投票合并 ──────────────────────────────────────────────────────────────

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> tuple[IntentCategory, float, Dict[str, float]]:
        """加权投票。返回最终意图、融合置信度和各路来源得分。"""
        source_scores = {
            "llm": float(llm.get("confidence", 0.0) or 0.0),
            "embedding": float(emb.get("confidence", 0.0) or 0.0),
            "pattern": float(pat.get("confidence", 0.0) or 0.0),
        }
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return emb["intent"], source_scores["embedding"], source_scores
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return pat["intent"], source_scores["pattern"], source_scores
            return IntentCategory.OTHER, 0.0, source_scores

        configured = self._vote_weights[self.similarity_mode]
        sources = {"llm": llm, "embedding": emb, "pattern": pat}
        scores: Dict[IntentCategory, float] = {}
        for name, w in configured.items():
            if name == "pattern" and isinstance(pat.get("polarity_scores"), dict):
                for raw_intent, signed_confidence in pat["polarity_scores"].items():
                    intent = raw_intent if isinstance(raw_intent, IntentCategory) else IntentCategory(raw_intent)
                    scores[intent] = scores.get(intent, 0.0) + w * float(signed_confidence)
                continue
            result = sources[name]
            cat  = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            scores[cat] = scores.get(cat, 0.0) + w * conf

        polarity_scores = pat.get("polarity_scores") or {}
        if polarity_scores:
            signed_values = [float(value) for value in polarity_scores.values()]
            source_scores["pattern_positive"] = max([0.0, *signed_values])
            source_scores["pattern_negative"] = max([0.0, *(-value for value in signed_values)])

        best = max(scores, key=scores.get)  # type: ignore
        best_score = scores[best]
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if (
            best in _GENERIC_INTENTS
            and pat_intent in _SPECIFIC_INTENTS
            and pat_conf >= 0.5
            and best_score < 0.8
            and (not pat.get("evidence") or pattern_supports(pat["evidence"], pat_intent))
        ):
            source_scores["refined_by_pattern"] = pat_conf
            return pat_intent, max(best_score, pat_conf), source_scores
        if best_score < self.threshold:
            return IntentCategory.OTHER, best_score, source_scores
        return best, best_score, source_scores

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    def _extract_entities(self, message: str) -> Dict[str, List[str]]:
        """用规则提取高价值实体，避免每次识别都额外调用 LLM。"""
        message = self._clean_text(message)
        return {
            "order_id": self._unique(re.findall(r"(?:订单号?|order(?:_id)?|#)\s*[:：#]?\s*([A-Za-z0-9_-]{4,32})", message, re.I)),
            "product": [],
            "date": self._unique(re.findall(r"(今天|明天|昨天|本周|这周|下周|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", message)),
            "amount": self._unique(re.findall(r"((?:¥|￥)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:元|块|rmb|cny|usd|美元))", message, re.I)),
            "error_code": self._unique(re.findall(r"\b([45]\d{2}|[A-Z][A-Z0-9_-]{2,16})\b", message)),
        }

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    async def _load_template_embeddings(self) -> None:
        """懒加载所有模板的 Embedding（只在首次调用时执行）。"""
        missing = [cat for cat in _TEMPLATES if cat not in self._tpl_embeddings]
        if not missing:
            return

        all_texts = [t for cat in missing for t in _TEMPLATES[cat]]
        vecs = [await self._embed_text(text) for text in all_texts]
        idx = 0
        for cat in missing:
            n = len(_TEMPLATES[cat])
            self._tpl_embeddings[cat] = vecs[idx: idx + n]
            idx += n

    async def _embed_text(self, text: str) -> List[float]:
        """
        生成文本向量。

        如果未来接入的官方/兼容客户端提供 embeddings.create，会优先使用远端向量；
        当前 Anthropic SDK 没有该资源时，退化为字符 n-gram 哈希向量。这样不会因为
        Embedding 服务缺失导致三路融合中断。
        """
        embeddings = getattr(self.client, "embeddings", None)
        if embeddings is not None:
            try:
                resp = await embeddings.create(model="voyage-3-lite", input=[text])
                return list(resp.data[0].embedding)
            except Exception as ex:
                logger.warning(f"远端 Embedding 失败，使用本地向量兜底: {ex}")

        return self._local_embedding(text)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """稳定的字符 n-gram 哈希向量，用于无远端 Embedding 时的语义近似匹配。"""
        normalized = text.lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        if not tokens:
            tokens.add(normalized)

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        """结合显式人工升级意图和紧急关键词计算优先级。"""
        msg = message.lower()
        for level, kws in _URGENCY_KEYWORDS.items():
            if any(kw in msg for kw in kws):
                return level
        if intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    def classifier_fingerprint(self, bundle: Optional[AgentBundle] = None) -> str:
        """哈希所有会影响分类结果的有效配置，不使用可变进程状态充当版本。"""
        payload: Dict[str, Any] = {
            "contract_version": _CLASSIFIER_CONTRACT_VERSION,
            "model_profile": {
                **self._model_profile.to_dict(),
                "provider": self._model_profile.provider,
            },
            "confidence_threshold": self.threshold,
            "similarity_mode": self.similarity_mode,
            "vote_weights": self._vote_weights[self.similarity_mode],
            "fusion_policy_version": self._fusion_policy_version,
            "prompt_policy": _INTENT_PROMPT_POLICY,
            "definitions": {
                category.value: description
                for category, description in sorted(
                    _INTENT_DEFINITIONS.items(), key=lambda item: item[0].value
                )
            },
            "templates": {
                category.value: list(values)
                for category, values in sorted(_TEMPLATES.items(), key=lambda item: item[0].value)
            },
            "specific_patterns": {
                category.value: list(values)
                for category, values in sorted(_SPECIFIC_PATTERNS.items(), key=lambda item: item[0].value)
            },
            "generic_patterns": {
                category.value: list(values)
                for category, values in sorted(_GENERIC_PATTERNS.items(), key=lambda item: item[0].value)
            },
            "pattern_polarity": {
                "negators": list(_PATTERN_NEGATORS),
                "hedges": list(_PATTERN_HEDGES),
                "quote_pairs": [list(pair) for pair in _PATTERN_QUOTE_PAIRS],
                "algebra": "positive:+w*c;negative:-w*c;uncertain_or_quoted:0",
            },
            "intent_groups": {
                category.value: group.value
                for category, group in sorted(_INTENT_GROUPS.items(), key=lambda item: item[0].value)
            },
            "urgency_keywords": {
                level.name.lower(): list(values)
                for level, values in sorted(_URGENCY_KEYWORDS.items(), key=lambda item: item[0].value)
            },
        }
        if bundle is not None:
            payload["bundle_components"] = {
                "prompts": bundle.component_hash("prompts"),
                "few_shots": bundle.component_hash("few_shots"),
            }
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def input_fingerprint(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """哈希分类器实际消费的完整消息与最近三轮，避免前缀截断造成缓存别名。"""
        payload: Dict[str, Any] = {"message": self._clean_text(message)}
        if history:
            payload["history"] = [
                {
                    "role": self._clean_text(item.get("role", "")),
                    "content": self._clean_text(item.get("content", "")),
                }
                for item in history[-3:]
            ]
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_key(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        bundle: Optional[AgentBundle] = None,
    ) -> str:
        """缓存身份由完整输入和实际分类器版本共同决定。"""
        classifier = self.classifier_fingerprint(bundle)
        inputs = self.input_fingerprint(message, history)
        return hashlib.sha256(f"{classifier}:{inputs}".encode("utf-8")).hexdigest()

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        """按首次出现顺序去重非空字符串。"""
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))

    @staticmethod
    def _best_pattern_match(
        message: str,
        patterns: Dict[IntentCategory, List[str]],
    ) -> tuple[IntentCategory, float]:
        """在关键词集合中选择命中数量最高的细粒度意图。"""
        best_cat, best_score = IntentCategory.OTHER, 0.0
        for cat, kws in patterns.items():
            hits = sum(1 for kw in kws if kw in message)
            if not hits:
                continue
            # 单个明确业务关键词就给可用置信度；多个关键词命中时提高置信度。
            score = min(1.0, 0.5 + 0.25 * (hits - 1))
            if score > best_score:
                best_score, best_cat = score, cat
        return best_cat, best_score

    @staticmethod
    def _intent_group(intent: IntentCategory) -> str:
        """把细粒度意图投影为下游兼容的领域分组。"""
        return _INTENT_GROUPS.get(intent, intent).value

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 HTTP 客户端编码 prompt 时崩溃。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        """暴露缓存容量和命中率，供健康检查与排障使用。"""
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
            "ttl_seconds": self._cache_ttl_seconds,
            "classifier_fingerprint": self.classifier_fingerprint(),
        }
