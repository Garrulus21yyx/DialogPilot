"""不可变 AgentBundle 合同及其稳定内容哈希。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence, Tuple

from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY


class BundleContractError(ValueError):
    """候选 Bundle 试图越过可进化配置面或值域不合法。"""


_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_AGENTS = {
    "*", "intent", "general", "technical", "billing", "account_security", "escalation",
}
_ROUTING_KEYS = {"supporting_threshold", "clarification_threshold"}
_RETRIEVAL_KEYS = {
    "top_k", "candidate_k", "context_max_tokens", "rrf_k",
    "vector_weight", "lexical_weight", "raw_query_weight", "standalone_query_weight",
}
_PROHIBITED = re.compile(
    r"permission|allowlist|approval|jwt|auth|secret|pii|redact|verifier|fail.?closed|gold",
    re.IGNORECASE,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise BundleContractError(f"bundle value is not JSON-compatible: {type(value).__name__}")


def stable_hash(value: Any) -> str:
    """对 JSON 语义而非 Python 对象身份生成稳定 SHA-256。"""
    return hashlib.sha256(_canonical(_thaw(value)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentBundle:
    """一次请求必须完整固定的可进化配置集合。

    权限、审批、身份、脱敏、Verifier 和 Gold 状态不属于该集合；这些事实仍由
    各自生产 Owner 持有，候选生成器没有写权。
    """

    version: str
    base_version: str = ""
    prompts: Mapping[str, str] = field(default_factory=dict)
    few_shots: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    routing_policy: Mapping[str, Any] = field(default_factory=dict)
    retrieval_policy: Mapping[str, Any] = field(default_factory=dict)
    tool_descriptions: Mapping[str, str] = field(default_factory=dict)
    model_policy: Mapping[str, Any] = field(default_factory=dict)
    source_badcase_groups: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        version = str(self.version).strip()
        base = str(self.base_version).strip()
        if not _VERSION.fullmatch(version):
            raise BundleContractError("version must be 1-128 URL-safe characters")
        if base and not _VERSION.fullmatch(base):
            raise BundleContractError("base_version must be empty or URL-safe")
        self._validate_surface("prompts", self.prompts)
        self._validate_surface("few_shots", self.few_shots)
        self._validate_surface("routing_policy", self.routing_policy)
        self._validate_surface("retrieval_policy", self.retrieval_policy)
        self._validate_surface("tool_descriptions", self.tool_descriptions)
        self._validate_surface("model_policy", self.model_policy)

        prompts = {str(key): str(value).strip() for key, value in self.prompts.items()}
        if set(prompts) - _AGENTS:
            raise BundleContractError(f"unsupported prompt owners: {sorted(set(prompts) - _AGENTS)}")
        if any(len(value) > 12000 for value in prompts.values()):
            raise BundleContractError("prompt fragment exceeds 12000 characters")

        routing = dict(self.routing_policy)
        unknown_routing = set(routing) - _ROUTING_KEYS
        if unknown_routing:
            raise BundleContractError(f"unsupported routing policy keys: {sorted(unknown_routing)}")
        for key in _ROUTING_KEYS:
            if key in routing and not 0.0 <= float(routing[key]) <= 1.0:
                raise BundleContractError(f"{key} must be between 0 and 1")

        retrieval = dict(self.retrieval_policy)
        unknown_retrieval = set(retrieval) - _RETRIEVAL_KEYS
        if unknown_retrieval:
            raise BundleContractError(f"unsupported retrieval policy keys: {sorted(unknown_retrieval)}")
        if "top_k" in retrieval and not 1 <= int(retrieval["top_k"]) <= 20:
            raise BundleContractError("retrieval top_k must be between 1 and 20")
        if "candidate_k" in retrieval and not 1 <= int(retrieval["candidate_k"]) <= 100:
            raise BundleContractError("retrieval candidate_k must be between 1 and 100")
        if (
            "candidate_k" in retrieval and "top_k" in retrieval
            and int(retrieval["candidate_k"]) < int(retrieval["top_k"])
        ):
            raise BundleContractError("retrieval candidate_k must be at least top_k")
        if "context_max_tokens" in retrieval and not 128 <= int(retrieval["context_max_tokens"]) <= 12000:
            raise BundleContractError("retrieval context_max_tokens must be between 128 and 12000")
        if "rrf_k" in retrieval and not 1 <= int(retrieval["rrf_k"]) <= 1000:
            raise BundleContractError("retrieval rrf_k must be between 1 and 1000")
        weights = [float(retrieval.get(key, 0.0)) for key in ("vector_weight", "lexical_weight")]
        if any(weight < 0 for weight in weights):
            raise BundleContractError("retrieval weights must be non-negative")
        if any(key in retrieval for key in ("vector_weight", "lexical_weight")) and sum(weights) <= 0:
            raise BundleContractError("retrieval weights must not both be zero")
        query_weights = [
            float(retrieval.get(key, 0.0))
            for key in ("raw_query_weight", "standalone_query_weight")
        ]
        if any(weight < 0 for weight in query_weights):
            raise BundleContractError("query weights must be non-negative")
        if any(key in retrieval for key in ("raw_query_weight", "standalone_query_weight")) and sum(query_weights) <= 0:
            raise BundleContractError("query weights must not both be zero")

        descriptions = {str(key).strip(): str(value).strip() for key, value in self.tool_descriptions.items()}
        if any(not key or not value for key, value in descriptions.items()):
            raise BundleContractError("tool description names and values must not be empty")
        if any(len(value) > 4000 for value in descriptions.values()):
            raise BundleContractError("tool description exceeds 4000 characters")

        groups = tuple(dict.fromkeys(str(item).strip() for item in self.source_badcase_groups if str(item).strip()))
        if len(groups) > 100 or any(len(item) > 160 for item in groups):
            raise BundleContractError("source_badcase_groups exceeds bounded contract")

        object.__setattr__(self, "version", version)
        object.__setattr__(self, "base_version", base)
        object.__setattr__(self, "prompts", _freeze(prompts))
        object.__setattr__(self, "few_shots", _freeze(dict(self.few_shots)))
        object.__setattr__(self, "routing_policy", _freeze(routing))
        object.__setattr__(self, "retrieval_policy", _freeze(retrieval))
        object.__setattr__(self, "tool_descriptions", _freeze(descriptions))
        object.__setattr__(self, "model_policy", _freeze(dict(self.model_policy)))
        object.__setattr__(self, "source_badcase_groups", groups)

    @staticmethod
    def _validate_surface(name: str, value: Mapping[str, Any]) -> None:
        if not isinstance(value, Mapping):
            raise BundleContractError(f"{name} must be a mapping")
        prohibited = [str(key) for key in value if _PROHIBITED.search(str(key))]
        if prohibited:
            raise BundleContractError(f"security-owned fields cannot evolve: {sorted(prohibited)}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "base_version": self.base_version,
            "prompts": _thaw(self.prompts),
            "few_shots": _thaw(self.few_shots),
            "routing_policy": _thaw(self.routing_policy),
            "retrieval_policy": _thaw(self.retrieval_policy),
            "tool_descriptions": _thaw(self.tool_descriptions),
            "model_policy": _thaw(self.model_policy),
            "source_badcase_groups": list(self.source_badcase_groups),
        }

    @property
    def content_hash(self) -> str:
        return stable_hash(self.to_dict())

    def component_hash(self, component: str) -> str:
        payload = self.to_dict()
        if component not in payload:
            raise KeyError(component)
        return stable_hash(payload[component])

    def prompt_fragment(self, agent_type: str) -> str:
        pieces = [str(self.prompts.get("*", "")).strip(), str(self.prompts.get(agent_type, "")).strip()]
        return "\n".join(piece for piece in pieces if piece)

    def few_shot_examples(self, agent_type: str) -> Tuple[Mapping[str, Any], ...]:
        values = [*self.few_shots.get("*", ()), *self.few_shots.get(agent_type, ())]
        return tuple(values)


def build_default_bundle(
    model_policy: Mapping[str, Any],
    retrieval_policy: Mapping[str, Any] = DEFAULT_RAG_RETRIEVAL_POLICY,
) -> AgentBundle:
    """用当前生产默认值建立只读 bootstrap Bundle。"""
    routing_policy = {"supporting_threshold": 0.45, "clarification_threshold": 0.5}
    bootstrap_version = "agent-v2-rag-" + stable_hash({
        "routing_policy": routing_policy,
        "retrieval_policy": dict(retrieval_policy),
        "model_policy": dict(model_policy),
    })[:12]
    return AgentBundle(
        version=bootstrap_version,
        routing_policy=routing_policy,
        retrieval_policy=dict(retrieval_policy),
        model_policy=dict(model_policy),
    )
