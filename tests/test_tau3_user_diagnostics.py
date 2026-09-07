"""Simulator observability must not repair, suppress or execute model output."""
import json
from datetime import datetime
from unittest.mock import Mock

import pytest

pytest.importorskip('litellm')
from litellm import ModelResponse
from evaluation.tau3_user_diagnostics import SimulatorDiagnostics, response_shape, replay_user_call


@pytest.mark.parametrize('content,tools,reason,expected', [
    ('hello', None, 'stop', 'TEXT'), ('', None, 'stop', 'EMPTY_CONTENT'),
    (None, None, 'stop', 'EMPTY_CONTENT'), ('  ', None, 'stop', 'EMPTY_CONTENT'),
    ('partial', None, 'length', 'TRUNCATED'), (None, None, 'length', 'TRUNCATED'),
    (None, [{'id': '1'}], 'tool_calls', 'TOOL_CALL'),
])
def test_output_algebra(content, tools, reason, expected):
    response = {'choices': [{'message': {'content': content, 'tool_calls': tools}, 'finish_reason': reason}]}
    assert response_shape(response)['outcome'] == expected


def test_capture_all_boundaries_and_redact(tmp_path):
    capture = SimulatorDiagnostics(tmp_path / 'capture', secrets=('secret-value',))
    kwargs = {'litellm_call_id': 'call', 'litellm_params': {'metadata': capture.options('task', 'session')['metadata']},
              'custom_llm_provider': 'anthropic', 'optional_params': {'max_tokens': 512},
              'api_key': 'secret-value'}
    capture.log_pre_api_call('model', [{'role': 'user', 'content': 'question'}], kwargs)
    kwargs['original_response'] = json.dumps({'content': [], 'stop_reason': 'max_tokens',
                                             'usage': {'input_tokens': 100, 'output_tokens': 512}})
    capture.log_post_api_call(kwargs, None, None, None)
    response = ModelResponse(model='model', choices=[{'message': {'role': 'assistant', 'content': None,
        'reasoning_content': 'private reasoning secret-value'}, 'finish_reason': 'length'}],
        usage={'prompt_tokens': 100, 'completion_tokens': 512, 'total_tokens': 612})
    capture.log_success_event(kwargs, response, datetime.now(), datetime.now())
    record = json.loads(next(capture.directory.glob('*.json')).read_text())
    assert record['provider_response']['stop_reason'] == 'max_tokens'
    assert record['assessment']['outcome'] == 'TRUNCATED'
    assert record['assessment']['reasoning_present'] is True
    assert record['assessment']['usage']['prompt_tokens'] == 100
    assert record['response']['usage']['completion_tokens'] == 512
    assert record['request']['model'] == 'anthropic/model'
    assert record['request']['max_tokens'] == 512
    assert 'secret-value' not in json.dumps(record)
    assert 'private reasoning' not in json.dumps(record)
    assert capture.task_summary('task')['capture_complete']
    assert response.choices[0].message.content is None  # never modify model result


def test_failure_and_missing_callback_are_visible(tmp_path):
    c = SimulatorDiagnostics(tmp_path / 'capture')
    kwargs = {'litellm_call_id': 'call', 'metadata': c.options('t', 's')['metadata']}
    c.log_pre_api_call('m', [], kwargs)
    assert not c.task_summary('t', timeout=0)['capture_complete']
    c.log_failure_event({**kwargs, 'exception': TimeoutError('provider timeout')}, None, None, None)
    summary = c.task_summary('t')
    assert summary['calls'][0]['failure']['type'] == 'TimeoutError'
    assert summary['calls'][0]['stage'] == 'SDK_FAILED'
    c.log_pre_api_call('other', [], {'litellm_call_id': 'unrelated'})
    assert len(c.records) == 1


def test_replay_calls_only_injected_completion_once(tmp_path):
    c = SimulatorDiagnostics(tmp_path / 'capture')
    record = {'schema': 'tau3-simulator-call-v1', 'task_id': 't', 'session_id': 's',
              'request': {'model': 'anthropic/m', 'messages': [{'role': 'user', 'content': 'Q'}],
                          'max_tokens': 512, 'num_retries': 10, 'api_key': 'old-key'}}
    completion = Mock(return_value='raw')
    assert replay_user_call(record, api_key='new-key', api_base='https://example.test',
                            diagnostics=c, completion=completion) == 'raw'
    completion.assert_called_once()
    assert completion.call_args.kwargs['num_retries'] == 0
    assert completion.call_args.kwargs['api_key'] == 'new-key'
    record['request']['messages'][0]['content'] = '[REDACTED]'
    with pytest.raises(ValueError, match='exact replay'):
        replay_user_call(record, api_key='x', api_base='x', diagnostics=c, completion=completion)
    completion.assert_called_once()


