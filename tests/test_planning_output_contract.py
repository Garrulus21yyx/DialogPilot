"""Wire-shape properties; compiler remains the authority for executable meaning."""
import itertools

import pytest
from jsonschema import Draft202012Validator

from application.conversation_agent import ConversationAgent, planning_output_schema
from application.turn_planning import ProposalDisposition
from tests.test_conversation_agent import Provider, _invoke


def validator():
    schema = planning_output_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_status_algebra_has_exactly_one_legal_field_combination_per_status():
    check = validator()
    for status, goals, missing in itertools.product(
        ('resolved', 'insufficient_context', 'out_of_scope', 'partial', None),
        (False, True), (False, True),
    ):
        raw = {'status': status}
        if goals:
            raw['goals'] = [{'kind': 'general_qa', 'resolved_query': '退货规则是什么？'}]
        if missing:
            raw['missing_fields'] = ['order_id']
        expected = ((status == 'resolved' and goals and not missing)
                    or (status == 'insufficient_context' and missing and not goals)
                    or (status == 'out_of_scope' and not goals and not missing))
        assert check.is_valid(raw) == expected, raw
        if expected:
            result, _, _ = _invoke(ConversationAgent(Provider(raw)), '想咨询退货规则')
            assert result.disposition is {
                'resolved': ProposalDisposition.RESOLVED,
                'insufficient_context': ProposalDisposition.CLARIFY,
                'out_of_scope': ProposalDisposition.OUT_OF_SCOPE,
            }[status]


@pytest.mark.parametrize('field', [
    'goal_id', 'order_id', 'order_id_source_ref', 'asset_id',
    'asset_id_source_ref', 'new_address', 'revises_control_id', 'resolved_query',
])
@pytest.mark.parametrize('value', [None, '', '   ', 1, False, [], {}])
def test_present_optional_text_is_never_null_or_coerced(field, value):
    assert not validator().is_valid({'status': 'resolved', 'goals': [
        {'kind': 'general_qa', field: value},
    ]})


def test_schema_preserves_compiler_vocabulary_and_is_detached():
    provider = Provider({'status': 'out_of_scope'})
    _invoke(ConversationAgent(provider), '咨询售后问题')
    schema = planning_output_schema()
    assert schema['properties']['goals']['items']['properties']['kind']['enum'] == provider.calls[0]['supported_goals']
    assert schema['properties']['missing_fields']['items']['enum'] == provider.calls[0]['missing_fields_schema']
    schema['properties']['goals']['items']['properties']['kind']['enum'].append('invented')
    assert 'invented' not in planning_output_schema()['properties']['goals']['items']['properties']['kind']['enum']


def test_wire_shape_does_not_authorize_entity_or_dependency():
    for goal in (
        {'kind': 'order_status', 'order_id': 'DP9999'},
        {'kind': 'general_qa', 'resolved_query': '退货规则', 'depends_on': ['unknown']},
        {'kind': 'cancel_active_work', 'revises_control_id': 'inactive'},
    ):
        raw = {'status': 'resolved', 'goals': [goal]}
        assert validator().is_valid(raw)
        result, _, _ = _invoke(ConversationAgent(Provider(raw)), '咨询退货规则')
        assert result.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


def test_bounded_queries_and_options_preserve_positive_boundaries():
    check = validator()
    for size in (1, 128, 129, 4000, 4001):
        raw = {'status': 'resolved', 'goals': [{'kind': 'general_qa', 'resolved_query': '文' * size}]}
        assert check.is_valid(raw) == (size <= 4000)
        raw['goals'][0] = {'kind': 'general_qa', 'knowledge_options': {'applicable_region': '文' * size}}
        assert check.is_valid(raw) == (size <= 128)
