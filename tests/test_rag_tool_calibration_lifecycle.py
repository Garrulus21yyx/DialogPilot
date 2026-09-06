import sys
from contextlib import contextmanager

import pytest

from scripts import run_rag_tool_calibration as runner


def test_unwritable_output_does_not_create_database(tmp_path, monkeypatch):
    parent = tmp_path/'file'
    parent.write_text('not a directory')
    monkeypatch.setenv('TEST_DATABASE_URL', 'postgresql://localhost/test')
    monkeypatch.setattr(sys, 'argv', ['runner', '--model', str(tmp_path), '--output', str(parent/'output')])
    def forbidden(*args, **kwargs):
        pytest.fail('database creation must follow successful output preparation')
    monkeypatch.setattr(runner.psycopg, 'connect', forbidden)
    with pytest.raises(NotADirectoryError):
        runner.main()


def test_evaluation_failure_cleans_only_owned_database(tmp_path, monkeypatch):
    queries = []
    class Connection:
        def execute(self, query, params=()):
            queries.append((query.as_string() if hasattr(query, 'as_string') else query, params))
    @contextmanager
    def connect(*args, **kwargs):
        yield Connection()
    async def failing(*args):
        raise RuntimeError('injected evaluation failure')
    monkeypatch.setenv('TEST_DATABASE_URL', 'postgresql://localhost/test')
    monkeypatch.setattr(sys, 'argv', ['runner', '--model', str(tmp_path), '--output', str(tmp_path/'output')])
    monkeypatch.setattr(runner.psycopg, 'connect', connect)
    monkeypatch.setattr(runner, 'evaluate', failing)
    with pytest.raises(RuntimeError, match='injected evaluation failure'):
        runner.main()
    created = queries[0][0].removeprefix('CREATE DATABASE ')
    assert created.startswith('"dialogpilot_rag_dev_')
    assert queries[-1][0] == 'DROP DATABASE ' + created
    assert queries[1][1] == (created.strip('"'),)
