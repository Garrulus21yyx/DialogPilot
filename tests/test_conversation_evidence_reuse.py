"""Evidence continuity properties; no paid model, search or business execution."""
import asyncio
import json
from dataclasses import replace

import pytest

from application.conversation_context import conversation_context_payload
from application.conversation_evidence import ConversationEvidence
from application.knowledge_tool_contract import evidence_id
from application.response_assembly import ResponseAssembler
from infrastructure.postgres_conversation_evidence import PostgresConversationEvidence
from infrastructure.target_turn_context import TargetTurnContextLoader
from tests.test_knowledge_answer_boundary import Verifier, board
from tests.test_knowledge_tool_contract import evidence_result
from tests.test_target_turn_context import Memory, Tools, _identity, _state
from tests.test_postgres_publication import publication_components, _final, _interaction
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from core.identity import IdentityFactory


def entry():
    return {"pack": evidence_result(), "observed_at": "2026-09-01T00:00:00+00:00"}


@pytest.mark.parametrize("reply", ["仅未拆封商品可以退货。", "换句话说，需要保持未拆封。"])
def test_followup_reuses_evidence_in_same_verifier_without_composition_or_search(reply):
    calls = []
    verifier = Verifier(True)
    assembler = ResponseAssembler(knowledge_verifier=verifier,
        knowledge_reuse_validator=lambda packs: calls.append(packs) or True)
    source = entry()
    text = reply + '[' + evidence_id('child-1') + ']'
    response = asyncio.run(assembler.assemble(None, current_message="解释一下",
        conversation_context={"knowledge_evidence": [{**source, "status": "CURRENT"}]},
        response_candidate=text))
    assert response.verified and response.text == text
    assert response.knowledge_evidence == (source,)
    assert len(verifier.calls) == len(calls) == 1
    assert verifier.calls[0][1]['knowledge_evidence']['packs'][0]['query_used'] == '退货条件'


def test_no_citation_conversation_does_not_revalidate_or_republish_unrelated_pack():
    def forbidden(_):
        raise AssertionError("unrelated source was rechecked")
    response = asyncio.run(ResponseAssembler(knowledge_verifier=Verifier(True),
        knowledge_reuse_validator=forbidden).assemble(None, current_message="谢谢",
        conversation_context={"knowledge_evidence": [{**entry(), "status": "CURRENT"}]},
        response_candidate="不客气。"))
    assert response.verified
    assert response.knowledge_evidence == ()


@pytest.mark.parametrize('status', ['NOT_REUSABLE', 'UNAVAILABLE'])
def test_unusable_source_is_not_support_or_reason_to_block_social_reply(status):
    verifier = Verifier(True)
    response = asyncio.run(ResponseAssembler(knowledge_verifier=verifier).assemble(
        None, current_message="谢谢", response_candidate="不客气。",
        conversation_context={"knowledge_evidence": [{"status": status}]}))
    assert response.verified
    assert verifier.calls[0][1]['knowledge_evidence'] is None


def test_withdrawal_during_response_generation_blocks_only_cited_reused_source():
    response = asyncio.run(ResponseAssembler(knowledge_verifier=Verifier(True),
        knowledge_reuse_validator=lambda _: False).assemble(None,
        current_message="解释一下", response_candidate='未拆封可以退货。['+evidence_id('child-1')+']',
        conversation_context={"knowledge_evidence": [{**entry(), "status": "CURRENT"}]}))
    assert not response.verified
    assert response.verification_reason == 'source_validation:ValueError'
    assert not response.knowledge_evidence


def test_initial_assembly_retains_actual_cited_pack_not_internal_text():
    class Author:
        async def compose(self, _):
            return '未拆封可以退货。['+evidence_id('child-1')+']'
    response = asyncio.run(ResponseAssembler(Author(), knowledge_verifier=Verifier(True),
        knowledge_source_validator=lambda _: True).assemble(board('internal note'), current_message="能退吗"))
    assert response.verified
    assert len(response.knowledge_evidence) == 1
    assert response.knowledge_evidence[0]['pack'] == evidence_result()


