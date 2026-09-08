"""Domain-only fast routing into the existing governed delegation chain."""
import asyncio
import logging
from application.domain_encoder import DOMAIN_MINIMUM_MARGIN, DomainEncoderUnavailable
from application.encoder_fast_path import FastPathDecision
from application.encoder_input import EncoderInput
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal


class TargetEncoderUnderstanding:
    version = "target-domain-encoder-understanding-v1"

    def __init__(self, artifact):
        self._artifact = artifact

    async def __call__(self, observations, state, registry, turn_context=None):
        if (state.pending_interaction or state.pending_approval or state.active_workstreams
                or state.active_work_controls):
            return FastPathDecision(False, "ENCODER_STATE_COORDINATION_REQUIRED")
        if turn_context is not None and turn_context.summary is not None:
            return FastPathDecision(False, "ENCODER_SUMMARY_UNCALIBRATED")
        try:
            prediction = await asyncio.to_thread(self._artifact.predict,
                EncoderInput.from_turn(observations, state, turn_context))
        except DomainEncoderUnavailable:
            logging.getLogger(__name__).warning("Domain encoder inference unavailable", exc_info=True)
            return FastPathDecision(False, "ENCODER_PROVIDER_UNAVAILABLE")
        ranked = sorted(prediction.candidates, key=lambda r: (-r.score, r.candidate_id))
        top = ranked[0]
        threshold = self._artifact.manifest.thresholds.get(top.candidate_id)
        runner_up = max(prediction.defer_score, ranked[1].score if len(ranked) > 1 else 0.)
        if threshold is None or top.score < threshold or top.score - runner_up < DOMAIN_MINIMUM_MARGIN:
            return FastPathDecision(False, "ENCODER_DOMAIN_UNCERTAIN")
        if top.candidate_id not in {agent.agent_id for agent in registry.agents}:
            return FastPathDecision(False, "ENCODER_DOMAIN_UNSUPPORTED")
        # Selection is not authorization. Policy compiles read tools and scoped
        # action proposals; only the existing approval/write runtime may commit.
        command = CommandProposal(
            command_id=f"encoder:domain:{top.candidate_id}", kind=CommandKind.DELEGATE_TASK,
            target_agent=top.candidate_id, objective=observations.raw_text,
            allow_action_proposals=True,
        )
        return FastPathDecision(True, "ENCODER_DOMAIN_ACCEPTED", TurnProposal(
            ProposalDisposition.RESOLVED, (command,), "ENCODER_DOMAIN_ACCEPTED"))
