"""按调用角色拥有模型与推理强度配置。

传输协议仍统一使用 Anthropic Messages API；本模块只拥有“哪个角色使用
哪个模型、是否开启推理”这一事实，业务组件不得再从全局模型名自行猜测。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class ModelRole(str, Enum):
    """系统内具有独立成本/质量权衡的模型调用角色。"""

    INTENT = "intent"
    WORKER = "worker"
    REACT = "react"
    SYNTHESIS = "synthesis"
    VERIFIER = "verifier"
    JUDGE = "judge"
    MEMORY = "memory"
    REWRITE = "rewrite"
    RERANK = "rerank"


class ReasoningEffort(str, Enum):
    """DeepSeek Anthropic 兼容端点支持的闭合推理强度集合。"""

    NONE = "none"
    LOW = "low"
    HIGH = "high"
    MAX = "max"


@dataclass(frozen=True)
class ModelProfile:
    """单个角色的不可变模型调用合同。"""

    model: str
    reasoning: ReasoningEffort = ReasoningEffort.NONE
    provider: str = "anthropic"
    min_completion_tokens: int = 0

    def __post_init__(self) -> None:
        model = self.model.strip()
        provider = self.provider.strip().lower()
        if not model:
            raise ValueError("model must not be empty")
        if provider not in {"anthropic", "deepseek"}:
            raise ValueError(f"unsupported MODEL_PROVIDER: {self.provider}")
        if provider == "deepseek" and model not in {
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        }:
            raise ValueError(f"unsupported DeepSeek model: {model}")
        if provider != "deepseek" and self.reasoning is not ReasoningEffort.NONE:
            raise ValueError("role reasoning controls currently require MODEL_PROVIDER=deepseek")
        if self.min_completion_tokens < 0 or self.min_completion_tokens > 8192:
            raise ValueError("min_completion_tokens must be between 0 and 8192")
        if self.reasoning is not ReasoningEffort.NONE and self.min_completion_tokens < 256:
            raise ValueError("reasoning profiles require min_completion_tokens >= 256")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "provider", provider)

    def request(self, **payload: Any) -> Dict[str, Any]:
        """生成一次 Messages 请求并显式覆盖 DeepSeek 默认 thinking 行为。"""
        request = {"model": self.model, **payload}
        if self.provider != "deepseek":
            return request
        if self.reasoning is ReasoningEffort.NONE:
            request["extra_body"] = {"thinking": {"type": "disabled"}}
            return request
        # Thinking 模式下 temperature 不生效，移除可避免配置看似有效却被忽略。
        request.pop("temperature", None)
        request["max_tokens"] = max(
            int(request.get("max_tokens", 0)),
            self.min_completion_tokens,
        )
        request["extra_body"] = {
            "thinking": {"type": "enabled"},
            "output_config": {"effort": self.reasoning.value},
        }
        return request

    def to_dict(self) -> Dict[str, str]:
        """返回不含密钥的运行时投影。"""
        return {
            "model": self.model,
            "reasoning": self.reasoning.value,
            "min_completion_tokens": self.min_completion_tokens,
        }


@dataclass(frozen=True)
class ModelPolicy:
    """所有角色配置的唯一 Owner，并提供环境变量解析与启动期校验。"""

    provider: str
    base_url: Optional[str]
    profiles: Mapping[ModelRole, ModelProfile]

    _DEEPSEEK_DEFAULTS = {
        ModelRole.INTENT: ("deepseek-v4-flash", ReasoningEffort.NONE, 0),
        ModelRole.WORKER: ("deepseek-v4-flash", ReasoningEffort.NONE, 0),
        ModelRole.REACT: ("deepseek-v4-flash", ReasoningEffort.NONE, 0),
        ModelRole.SYNTHESIS: ("deepseek-v4-pro", ReasoningEffort.NONE, 0),
        ModelRole.VERIFIER: ("deepseek-v4-pro", ReasoningEffort.NONE, 0),
        ModelRole.JUDGE: ("deepseek-v4-pro", ReasoningEffort.NONE, 0),
        ModelRole.MEMORY: ("deepseek-v4-flash", ReasoningEffort.NONE, 0),
        ModelRole.REWRITE: ("deepseek-v4-flash", ReasoningEffort.NONE, 0),
        ModelRole.RERANK: ("deepseek-v4-flash", ReasoningEffort.NONE, 0),
    }

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ModelPolicy":
        """读取分层配置；DeepSeek 角色均有显式模型和 thinking 默认值。"""
        source = os.environ if env is None else env
        provider = source.get("MODEL_PROVIDER", "anthropic").strip().lower()
        if provider not in {"anthropic", "deepseek"}:
            raise ValueError(f"unsupported MODEL_PROVIDER: {provider}")
        base_url = source.get("ANTHROPIC_BASE_URL", "").strip() or None
        legacy_model = source.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip()
        profiles: Dict[ModelRole, ModelProfile] = {}
        for role in ModelRole:
            if provider == "deepseek":
                default_model, default_reasoning, default_min_tokens = cls._DEEPSEEK_DEFAULTS[role]
            else:
                default_model, default_reasoning = legacy_model, ReasoningEffort.NONE
                default_min_tokens = 0
            model = source.get(f"MODEL_{role.value.upper()}", default_model).strip()
            raw_reasoning = source.get(
                f"MODEL_{role.value.upper()}_REASONING",
                default_reasoning.value,
            ).strip().lower()
            try:
                reasoning = ReasoningEffort(raw_reasoning)
            except ValueError as exc:
                raise ValueError(
                    f"unsupported reasoning effort for {role.value}: {raw_reasoning}"
                ) from exc
            raw_min_tokens = source.get(
                f"MODEL_{role.value.upper()}_MIN_COMPLETION_TOKENS",
                str(default_min_tokens),
            ).strip()
            try:
                min_tokens = int(raw_min_tokens)
            except ValueError as exc:
                raise ValueError(
                    f"invalid completion token floor for {role.value}: {raw_min_tokens}"
                ) from exc
            profiles[role] = ModelProfile(model, reasoning, provider, min_tokens)
        if provider == "deepseek":
            base_url = base_url or "https://api.deepseek.com/anthropic"
        return cls(provider=provider, base_url=base_url, profiles=profiles)

    @classmethod
    def legacy(cls, model: str, base_url: Optional[str] = None) -> "ModelPolicy":
        """兼容直接构造组件的调用方；不根据 URL 猜测供应商。"""
        profile = ModelProfile(model=model)
        return cls("anthropic", base_url, {role: profile for role in ModelRole})

    def profile(self, role: ModelRole) -> ModelProfile:
        """返回已通过启动期校验的角色配置。"""
        return self.profiles[role]

    def to_dict(self) -> Dict[str, Any]:
        """输出健康检查和评测报告可安全记录的实际配置。"""
        return {
            "provider": self.provider,
            "base_url": self.base_url or "official",
            "roles": {role.value: self.profile(role).to_dict() for role in ModelRole},
        }
