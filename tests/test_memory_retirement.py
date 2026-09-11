"""Production entry points use the canonical owner, never Redis summaries."""
import asyncio

import pytest
from fastapi import HTTPException

from application.conversation_projection import ProjectionName
from infrastructure.memory_projection_adapter import (
    MemoryProjectionContractError, PostgresMemoryFactProjectionAdapter,
)


def test_fact_adapter_cannot_become_a_second_summary_owner():
    for name in ProjectionName:
        if name in {ProjectionName.WORKING_WINDOW, ProjectionName.FACT_EXTRACTION}:
            assert PostgresMemoryFactProjectionAdapter(None, None, name).projection_name is name
        else:
            with pytest.raises(MemoryProjectionContractError):
                PostgresMemoryFactProjectionAdapter(None, None, name)


def test_finalize_unavailable_does_not_switch_to_redis(monkeypatch):
    from api import main
    from core.auth import Principal
    class Memory:
        async def finalize_conversation(self, *args):
            raise AssertionError('old Redis summary must not run')
    monkeypatch.setattr(main, '_conversation_query', None)
    monkeypatch.setattr(main, '_memory', Memory())
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.finalize_conversation('conversation', Principal('user', frozenset({'chat'}))))
    assert error.value.status_code == 503
    assert error.value.detail == {'error': 'conversation_projection_unavailable'}
