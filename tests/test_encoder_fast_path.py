from application.authority_policy import AuthoritySupport, FactRequirement, RequirementEffect
from application.capability_registry import (
    AgentDefinition,
    CapabilityEffect,
    CapabilityRegistryBundle,
    CapabilityRisk,
    SkillDefinition,
    ToolDefinition,
    VerificationProfile,
)
from application.conversation_state import (
    ConversationState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.encoder_fast_path import (
    EncoderFastPathPolicy,
    IntentEncoderOutput,
    MediaAssetSignal,
    MediaEvidenceAction,
    RankedCandidate,
    UnderstandingEvidencePolicy,
)
from application.turn_planning import CommandKind


def _requirement(requirement_id, effect, tool_id):
    return FactRequirement(
        requirement_id,
        requirement_id,
        ("value",),
        60 if effect is RequirementEffect.READ else None,
        effect,
        (tool_id,),
        (),
        "receipt-v1" if effect is RequirementEffect.WRITE else "",
        AuthoritySupport.SUPPORTED,
        "owner-v1",
    )


def _registry():
    profile = VerificationProfile("default", "v1", ("authority", "coverage"))
    return CapabilityRegistryBundle(
        "tenant-a",
        "customer-service-v1",
        (AgentDefinition(
            "billing_refund",
            "v1",
            ("refund_status", "refund_create"),
            ("refund_status_summary", "execute_refund_skill"),
            "refund-model-v1",
            "refund-context-v1",
            profile.ref,
        ),),
        (
            SkillDefinition(
                "refund_status_summary",
                "v1",
                "billing_refund",
                "Query refund status",
                ("order_id",),
                (),
                ("refund.current_state",),
                ("refund_status",),
                CapabilityEffect.READ,
                CapabilityRisk.MEDIUM,
                profile.ref,
            ),
            SkillDefinition(
                "execute_refund_skill",
                "v1",
                "billing_refund",
                "Create refund",
                ("order_id",),
                (),
                ("refund.request_action",),
                ("refund_create",),
                CapabilityEffect.WRITE,
                CapabilityRisk.HIGH,
                profile.ref,
            ),
        ),
        (),
        (),
        (
            _requirement("refund.current_state", RequirementEffect.READ, "refund_status"),
            _requirement("refund.request_action", RequirementEffect.WRITE, "refund_create"),
        ),
        (
            ToolDefinition(
                "refund_status", "v1", "input-v1", "output-v1",
                CapabilityEffect.READ, CapabilityRisk.MEDIUM,
                "refund.current_state", profile.ref,
            ),
            ToolDefinition(
                "refund_create", "v1", "input-v1", "output-v1",
                CapabilityEffect.WRITE, CapabilityRisk.HIGH,
                "refund.request_action", profile.ref, "receipt-v1",
            ),
        ),
        (profile,),
    )


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _output(skill="refund_status_summary", score=0.98, **changes):
    values = {
        "domains": (RankedCandidate("billing_refund", 0.99),),
        "skills": (RankedCandidate(skill, score),),
        "entities": (("order_id", "DP1234"),),
        "boundary_score": 0.03,
        "multi_intent": False,
        "out_of_distribution": False,
    }
    values.update(changes)
    return IntentEncoderOutput(**values)


def _policy():
    return EncoderFastPathPolicy(skill_thresholds={
        "refund_status_summary": 0.95,
        "execute_refund_skill": 0.99,
    })


def test_calibrated_read_skill_fast_path_produces_registry_backed_command():
    decision = _policy().decide(_output(), _state(), _registry())

    assert decision.accepted is True
    command = decision.proposal.commands[0]
    assert command.kind is CommandKind.RUN_SKILL
    assert command.target_agent == "billing_refund"
    assert command.arguments[0].value == "DP1234"


def test_fast_path_defers_low_margin_boundary_and_multi_intent_cases():
    policy = _policy()
    registry = _registry()
    state = _state()

    low_margin = _output(skills=(
        RankedCandidate("refund_status_summary", 0.98),
        RankedCandidate("other", 0.93),
    ))
    assert policy.decide(low_margin, state, registry).reason_code == "ENCODER_LOW_CONFIDENCE"
    assert policy.decide(_output(boundary_score=0.4), state, registry).accepted is False
    assert policy.decide(_output(multi_intent=True), state, registry).accepted is False


def test_fast_path_defers_write_and_any_active_conversation_binding():
    registry = _registry()
    write = _policy().decide(
        _output(skill="execute_refund_skill", score=1.0), _state(), registry,
    )
    assert write.reason_code == "ENCODER_WRITE_DEFERRED"

    active = _state().start_workstream(WorkstreamState(
        "refund-ws-1", "billing_refund", "refund_status_summary:v1",
        "ACTIVE", WorkstreamStatus.ACTIVE, 1,
    ))
    state_bound = _policy().decide(_output(), active, registry)
    assert state_bound.reason_code == "ENCODER_STATE_CONFLICT"


def test_fast_path_requires_every_registered_skill_argument():
    decision = _policy().decide(
        _output(entities=()), _state(), _registry(),
    )
    assert decision.reason_code == "ENCODER_REQUIRED_ARGUMENT_MISSING"


def test_understanding_memory_supplement_is_bounded_to_one_attempt():
    policy = UnderstandingEvidencePolicy()
    first = policy.plan(
        unresolved_historical_reference=True,
        memory_attempt=0,
        media_assets=(),
    )
    second = policy.plan(
        unresolved_historical_reference=True,
        memory_attempt=first.next_memory_attempt,
        media_assets=(),
    )

    assert first.request_memory is True
    assert first.next_memory_attempt == 1
    assert second.request_memory is False


def test_media_policy_reuses_evidence_then_chooses_ocr_or_vlm_by_need():
    plan = UnderstandingEvidencePolicy().plan(
        unresolved_historical_reference=False,
        memory_attempt=0,
        media_assets=(
            MediaAssetSignal("IMG1", "image/png", reusable_evidence_ref="media:IMG1:v1"),
            MediaAssetSignal("IMG2", "image/png", text_extraction_sufficient=True),
            MediaAssetSignal("IMG3", "image/png", semantic_visual_reasoning_required=True),
        ),
    )

    assert [item.action for item in plan.media_steps] == [
        MediaEvidenceAction.REUSE,
        MediaEvidenceAction.OCR,
        MediaEvidenceAction.VLM,
    ]
    assert plan.media_steps[0].evidence_ref == "media:IMG1:v1"