def test_real_sdk_callback_lifecycle(tmp_path):
    import litellm
    c = SimulatorDiagnostics(tmp_path / 'capture')
    try:
        result = litellm.completion(model='openai/test', messages=[{'role': 'user', 'content': 'Hi'}],
            max_tokens=512, mock_response='Hi', **c.options('t', 's'))
        assert result.choices[0].message.content == 'Hi'
        assert c.task_summary('t')['capture_complete']
        record = next(iter(c.records.values()))
        assert record['request']['model'] == 'openai/test'
        assert record['request']['max_tokens'] == 512
    finally:
        c.close()
    assert c not in litellm.input_callback


def test_disk_failure_never_counts_as_capture_complete(tmp_path, monkeypatch):
    from pathlib import Path
    c = SimulatorDiagnostics(tmp_path / 'capture')
    kwargs = {'litellm_call_id': 'call', 'metadata': c.options('t', 's')['metadata']}
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'write_text', Mock(side_effect=OSError('disk unavailable')))
        c.log_pre_api_call('m', [], kwargs)
    result = c.task_summary('t', timeout=0)
    assert not result['capture_complete']
    assert result['capture_errors'][0]['type'] == 'OSError'


def test_export_boundary_masks_every_payload(tmp_path):
    c = SimulatorDiagnostics(tmp_path / 'capture', secrets=('SYNTHETIC-SECRET',))
    c.exporter = Mock()
    kwargs = {'litellm_call_id': 'call', 'metadata': c.options('t', 's')['metadata'],
              'original_response': json.dumps({'content': [{'type': 'reasoning', 'content': 'PRIVATE', 'signature': 'PRIVATE'}]}),
              'additional_args': {'complete_input_dict': {'key': 'SYNTHETIC-SECRET'}},
              'standard_logging_object': {'response': {'type': 'reasoning', 'content': 'PRIVATE'}},
              'exception': RuntimeError('SYNTHETIC-SECRET')}
    c.log_failure_event(kwargs, None, datetime.now(), datetime.now())
    exported = str(c.exporter.log_failure_event.call_args)
    assert 'SYNTHETIC-SECRET' not in exported and 'PRIVATE' not in exported
    assert c.task_summary('t')['capture_complete']


@pytest.mark.parametrize('model', ['unknown-model-a', 'future-model-b', 'deepseek-v4-flash'])
def test_sdk_sends_required_thinking_control_for_unlisted_models(tmp_path, model):
    """Use real LiteLLM mapping + HTTP, not a mocked parameter dictionary."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import litellm
    from evaluation.tau3_user_diagnostics import simulator_parameters
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            payload = json.dumps({'id': 'message-test', 'type': 'message', 'role': 'assistant',
                'model': model, 'content': [{'type': 'text', 'text': 'Hello'}], 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 8, 'output_tokens': 2}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    c = SimulatorDiagnostics(tmp_path / 'capture')
    try:
        litellm.completion(model='anthropic/' + model, api_key='test',
            api_base=f'http://127.0.0.1:{server.server_port}', drop_params=True,
            messages=[{'role': 'user', 'content': 'Hi'}], **simulator_parameters(512),
            **c.options('t', 's'))
        assert c.task_summary('t')['capture_complete']
        assert requests[0]['thinking'] == {'type': 'disabled'}
        assert requests[0]['max_tokens'] == 512
        record = next(iter(c.records.values()))
        assert record['provider_request']['thinking'] == {'type': 'disabled'}
        assert record['provider_response']['content'][0]['text'] == 'Hello'
        assert record['response']['choices'][0]['message']['content'] == 'Hello'
    finally:
        c.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_official_langfuse_exporter_attributes_and_actual_provider_flush(tmp_path, monkeypatch):
    import litellm
    from litellm.integrations.opentelemetry import OpenTelemetry
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    memory = InMemorySpanExporter()
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'pk-test')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'sk-test')
    monkeypatch.setenv('LANGFUSE_HOST', 'https://example.test')
    monkeypatch.setattr(OpenTelemetry, '_get_span_processor', lambda *a, **kw: SimpleSpanProcessor(memory))
    c = SimulatorDiagnostics(tmp_path / 'capture', langfuse=True)
    litellm.completion(model='openai/test', messages=[{'role': 'user', 'content': 'Hello'}],
                       mock_response='Hi', **c.options('t', 'sdk-session'))
    assert c.task_summary('t')['capture_complete']
    providers = list(c.exporter._tracer_provider_cache.values())
    assert providers  # This SDK really used credential-scoped providers.
    for provider in providers:
        monkeypatch.setattr(provider, 'force_flush', Mock(wraps=provider.force_flush))
        monkeypatch.setattr(provider, 'shutdown', Mock(wraps=provider.shutdown))
    spans = memory.get_finished_spans()
    assert any(s.attributes.get('session.id') == 'sdk-session' for s in spans)
    assert any(s.attributes.get('openinference.span.kind') == 'LLM' for s in spans)
    result = c.close()
    assert result['flush_errors'] == []
    for provider in providers:
        provider.force_flush.assert_called_once()
        provider.shutdown.assert_called_once()
