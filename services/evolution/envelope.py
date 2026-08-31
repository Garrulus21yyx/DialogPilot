"""Bad Case 的脱敏版本归因信封。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Tuple

from .bundle import AgentBundle


@dataclass(frozen=True)
class EvolutionEnvelope:
    """只记录版本、稳定哈希和生产者 ID，不记录原始 Prompt/输出/工具参数。"""

    request_id: str
    trace_id: str
    agent_bundle_version: str
    agent_bundle_hash: str
    model_policy_hash: str
    prompt_hash: str
    router_policy_hash: str
    retrieval_policy_hash: str
    tool_registry_hash: str
    producer_agent_keys: Tuple[str, ...]
    task_ids: Tuple[str, ...]
    tool_call_ids: Tuple[str, ...]
    verification: str
    badcase_group: str = ""
    observed_at: str = ""

    @classmethod
    def from_execution(
        cls,
        *,
        request_id: str,
        trace_id: str,
        bundle: AgentBundle,
        tool_registry_hash: str,
        producer_agent_keys: Iterable[str] = (),
        task_ids: Iterable[str] = (),
        tool_call_ids: Iterable[str] = (),
        verification: str,
        badcase_group: str = "",
    ) -> "EvolutionEnvelope":
        return cls(
            request_id=str(request_id)[:128],
            trace_id=str(trace_id)[:128],
            agent_bundle_version=bundle.version,
            agent_bundle_hash=bundle.content_hash,
            model_policy_hash=bundle.component_hash("model_policy"),
            prompt_hash=bundle.component_hash("prompts"),
            router_policy_hash=bundle.component_hash("routing_policy"),
            retrieval_policy_hash=bundle.component_hash("retrieval_policy"),
            tool_registry_hash=str(tool_registry_hash)[:64],
            producer_agent_keys=cls._bounded(producer_agent_keys),
            task_ids=cls._bounded(task_ids),
            tool_call_ids=cls._bounded(tool_call_ids),
            verification=str(verification)[:40],
            badcase_group=str(badcase_group)[:160],
            observed_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _bounded(values: Iterable[str]) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(str(value)[:160] for value in values if str(value)))[:100]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "agent_bundle_version": self.agent_bundle_version,
            "agent_bundle_hash": self.agent_bundle_hash,
            "model_policy_hash": self.model_policy_hash,
            "prompt_hash": self.prompt_hash,
            "router_policy_hash": self.router_policy_hash,
            "retrieval_policy_hash": self.retrieval_policy_hash,
            "tool_registry_hash": self.tool_registry_hash,
            "producer_agent_keys": list(self.producer_agent_keys),
            "task_ids": list(self.task_ids),
            "tool_call_ids": list(self.tool_call_ids),
            "verification": self.verification,
            "badcase_group": self.badcase_group,
            "observed_at": self.observed_at,
        }
