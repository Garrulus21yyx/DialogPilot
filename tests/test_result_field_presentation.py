import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from application.agent_result import AgentResultStatus, FactSourceKind
from application.capability_registry import ResultField
from application.default_capability_registry import build_default_capability_registry
from application.execution_presentation import execution_status_text
from application.response_assembly import ResponseAssembler
from application.work_item import ControlMode
from application.result_field_presentation import result_fields_text
from infrastructure.target_agent_result_adapter import fact_from_tool_result
from mcp.tool_manager import ToolResult, ToolEffectStatus
from tests.test_execution_presentation import board_for


def refund_case(amount=1346, currency='USD'):
    registry = build_default_capability_registry('tenant-a')
    board = board_for(AgentResultStatus.SUCCEEDED, committed=True)
    item = board.work_items[0]
    definition = registry.tool('refund_request_create')
    fact = fact_from_tool_result(item, ToolResult(
        True, {'refund_id': 'RF1', 'order_id': 'DP1234', 'status': 'REQUESTED',
               'amount_minor': amount, 'currency': currency, 'private_note': 'DO NOT PUBLISH'},
        definition.tool_id, authority=definition.authority,
        output_schema_version=definition.output_schema_version, receipt_id='receipt-0',
    ))
    result = replace(board.results[0], facts=(fact,), candidate_response='Internal reasoning, not a public answer.')
    return registry, replace(board, results=(result,), facts=(fact,)), fact


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('amount', [0, 1, 1346, 10000, 987654321])
@pytest.mark.parametrize('currency', ['USD', 'EUR', 'CNY'])
@pytest.mark.parametrize('mode', [ControlMode.ACTION, ControlMode.WORKFLOW])
def test_real_tool_fact_conversion_reaches_no_model_presentation(locale, amount, currency, mode):
    class NoModel:
        async def compose(self, *args, **kwargs):
            raise AssertionError('Fact projection does not need an author')
        async def verify(self, *args, **kwargs):
            raise AssertionError('Fact projection does not need a judge')
    registry, board, fact = refund_case(amount, currency)
    board = replace(board, work_items=(replace(board.work_items[0], control_mode=mode,
        flow_ref='execute_refund:v1' if mode is ControlMode.WORKFLOW else None),))
    from decimal import Decimal
    result = asyncio.run(ResponseAssembler(NoModel(), knowledge_verifier=NoModel(),
        registry=registry, fallback_locale=locale).assemble(board, current_message='Proceed'))
    assert result.verified and not result.composer_used
    assert result.verification_reason == 'EXECUTION_STATUS_RENDERED'
    assert f'{Decimal(amount) / 100:f} {currency}' in result.text
    assert 'DP1234' in result.text and 'RF1' in result.text and 'REQUESTED' in result.text
    assert 'DO NOT PUBLISH' not in result.text and 'Internal reasoning' not in result.text
    assert fact.source_ref in result.evidence_refs


@pytest.mark.parametrize('amount,direction', [('-13.46', 'Refund'), ('0', 'No difference'), ('13.46', 'Additional charge')])
def test_delta_direction_is_declared_by_tool_not_guessed_from_amount(amount, direction):
    registry, _, fact = refund_case()
    tool = registry.tool(fact.producer_id)
    field = ResultField(('amount_minor',), '差额', 'Difference', 'charge_delta', 'USD')
    registry = replace(registry, tools=tuple(replace(t, result_fields=(field,)) if t == tool else t for t in registry.tools))
    fact = replace(fact, value_json=json.dumps({'amount_minor': amount}, separators=(',', ':'), sort_keys=True))
    text = result_fields_text(fact, registry, locale='en')
    assert direction in text and '-' not in text and 'settled' not in text


@pytest.mark.parametrize('fault', ['source_kind', 'producer', 'authority', 'schema_version', 'missing_field',
                                  'object', 'boolean_money', 'nonfinite', 'currency'])
def test_unknown_or_malformed_projection_is_not_rendered(fault):
    registry, _, fact = refund_case()
    changes = {'source_kind': {'source_kind': FactSourceKind.USER_ASSERTED},
               'producer': {'producer_id': 'other'}, 'authority': {'requirement_id': 'other'},
               'schema_version': {'producer_version': 'unknown'}}
    if fault in changes:
        fact = replace(fact, **changes[fault])
    else:
        data = json.loads(fact.value_json)
        if fault == 'missing_field':
            data.pop('amount_minor')
        elif fault == 'object':
            data['order_id'] = {'unexpected': 'object'}
        else:
            data['currency' if fault == 'currency' else 'amount_minor'] = {
                'boolean_money': True, 'nonfinite': 'NaN', 'currency': None}[fault]
        fact = replace(fact, value_json=json.dumps(data, separators=(',', ':'), sort_keys=True))
    assert result_fields_text(fact, registry, locale='en') is None


def test_independent_goal_is_not_discarded_even_when_it_reuses_action_evidence():
    from tests.test_work_control import _item
    registry, board, _ = refund_case()
    other = replace(_item('explain', 1, work_item_id='explain'),
                    dependencies=(board.work_items[0].work_item_id,))
    other_result = replace(board.results[0], work_item_id=other.work_item_id,
                           owner_agent=other.owner_agent, candidate_response='Explain whether this saves money.')
    board = replace(board, work_items=(*board.work_items, other), results=(*board.results, other_result))
    assert execution_status_text(board, registry=registry, locale='en') is None


