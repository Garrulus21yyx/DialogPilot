"""Evidence at the simulator's LiteLLM boundary; never changes model output.

Optional benchmark dependency: import only after the tau environment is loaded.
Official CustomLogger hooks capture the provider payload before tau validates it.
Langfuse export is delegated to LiteLLM's standard OTEL integration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Condition
from datetime import datetime
import uuid

from litellm.integrations.custom_logger import CustomLogger


REQUEST_PARAMETERS = (
    'temperature', 'max_tokens', 'thinking', 'timeout', 'num_retries',
    'seed', 'top_p', 'stop', 'tools', 'tool_choice', 'drop_params', 'allowed_openai_params',
)


def simulator_parameters(max_tokens):
    """Anthropic-compatible user endpoint: explicitly forward its thinking control.

    tau sets drop_params globally for optional parameters (including seed).
    Model-name discovery must not silently delete this required control.
    """
    return {'temperature': 0, 'thinking': {'type': 'disabled'},
            'allowed_openai_params': ['thinking'], 'max_tokens': max_tokens,
            'timeout': 60, 'num_retries': 0}


def response_shape(response):
    """Observations, not guesses about why a provider produced empty text."""
    choice = (response.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    content = message.get('content')
    text = content.strip() if isinstance(content, str) else ''
    tools = message.get('tool_calls') or []
    reason = choice.get('finish_reason')
    outcome = ('TRUNCATED' if reason == 'length' else
               'TOOL_CALL' if tools else 'TEXT' if text else 'EMPTY_CONTENT')
    return {'outcome': outcome, 'finish_reason': reason,
            'content_present': bool(text), 'tool_call_count': len(tools),
            'reasoning_present': bool(message.get('reasoning_content') or message.get('thinking_blocks')),
            'usage': response.get('usage')}


class SimulatorDiagnostics(CustomLogger):
    def __init__(self, directory: Path, *, langfuse=False, secrets=()):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.capture_id = uuid.uuid4().hex
        self.records = {}
        self.persistence_errors = []
        self.condition = Condition()
        self.secrets = tuple(value for value in secrets if value)
        self.exporter = None
        if langfuse:
            if os.getenv('LANGFUSE_BASE_URL'):
                os.environ.setdefault('LANGFUSE_HOST', os.environ['LANGFUSE_BASE_URL'])
            from litellm.integrations.langfuse.langfuse_otel import LangfuseOtelLogger
            self.exporter = LangfuseOtelLogger(callback_name='langfuse_otel')
        import litellm
        # Runtime dependency, not serializable model configuration. tau deep-copies
        # llm_args; only correlation metadata belongs in that value object.
        litellm.callbacks.append(self)

    def options(self, task_id, session_id, *, model=None, parameters=None):
        return {'metadata': {
            'simulator_capture_id': self.capture_id, 'task_id': str(task_id),
            'session_id': session_id, 'generation_name': 'simulate-user',
            'trace_name': 'simulate-user', 'tags': ['tau3', 'simulator'],
            'trace_metadata': {'task_id': str(task_id), 'capture_id': self.capture_id},
            'simulator_request': {'model': model, **(parameters or {})},
        }}

    def _identity(self, kwargs):
        metadata = (kwargs.get('litellm_params') or {}).get('metadata') or kwargs.get('metadata') or {}
        if metadata.get('simulator_capture_id') != self.capture_id:
            return None
        return str(kwargs['litellm_call_id']), metadata

    def _safe(self, value):
        from core.telemetry_privacy import mask_telemetry
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                pass
        return mask_telemetry(value, secrets=self.secrets)

    def sanitize(self, value):
        return self._safe(value)

    def _record(self, kwargs, **updates):
        identity = self._identity(kwargs)
        if identity is None:
            return
        call_id, metadata = identity
        with self.condition:
            record = dict(self.records.get(call_id, {
                'schema': 'tau3-simulator-call-v1', 'call_id': call_id,
                'task_id': metadata['task_id'], 'session_id': metadata['session_id'],
            }))
            record.update(self._safe(updates))
            # SDK call ids are not file paths; use our own stable UUID filename.
            name = uuid.uuid5(uuid.NAMESPACE_URL, call_id).hex + '.json'
            try:
                temporary = self.directory / (name + '.tmp')
                temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
                temporary.replace(self.directory / name)
            except OSError as exc:
                self.persistence_errors.append({'call_id': call_id, 'task_id': metadata['task_id'],
                    'type': type(exc).__name__, 'message': self._safe(str(exc))})
            else:
                self.records[call_id] = record
            self.condition.notify_all()

    def log_pre_api_call(self, model, messages, kwargs):
        import litellm
        identity = self._identity(kwargs)
        if identity is None:
            return
        params = kwargs.get('optional_params') or {}
        provider = kwargs.get('custom_llm_provider')
        model = f'{provider}/{model}' if provider and not model.startswith(f'{provider}/') else model
        requested = identity[1].get('simulator_request') or {}
        self._record(kwargs, request={'model': requested.get('model') or model, 'messages': messages,
            'drop_params': kwargs.get('drop_params', litellm.drop_params),
            **{k: params[k] for k in REQUEST_PARAMETERS if k in params},
            **{k: requested[k] for k in REQUEST_PARAMETERS if k in requested}},
            effective_parameters=params,
            provider_request=(kwargs.get('additional_args') or {}).get('complete_input_dict'), stage='REQUESTED')

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, provider_response=self._safe(kwargs.get('original_response')), stage='PROVIDER_RETURNED')

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        if self._identity(kwargs) is None:
            return
        response = response_obj.model_dump(mode='json')
        self._record(kwargs, response=response, assessment=response_shape(response),
                     stage='SDK_RETURNED', start_time=start_time, end_time=end_time, terminal=True)
        self._export('log_success_event', kwargs, response_obj, start_time, end_time)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        if self._identity(kwargs) is None:
            return
        exc = kwargs.get('exception')
        if self._identity(kwargs)[0] not in self.records:
            self.log_pre_api_call(kwargs.get('model', ''), kwargs.get('messages', []), kwargs)
        self._record(kwargs, stage='SDK_FAILED', terminal=True,
            failure={'type': type(exc).__name__, 'message': str(exc)},
            start_time=start_time, end_time=end_time)
        self._export('log_failure_event', kwargs, response_obj, start_time, end_time)

    def _export(self, method, kwargs, response, start, end):
        if self.exporter:
            # Standard integration owns the span shape and OTEL transport.
            try:
                safe_response = type(response).model_validate(self._safe(response)) if hasattr(response, 'model_validate') else response
                safe_kwargs = self._safe(kwargs)
                metadata = safe_kwargs.setdefault('litellm_params', {}).setdefault('metadata', {})
                metadata.setdefault('trace_metadata', {})['call_id'] = kwargs['litellm_call_id']
                for key, value in kwargs.items():
                    if isinstance(value, datetime):
                        safe_kwargs[key] = value
                if kwargs.get('exception') is not None:
                    safe_kwargs['exception'] = RuntimeError(self._safe(str(kwargs['exception'])))
                getattr(self.exporter, method)(safe_kwargs, safe_response, start, end)
            except Exception as exc:
                self._record(kwargs, trace_export_error={'type': type(exc).__name__, 'message': str(exc)})
            finally:
                self._record(kwargs, export_finished=True)

    def task_summary(self, task_id, timeout=5):
        with self.condition:
            complete = self.condition.wait_for(lambda: all(
                r.get('terminal') and (not self.exporter or r.get('export_finished'))
                for r in self.records.values() if r['task_id'] == str(task_id)), timeout)
            records = [r for r in self.records.values() if r['task_id'] == str(task_id)]
            errors = [e for e in self.persistence_errors if e['task_id'] == str(task_id)]
            return {'capture_complete': bool(records) and complete and not errors, 'capture_errors': errors,
                    'calls': [{'call_id': r['call_id'], 'stage': r.get('stage'),
                               'assessment': r.get('assessment'), 'failure': r.get('failure'),
                               'trace_export_error': r.get('trace_export_error')}
                              for r in records]}

    def close(self):
        import litellm
        flush_errors = []
        if self.exporter:
            # Pinned LiteLLM uses credential-scoped providers even when a provider
            # is injected. It exposes no close hook; own only this SDK seam.
            for provider in self.exporter._tracer_provider_cache.values():
                try:
                    if not provider.force_flush():
                        flush_errors.append('OTEL_FLUSH_TIMEOUT')
                    provider.shutdown()
                except Exception as exc:
                    flush_errors.append(self._safe(f'{type(exc).__name__}: {exc}'))
        # Dynamic callbacks in this pinned SDK also register globally.
        for name in ('callbacks', 'input_callback', 'success_callback', 'failure_callback',
                     '_async_success_callback', '_async_failure_callback'):
            callbacks = getattr(litellm, name)
            while self in callbacks:
                callbacks.remove(self)
        return {'flush_errors': flush_errors,
                'delivery_acknowledged': False}  # OTEL flush is not a server receipt.


def replay_user_call(record, *, api_key, api_base, diagnostics, completion):
    """One LLM request only. No environment, application, tools executor or scores."""
    if record.get('schema') != 'tau3-simulator-call-v1':
        raise ValueError('Replay requires a captured simulator request')
    request = record['request']
    if any(marker in json.dumps(request) for marker in ('[REDACTED', '{{EMAIL', '{{UNKNOWN')):
        raise ValueError('Captured input was redacted; exact replay is unavailable')
    return completion(model=request['model'], messages=request['messages'],
        **{k: request[k] for k in REQUEST_PARAMETERS if k in request and k != 'num_retries'},
        num_retries=0, api_key=api_key, api_base=api_base,
        **diagnostics.options(record['task_id'], 'replay-' + record['session_id'],
            model=request['model'], parameters={k: request[k] for k in REQUEST_PARAMETERS
                                               if k in request and k != 'num_retries'}))
