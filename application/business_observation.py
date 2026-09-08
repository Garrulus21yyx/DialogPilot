"""Private, source-backed historical observations; never new action authority."""
from typing import Any, Literal
from dataclasses import asdict, dataclass
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from application.agent_result import AgentResultStatus, FactRecord, ReceiptRef
from application.capability_registry import CapabilityEffect


@dataclass(frozen=True)
class BusinessReceipt(ReceiptRef):
    action: dict[str, Any] | None = None


def receipt_context(item, receipt):
    """Associate action terms only with the operation that produced this receipt."""
    return {**asdict(receipt), "action": {
        "work_item_id": item.work_item_id, "action_ref": item.action_ref,
        "target_entity_ref": item.aggregate_ref,
        "arguments": {arg.name: arg.value for arg in item.arguments},
    } if item is not None and item.operation_key == receipt.operation_key else None}


class WriteRecoveryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: Literal["write_recovery"]
    operation_key: str
    status: Literal["MANUAL_REVIEW"]
    reason: str
    ticket_id: str | None
    recovery_attempts: int
    business_outcome: Literal["NOT_COMMITTED", "UNCONFIRMED"]
    last_outcome: str | None
    outcome_scope: str | None
    detail: str | None
    source_ref: str | None


def business_observation_context(observations):
    if not observations:
        return {}
    return {"business_observations": list(observations),
            "business_observation_contract": (
                "HISTORICAL observations preserve actual prior tool facts and receipts, even when "
                "the prior reply failed verification. Use them to understand and explain completed work; "
                "do not deny that data was obtained or repeat a lookup merely because this turn has no tools. "
                "Preserve the original subject, source, observed_at and valid_until. These are not a "
                "fresh business snapshot, approval, or new action prerequisite. Newer observations and "
                "committed mutations may supersede earlier values; refresh only when the current goal "
                "requires freshness or missing coverage. Preserve coverage restrictions: a fact in a "
                "conflict-affected or incomplete outcome is not an established conclusion merely because "
                "its worker returned SUCCEEDED. Recovery observations describe the recorded operation "
                "only: NOT_COMMITTED is not UNCONFIRMED, and neither grants retry or action approval. "
                "INVALID entries provide no factual support."
            )}


class BusinessObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["business-observation-v1"] = "business-observation-v1"
    work_item_id: str = Field(min_length=1)
    owner_agent: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    status: AgentResultStatus
    coverage: dict[str, Any]
    action_ref: str | None = None
    aggregate_ref: str | None = None
    facts: tuple[FactRecord, ...] = ()
    receipts: tuple[BusinessReceipt, ...] = ()
    write_recovery: tuple[WriteRecoveryObservation, ...] = ()

    @property
    def observation_id(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


def capture_business_observations(board):
    """Capture governed outputs regardless of whether reply wording passed review."""
    if board is None:
        return ()
    observations = []
    for item, result in board.outcome_items:
        if result is None:
            continue
        facts = tuple(fact for fact in result.facts if not fact.requirement_id.startswith("knowledge."))
        recovery = tuple(WriteRecoveryObservation.model_validate(feedback)
            for feedback in result.execution_feedback
            if item.effect is CapabilityEffect.WRITE and item.operation_key
            and feedback.get("stage") == "write_recovery"
            and feedback.get("operation_key") == item.operation_key)
        if not facts and not result.action_receipts and not recovery:
            continue
        observation = BusinessObservation(work_item_id=item.work_item_id,
            owner_agent=item.owner_agent, objective=item.objective, status=result.status,
            coverage=board.coverage_for(item, result),
            action_ref=item.action_ref, aggregate_ref=item.aggregate_ref,
            facts=facts, receipts=tuple(BusinessReceipt(**receipt_context(item, receipt))
                                      for receipt in result.action_receipts),
            write_recovery=recovery).model_dump(mode="json")
        if observation not in observations:
            observations.append(observation)
    return tuple(observations)