def test_result_fields_survive_sdk_serialization_and_change_bundle_fingerprint():
    registry, _, fact = refund_case()
    tool = registry.tool(fact.producer_id)
    adapter = TypeAdapter(type(tool))
    assert adapter.validate_json(adapter.dump_json(tool)) == tool
    changed = replace(registry, tools=tuple(replace(t, result_fields=()) if t == tool else t for t in registry.tools))
    assert changed.fingerprint != registry.fingerprint


@pytest.mark.parametrize('tool_id,payload,expected', [
    ('shipping_address_change', {'order_id': 'O1', 'new_address': 'New York, NY 10001, USA', 'status': 'CHANGED'}, 'New York, NY 10001, USA'),
    ('order_cancel', {'order_id': 'O1', 'status': 'CANCELLED'}, 'CANCELLED'),
])
def test_native_result_views_cover_business_fields(tool_id, payload, expected):
    registry, _, fact = refund_case()
    tool = registry.tool(tool_id)
    fact = replace(fact, producer_id=tool_id, requirement_id=tool.authority,
                   producer_version=tool.output_schema_version,
                   value_json=json.dumps(payload, sort_keys=True, separators=(',', ':')))
    assert expected in result_fields_text(fact, registry, locale='en')


def test_public_views_are_bound_to_native_tool_manifests():
    from mcp.customer_operations_tools import customer_operation_tools
    registry = build_default_capability_registry('tenant-a')
    tools = {tool.name: tool for tool in customer_operation_tools(None)}
    for definition in registry.tools:
        if not definition.result_fields:
            continue
        tool = tools[definition.tool_id]
        assert tool.output_schema_version == definition.output_schema_version
        assert tool.authority == definition.authority
        assert {field.path[0] for field in definition.result_fields} <= set(tool.output_fields)


def test_retained_action_projection_survives_checkpoint_and_keeps_operation_numbering():
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    registry, board, _ = refund_case()
    second = board_for(AgentResultStatus.SUCCEEDED, committed=True).work_items[0]
    second = replace(second, work_item_id='second', operation_key='second')
    result = replace(board.results[0], work_item_id='second', action_receipts=(
        replace(board.results[0].action_receipts[0], receipt_id='second', operation_key='second'),),
        facts=(replace(board.results[0].facts[0], source_ref='second'),))
    combined = replace(board, retained_outcomes=tuple(board.outcome_items), work_items=(second,), results=(result,))
    serde = target_checkpoint_serializer()
    restored = serde.loads_typed(serde.dumps_typed(combined))
    text = execution_status_text(restored, registry=registry, locale='en')
    assert 'Operation 1:' in text and 'Operation 2:' in text and 'Operation 6:' not in text


def test_old_snapshot_cannot_be_presented_as_write_result():
    registry, board, fact = refund_case()
    old = replace(fact, source_ref='previous-query')
    board = replace(board, results=(replace(board.results[0], facts=(old,)),))
    assert execution_status_text(board, registry=registry, locale='en') is None


def test_committed_receipt_does_not_hide_a_remaining_question():
    from application.agent_result import MissingInputSpec
    registry, board, _ = refund_case()
    result = replace(board.results[0], status=AgentResultStatus.PARTIAL, missing_inputs=(
        MissingInputSpec('reply', board.results[0].work_item_id, 'INPUT', 'string', 'Which option?'),))
    board = replace(board, results=(result,))
    assert execution_status_text(board, registry=registry, locale='en') is None


def test_postgres_refund_receipt_delivers_amount_without_author_or_judge(customer_operations):
    from tests.test_customer_operations_tools import setup_runtime, context
    _, manager = setup_runtime(customer_operations)
    registry = build_default_capability_registry(customer_operations.tenant_id)
    async def run():
        eligible = await manager.execute_for_agent('refund_eligibility_check', {'order_id': 'order-1'},
            agent_type='billing', context=context())
        return await manager.execute_for_agent('refund_request_create',
            {'order_id': 'order-1', 'expected_order_version': eligible.data['order_version'], 'reason': 'Requested'},
            agent_type='billing', context=context(), approved=True, call_id='presentation-refund')
    result = asyncio.run(run())
    assert result.success and result.effect_status == ToolEffectStatus.COMMITTED.value
    board = board_for(AgentResultStatus.SUCCEEDED, committed=True)
    fact = fact_from_tool_result(board.work_items[0], result)
    receipt = replace(board.results[0].action_receipts[0], receipt_id=result.receipt_id)
    board = replace(board, results=(replace(board.results[0], facts=(fact,), action_receipts=(receipt,)),), facts=(fact,))
    # No models configured: ordinary explanation would not pass this assertion.
    response = asyncio.run(ResponseAssembler(registry=registry, fallback_locale='en').assemble(
        board, current_message='Proceed'))
    assert response.verified and response.verification_reason == 'EXECUTION_STATUS_RENDERED'
    assert '399 CNY' in response.text and result.receipt_id in response.text
    assert 'not settlement' in response.text
