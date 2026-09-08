"""Deterministic approval uses the host history view, without global planning."""
import asyncio
import json
from dataclasses import replace

from langgraph.checkpoint.memory import InMemorySaver

from application.context_budget import ContextBudgetManager
from application.conversation_evidence import ConversationEvidence
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_conversation_manager import TargetConversationManager
from application.turn_runtime import TurnRuntime
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from infrastructure.target_turn_context import TargetTurnContextLoader
from tests.test_historical_context_budget import historical
from tests.test_knowledge_answer_boundary import Verifier
from tests.test_target_turn_context import Memory, Tools
from tests.test_target_persistence_and_manager import (
    _ResumeAwareUnderstanding, _prepare_workflow_proposal, _identity,
    _OrderCancellationPreparationExecutor, _OrderCancellationWorkflowExecutor,
)


def test_approved_operation_and_reply_do_not_reload_oversized_history_or_repeat_submit():
    _, entry = historical()
    class Reader:
        def load(self, invocation):
            return ConversationEvidence(business=(entry,))
    class Workflow(_OrderCancellationWorkflowExecutor):
        async def __call__(self, context):
            return replace(await super().__call__(context), candidate_response='Cancellation committed.')
    async def run():
        registry = build_default_capability_registry('tenant-target')
        workflow = Workflow()
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(), registry=registry,
            context_provider=TargetTurnContextLoader(Memory(), Tools(), evidence_reader=Reader(),
                historical_context_budget=ContextBudgetManager()),
            understanding=_ResumeAwareUnderstanding(_prepare_workflow_proposal(
                command_id='prepare-cancel', owner='order_logistics', objective='Cancel DP1234',
                arguments=(('order_id','DP1234'),), requirement_id='order.current_state',
                flow_ref='cancel_order:v1', action_ref='order.cancel:v1', target_entity_ref='order:DP1234')),
            orchestration=OrchestrationRuntime(direct_executor=_OrderCancellationPreparationExecutor(),
                domain_workers={}, workflow_executor=workflow, checkpointer=saver))
        prepared = await manager.handle(_identity('prepare-history'), TurnObservations('Cancel DP1234'))
        pending = prepared.state_after.pending_approval
        assert pending
        class Author:
            async def compose(self, payload):
                assert payload['evidence']['receipts'][0]['effect_status'] == 'COMMITTED'
                assert payload['evidence']['user_context']['business_observations'][0]['observation']['facts'][0]['value_reference']
                assert 'x' * 30000 not in json.dumps(payload)
                return 'Your cancellation has been submitted.'
        verifier = Verifier(True)
        runtime = TurnRuntime(manager, ResponseAssembler(Author(), knowledge_verifier=verifier), checkpointer=saver)
        identity = _identity('confirm-history')
        message = TurnObservations('', approval_id=pending.approval_id, approval_decision=True)
        result = await runtime.execute(identity, message)
        assert result.assembled.verified and len(workflow.items) == 1
        assert result.managed.state_after.pending_approval is None
        replay = await runtime.execute(identity, message)
        assert replay.assembled == result.assembled and len(workflow.items) == 1
    asyncio.run(run())
