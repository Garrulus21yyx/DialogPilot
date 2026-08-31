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


# 这是 IntentCategory 的业务语义 owner。Prompt、规则和评测文档都应引用同一份
# 定义，避免把“客服领域外的一般问句”误当成 QUERY，或把安全事件误当支付失败。
_INTENT_DEFINITIONS: Dict[IntentCategory, str] = {
    IntentCategory.ORDER_STATUS: "本项目订单的处理、发货状态；不含承运商的一般信息",
    IntentCategory.LOGISTICS: "本项目订单或银行卡的寄送、到达时间、配送方式",
    IntentCategory.REFUND: "取消购买、退货退款、退款进度或退款时限",
    IntentCategory.INVOICE: "发票开具、抬头、税号或电子发票",
    IntentCategory.PAYMENT_ISSUE: "本人发起的支付失败、重复扣款、支付手续费或扣款异常",
    IntentCategory.ACCOUNT_SECURITY: "非本人交易/取现、身份验证、盗号或异常登录等安全事件",
    IntentCategory.TECHNICAL_LOGIN: "PIN、验证码、登录、卡片解锁或认证代码问题",
    IntentCategory.TECHNICAL_CRASH: "应用崩溃、闪退、HTTP 500 或明确错误码",
    IntentCategory.HUMAN_HANDOFF: "明确要求本项目人工客服或升级处理",
    IntentCategory.TECHNICAL: "银行卡、虚拟卡、非接触支付或应用功能不可用，且不属于登录/崩溃",
    IntentCategory.BILLING: "无法细分到退款、发票或支付异常的本项目账单问题",
    IntentCategory.ACCOUNT: "账户资料、地址、邮箱、销户等非安全账户管理",
    IntentCategory.QUERY: "本项目范围内但无法细分的普通信息查询",
    IntentCategory.REQUEST: "本项目范围内但无法细分的普通操作请求",
    IntentCategory.COMPLAINT: "对本项目服务表达不满，但未明确要求人工升级",
    IntentCategory.GREETING: "问候或开始对话",
    IntentCategory.ESCALATION: "投诉升级、找经理等升级诉求",
    IntentCategory.FEEDBACK: "对本项目服务的正面评价或建议",
    IntentCategory.OTHER: "项目业务范围外、语义不足或不受支持；一般知识/股票/航班/购物查询均在此类",
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


# ── Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）────────────────────────
_TEMPLATES: Mapping[IntentCategory, Tuple[str, ...]] = MappingProxyType({
    IntentCategory.QUERY: ("我的订单状态是什么？", "如何重置密码？", "快递什么时候到？"),
    IntentCategory.COMPLAINT: ("等了好几个小时！", "服务太差了！", "一直没人处理！"),
    IntentCategory.REQUEST: ("帮我取消订单", "我需要修改地址", "请协助退款"),
    IntentCategory.GREETING: ("你好", "嗨，有人吗", "早上好"),
    IntentCategory.ESCALATION: ("我要投诉！", "转人工客服", "找你们经理"),
    IntentCategory.TECHNICAL: ("应用一直崩溃", "无法登录", "出现500错误"),
    IntentCategory.BILLING: ("为什么扣了两次款？", "申请退款", "发票问题"),
    IntentCategory.ACCOUNT: ("修改邮箱", "注销账户", "更新个人信息"),
    IntentCategory.FEEDBACK: ("服务很棒！", "非常满意", "给个好评"),
    IntentCategory.ORDER_STATUS: ("我的订单现在是什么状态？", "订单有没有发货？", "订单处理到哪一步了？"),
    IntentCategory.LOGISTICS: ("快递什么时候到？", "物流一直不更新", "配送要多久？"),
    IntentCategory.REFUND: ("我要申请退款", "退货退款怎么处理？", "退款多久到账？"),
    IntentCategory.INVOICE: ("帮我开发票", "发票抬头怎么改？", "电子发票在哪里？"),
    IntentCategory.PAYMENT_ISSUE: ("为什么重复扣款？", "支付失败怎么办？", "这个月多扣了钱"),
    IntentCategory.ACCOUNT_SECURITY: ("账户被盗了", "发现异常登录", "我要重置密码"),
    IntentCategory.TECHNICAL_LOGIN: ("登录一直报401", "验证码收不到", "无法登录账号"),
    IntentCategory.TECHNICAL_CRASH: ("应用一直崩溃", "页面报500错误", "系统闪退"),
    IntentCategory.HUMAN_HANDOFF: ("转人工客服", "我要找人工", "请升级处理"),
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
        "被盗", "异常登录", "两步验证", "安全", "didn't buy", "did not make",
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

_INTENT_PROMPT_POLICY = """先判断消息是否属于本项目客服范围；范围外的一般问句必须返回 other，不能因为它是问句就返回 query。
陌生交易、非本人取现和身份验证属于 account_security；本人支付失败或手续费属于 payment_issue。
银行卡、虚拟卡、非接触支付本身不可用属于 technical；PIN、验证码和解锁属于 technical_login。"""

# 任何没有被上述数据常量表达、但会改变输出代数的实现变更都必须升级该值。
_CLASSIFIER_CONTRACT_VERSION = "intent-classifier-v2"


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
        """策略 3：关键词模式匹配（同步，零延迟兜底）。"""
        msg = message.lower()
        best_cat, best_score = self._best_pattern_match(msg, _SPECIFIC_PATTERNS)
        if best_cat != IntentCategory.OTHER:
            return {"intent": best_cat, "confidence": best_score}

        best_cat, best_score = self._best_pattern_match(msg, _GENERIC_PATTERNS)
        return {"intent": best_cat, "confidence": best_score}

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

        configured = _VOTE_WEIGHTS[self.similarity_mode]
        sources = {"llm": llm, "embedding": emb, "pattern": pat}
        weights = [(sources[name], weight) for name, weight in configured.items()]
        scores: Dict[IntentCategory, float] = {}
        for result, w in weights:
            cat  = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            scores[cat] = scores.get(cat, 0.0) + w * conf

        best = max(scores, key=scores.get)  # type: ignore
        best_score = scores[best]
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if best in _GENERIC_INTENTS and pat_intent in _SPECIFIC_INTENTS and pat_conf >= 0.5 and best_score < 0.8:
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
            "vote_weights": _VOTE_WEIGHTS[self.similarity_mode],
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
