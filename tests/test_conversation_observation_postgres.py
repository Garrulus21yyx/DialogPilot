"""Recreate runtime and database connections around interrupted observation turns."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import ProposalDisposition, TurnProposal
from application.turn_runtime import TurnRuntime
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from tests.test_turn_runtime import _Executor, _OrderUnderstanding
from tests.test_knowledge_answer_boundary import Verifier


@pytest.mark.parametrize("point", ["after_first_read", "after_second_read", "before_delivery"])
def test_observation_turn_survives_postgres_connection_and_runtime_recreation(postgres_database_url, point):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="pg-observation-" + uuid4().hex, request_id="original")
    executor, decisions = _Executor(), []
    message = TurnObservations("Check the order, refresh the status, and answer me")

    class Understanding:
        async def __call__(self, observations, state, deterministic, registry, context):
            decisions.append(observations.raw_text)
            if context.observed_execution and len(context.observed_execution.outcome_items) == 2:
                return TurnProposal(ProposalDisposition.RESPOND, (), "ANSWER", response_text="The order has shipped.")
            base = await _OrderUnderstanding()()
            return replace(base, commands=(replace(base.commands[0], observe_result=True),))

    class Interrupted(TurnRuntime):
        async def _execute_work_plan(self, state):
            result = await super()._execute_work_plan(state)
            step = state["prepared"].planning_step
            if (point == "after_first_read" and step == 0 or point == "after_second_read" and step == 1):
                raise RuntimeError("injected interruption after durable child execution")
            return result

        async def _assemble_response(self, state):
            if point == "before_delivery":
                raise RuntimeError("injected interruption before delivery")
            return await super()._assemble_response(state)

    async def run():
        for attempt in range(2):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=2))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=attempt == 0) as checkpoint:
                    manager = TargetConversationManager(state_store=PostgresConversationStateStore(pool),
                        registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
                        orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={},
                            checkpointer=checkpoint))
                    runtime = (Interrupted if attempt == 0 else TurnRuntime)(manager,
                        ResponseAssembler(knowledge_verifier=Verifier(True)), checkpointer=checkpoint)
                    if attempt == 0:
                        with pytest.raises(RuntimeError, match="injected interruption"):
                            await runtime.execute(identity, message)
                    else:
                        result = await runtime.execute(identity, message)
                        assert result.managed.request_completed
                        assert len(result.managed.board.outcome_items) == 2
                        assert result.assembled.text == "The order has shipped."
                        loaded = manager._state_store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
                        assert loaded.fingerprint == result.managed.state_after.fingerprint
            finally:
                pool.close()
        assert executor.calls == 2
        assert decisions == [message.raw_text] * 3
    asyncio.run(run())
