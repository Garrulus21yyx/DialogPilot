"""Domain acceptance delegates understanding, not tool execution or authorization."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.capability_registry import CapabilityEffect
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.domain_encoder import DOMAINS, DomainPrediction, DomainEncoderManifest, DomainEncoderUnavailable
from application.encoder_fast_path import RankedCandidate
from application.target_conversation_manager import TargetContextMessage, TargetTurnContext
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import CommandKind, RoutePolicy, TurnPlanCompiler, ProposalDisposition, TurnProposal
from application.work_item import ControlMode
from core.identity import IdentityFactory


class Artifact:
    def __init__(self, owner="billing_refund", score=.995, defer=.001):
        self.manifest = SimpleNamespace(thresholds={k: .98 for k in DOMAINS})
        self.prediction = DomainPrediction(tuple(RankedCandidate(k, score if k == owner else .001) for k in DOMAINS), defer)
        self.inputs = []

    def predict(self, value):
        self.inputs.append(value)
        return self.prediction


def setup():
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    registry = build_default_capability_registry("t")
    invocation = IdentityFactory(lambda: "fixed").create_invocation(
        tenant_id="t", user_id="u", conversation_id="c", request_id="r")
    return state, registry, invocation


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_domain_compiles_open_delegation_without_parameters_or_write_authority(domain):
    state, registry, invocation = setup()
    raw = "先查情况；不要提交。另一个同领域问题也请解释。"
    artifact = Artifact(domain)
    decision = asyncio.run(TargetEncoderUnderstanding(artifact)(TurnObservations(raw), state, registry))
    assert decision.accepted
    command, = decision.proposal.commands
    assert command.kind is CommandKind.DELEGATE_TASK
    assert command.target_agent == domain and command.objective == raw
    assert not command.arguments and not command.requirement_ids
    assert command.tool_id is command.skill_id is command.action_ref is command.approval_binding is None
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(decision.proposal, state, registry), state, registry, invocation)
    item, = plan.work.items
    assert item.control_mode is ControlMode.DELEGATED
    assert item.objective == raw
    assert all(registry.tool(t).effect is CapabilityEffect.READ for t in item.allowed_tools)
    assert all(next(a for a in registry.actions if a.ref == ref).owner_agent == domain for ref in item.allowed_actions)


def test_history_and_current_user_limits_are_preserved_without_query_authoring():
    state, registry, _ = setup()
    history = (TargetContextMessage("user", "请解释退款政策，不要提交。", "m1"),
               TargetContextMessage("assistant", "想先了解运费吗？", "m2"))
    context = TargetTurnContext(recent_messages=history)
    artifact = Artifact()
    decision = asyncio.run(TargetEncoderUnderstanding(artifact)(TurnObservations("是，另外说下退款时间。"), state, registry, context))
    assert decision.accepted
    assert artifact.inputs[0].messages == tuple((m.role, m.content) for m in history)
    assert decision.proposal.commands[0].objective == "是，另外说下退款时间。"
    # Runtime context stays available to the worker; not replaced by a class label.
    assert context.recent_relevant_turns == tuple(f"{m.role}: {m.content}" for m in history)


@pytest.mark.parametrize("field", ["pending_interaction", "pending_approval", "active_workstreams", "active_work_controls"])
def test_state_coordination_precedes_classifier(field):
    values = dict(pending_interaction=None, pending_approval=None, active_workstreams=(), active_work_controls=())
    values[field] = object()
    artifact = Artifact()
    result = asyncio.run(TargetEncoderUnderstanding(artifact)(TurnObservations("继续"), SimpleNamespace(**values), setup()[1]))
    assert not result.accepted and not artifact.inputs


@pytest.mark.parametrize("score,defer,accepted", [(.97, .01, False), (.99, .99, False), (.7, .69, False), (.995, .001, True)])
def test_cascade_routes_once_without_duplicate_global_planning(score, defer, accepted):
    state, registry, _ = setup()
    artifact = Artifact(score=score, defer=defer)
    class Planner:
        calls = 0
        async def plan(self, *args):
            self.calls += 1
            return TurnProposal(ProposalDisposition.RESPOND, (), "CONVERSATION_AGENT_PLAN", response_text="请说明要处理的事项。")
    planner = Planner()
    obs = TurnObservations("先查退款，再查配送")
    result = asyncio.run(CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), planner,
        encoder=TargetEncoderUnderstanding(artifact))(obs, state, DeterministicResolver().resolve(obs, state), registry, TargetTurnContext()))
    assert planner.calls == (0 if accepted else 1)
    assert result.reason_code == ("ENCODER_DOMAIN_ACCEPTED" if accepted else "CONVERSATION_AGENT_PLAN")


def test_inference_unavailable_is_distinct_from_semantic_uncertainty(caplog):
    artifact = Artifact()
    def fail(value):
        raise DomainEncoderUnavailable("test dependency unavailable")
    artifact.predict = fail
    state, registry, _ = setup()
    result = asyncio.run(TargetEncoderUnderstanding(artifact)(TurnObservations("查询"), state, registry))
    assert result.reason_code == "ENCODER_PROVIDER_UNAVAILABLE"
    assert "test dependency unavailable" in caplog.text


def test_old_action_artifact_is_not_a_domain_artifact():
    import json
    raw = json.loads(Path("artifacts/target-encoder-zh-context-v2/manifest.json").read_text())
    with pytest.raises(ValueError, match="legacy action artifacts"):
        DomainEncoderManifest.from_dict(raw)


@pytest.mark.parametrize("status", ["CANDIDATE", "REJECTED"])
def test_unqualified_weights_cannot_be_loaded_by_production(status):
    import json
    raw = json.loads(Path("artifacts/target-domain-encoder-zh-v1/manifest.json").read_text())
    raw["status"] = status
    with pytest.raises(ValueError, match="not an active"):
        DomainEncoderManifest.from_dict(raw)
    assert DomainEncoderManifest.from_dict(raw, evaluation=True).status == status


@pytest.mark.parametrize("changed", ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt"])
def test_model_input_changes_invalidate_artifact(tmp_path, changed):
    import json
    import shutil
    from infrastructure.target_domain_encoder import MODEL_INPUT_FILES, validate_model_inputs
    source = Path("artifacts/target-domain-encoder-zh-v1")
    manifest = json.loads((source / "manifest.json").read_text())
    for name in MODEL_INPUT_FILES:
        shutil.copyfile(source / name, tmp_path / name)
    validate_model_inputs(tmp_path, manifest)
    with (tmp_path / changed).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_model_inputs(tmp_path, manifest)


def test_reordered_label_semantics_are_not_accepted_even_with_new_digest(tmp_path):
    import json
    import shutil
    from infrastructure.target_domain_encoder import MODEL_INPUT_FILES, model_input_digests, validate_model_inputs
    source = Path("artifacts/target-domain-encoder-zh-v1")
    for name in MODEL_INPUT_FILES:
        shutil.copyfile(source / name, tmp_path / name)
    path = tmp_path / "config.json"
    config = json.loads(path.read_text())
    config["id2label"]["1"], config["id2label"]["2"] = config["id2label"]["2"], config["id2label"]["1"]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="label order"):
        validate_model_inputs(tmp_path, {"input_files_sha256": model_input_digests(tmp_path)})


def test_compiled_domain_task_reaches_worker_with_original_context():
    from application.agent_result import AgentResult, AgentResultStatus
    from application.orchestration_runtime import OrchestrationRuntime
    state, registry, invocation = setup()
    raw = "是，先只查，不要申请。"
    history = ("user: 之前问的是退款条件。", "assistant: 还想了解运费吗？")
    seen = []
    async def worker(context):
        seen.append(context)
        return AgentResult(context.work_item.work_item_id, "billing_refund",
            AgentResultStatus.SUCCEEDED, "FIXTURE_COMPLETE", "test-v1")
    async def run():
        decision = await TargetEncoderUnderstanding(Artifact())(TurnObservations(raw), state, registry)
        plan = TurnPlanCompiler().compile(RoutePolicy().accept(decision.proposal, state, registry), state, registry, invocation)
        await OrchestrationRuntime(direct_executor=worker, domain_workers={"billing_refund": worker}).execute(
            plan.work, current_message=raw, recent_relevant_turns=history)
    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].current_message == seen[0].work_item.objective == raw
    assert seen[0].recent_relevant_turns == history


def test_domain_dataset_family_and_parent_isolation():
    import json
    from application.encoder_input import EncoderInput
    for language in ("zh", "en"):
        groups, inputs = {}, set()
        for split in ("train", "calibration", "heldout"):
            rows = list(map(json.loads, Path(f"data/training/domain-encoder-v1/{language}/{split}.jsonl").read_text().splitlines()))
            ids = {r["case_id"] for r in rows}
            assert len(ids) == len(rows)
            for row in rows:
                assert groups.setdefault(row["group_id"], split) == split
                assert set(row.get("source_case_ids", ())) <= ids
                identity = EncoderInput.from_record(row).identity()
                assert identity not in inputs
                inputs.add(identity)