def test_composer_gets_actual_labels_without_computing_hashes():
    from application.target_conversation_manager import TargetTurnContext
    from tests.test_response_assembly import _board, _result
    ctx = conversation_context_payload(TargetTurnContext(
        knowledge_evidence=({**entry(), 'status': 'CURRENT'},)))
    class Author:
        async def compose(self, payload):
            label = payload['evidence']['user_context']['knowledge_evidence_labels']['child-1']
            assert label == evidence_id('child-1')
            return '未拆封可以退货。[' + label + ']'
    response = asyncio.run(ResponseAssembler(Author(), knowledge_verifier=Verifier(True),
        knowledge_reuse_validator=lambda _: True).assemble(_board(_result('k', 'general', response='Explain the rule')),
            current_message='解释一下', conversation_context=ctx))
    assert response.verified and response.knowledge_evidence


def test_loader_delivers_shared_evidence_without_calling_memory_search():
    class Reader:
        def load(self, invocation):
            return ConversationEvidence(knowledge=({**entry(), "status": "CURRENT", "publication_id": "p1"},))
    tools = Tools()
    obs, state = TurnObservations("解释一下刚才的规则"), _state()
    ctx = asyncio.run(TargetTurnContextLoader(Memory(), tools, evidence_reader=Reader()).load(
        _identity(), obs, state, DeterministicResolver().resolve(obs, state)))
    payload = conversation_context_payload(ctx)
    assert payload['knowledge_evidence'][0]['pack'] == evidence_result()
    assert payload['knowledge_evidence_labels'] == {'child-1': evidence_id('child-1')}
    from infrastructure.target_model_context import planning_context
    _, messages = planning_context({'message': obs.raw_text, 'conversation_context': payload})
    assert evidence_id('child-1') in json.dumps(messages[-1].content)
    assert 'not that it covers every new question' in payload['knowledge_reuse_contract']
    assert tools.calls == []


@pytest.mark.parametrize('kind', ['final', 'FIELDS', 'APPROVAL'])
def test_committed_evidence_roundtrip_is_scoped_private_and_deduplicated(publication_components, kind):
    pool, identity, service, _ = publication_components
    source = entry()
    command = (_final(identity) if kind == 'final' else
        replace(_interaction(identity), resume_schema={'interaction_kind': kind}))
    command = replace(command, knowledge_evidence=(source, source))
    publish = service.select_final_response if kind == 'final' else service.publish_interaction_request
    first = publish(command)
    assert publish(command).record.publication_id == first.record.publication_id
    with pool.transaction() as connection:
        payload, private = connection.execute('SELECT payload, verification FROM dialogpilot_app.response_deliveries WHERE publication_id=%s',
            (first.record.publication_id,)).fetchone()
    assert 'knowledge_evidence' not in json.dumps(payload)
    assert private['knowledge_evidence'] == [source, source]
    next_identity = IdentityFactory(lambda: 'unused').create_invocation(tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id), conversation_id=str(identity.conversation_id), request_id='next')
    calls = []
    reader = PostgresConversationEvidence(pool, lambda packs: calls.append(packs) or True)
    assert reader.load(identity) == ConversationEvidence()
    result = reader.load(next_identity).knowledge
    assert len(result) == len(calls) == 1
    assert result[0]['observed_at'] == source['observed_at']
    assert result[0]['status'] == 'CURRENT'
    for field in ('tenant_id', 'user_id', 'conversation_id'):
        assert reader.load(replace(next_identity, **{field: 'other'})) == ConversationEvidence()
    stale = PostgresConversationEvidence(pool, lambda _: False).load(next_identity).knowledge
    assert stale[0]['status'] == 'NOT_REUSABLE' and 'pack' not in stale[0]
