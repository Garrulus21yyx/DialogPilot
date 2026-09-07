"""Failure causes survive runtime/storage boundaries without leaking raw bodies."""
import json

import pytest
from jsonschema import Draft202012Validator

from application.chat_contracts import Completed, Failed
from application.response_assembly import ResponseAssembler
from application.target_run import terminal_from_outcome, outcome_from_terminal
from core.tracing import exception_chain
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer


@pytest.mark.parametrize('stage', ['composition_model', 'composition_render', 'answer_verification'])
@pytest.mark.parametrize('transient', [False, True])
def test_cause_and_retry_survive_checkpoint_and_terminal(stage, transient):
    from core.framework_models import ModelInvocationError
    try:
        try:
            raise TimeoutError('provider timeout') if transient else ValueError('structured_output_incomplete')
        except Exception as cause:
            raise ModelInvocationError(stage, cause) from cause
    except ModelInvocationError as error:
        response = ResponseAssembler()._failed(error, 'Safe message', stage)
    diagnostic = response.diagnostics[0]
    assert diagnostic.stage == stage
    assert diagnostic.detail['retryable'] is transient
    assert diagnostic.detail['exception_chain'][-1]['type'] == ('TimeoutError' if transient else 'ValueError')
    serializer = target_checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(response)) == response
    for outcome in (Completed('p', {'response': response.text}, response.diagnostics),
                    Failed('invalid', False, 'invocation', stages=response.diagnostics)):
        assert outcome_from_terminal(terminal_from_outcome(outcome)) == outcome


@pytest.mark.parametrize('content', ["{'api_key': 'SYNTHETIC-SECRET'}",
    '{"password":"SYNTHETIC-SECRET"}', '{"thinking":"SYNTHETIC-SECRET"}'])
def test_exception_text_is_masked(content):
    assert 'SYNTHETIC-SECRET' not in json.dumps(exception_chain(ValueError(content)))


def test_schema_failure_keeps_location_not_instance():
    error = next(Draft202012Validator({'type': 'integer'}).iter_errors('SYNTHETIC-SECRET'))
    detail = exception_chain(error)[0]
    assert detail['validator'] == 'type'
    assert detail['schema_path'] == ['type']
    assert 'SYNTHETIC-SECRET' not in json.dumps(detail)


def test_trace_export_failure_cannot_replace_original_cause():
    class BrokenSink:
        def record_failure(self, diagnostic):
            raise RuntimeError('export unavailable')
    result = ResponseAssembler(trace_sink=BrokenSink())._failed(
        ValueError('structured_output_incomplete'), 'Safe', 'composition_model')
    assert result.diagnostics[0].detail['exception_chain'][0]['message'] == 'structured_output_incomplete'
