"""Trusted per-attempt Run ownership, independent of model/checkpoint state."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import asyncio

from langgraph.errors import GraphBubbleUp


class ExecutionDeferred(GraphBubbleUp):
    """A saved turn is ready, but another Run owns conversation execution."""


_current = ContextVar("target_run_execution", default=None)


def has_run_owner():
    return _current.get() is not None


@dataclass(frozen=True)
class RunExecution:
    store: object
    item: object
    worker_id: str

    async def acquire(self):
        if not await asyncio.to_thread(self.store.acquire_execution, self.item,
                                       worker_id=self.worker_id):
            raise ExecutionDeferred()

    async def check(self):
        await asyncio.to_thread(self.store.assert_owned, self.item, worker_id=self.worker_id)


@contextmanager
def bind_run_execution(store, item, worker_id):
    token = _current.set(RunExecution(store, item, worker_id))
    try:
        yield
    finally:
        _current.reset(token)


async def acquire_execution():
    owner = _current.get()
    if owner is not None:
        await owner.acquire()


async def check_execution():
    owner = _current.get()
    if owner is not None:
        await owner.check()


def check_execution_sync():
    owner = _current.get()
    if owner is not None:
        owner.store.assert_owned(owner.item, worker_id=owner.worker_id)


async def fence_checkpoint(cursor):
    owner = _current.get()
    if owner is not None:
        await owner.store.fence_checkpoint(cursor, owner.item, worker_id=owner.worker_id)


def fence_transaction(connection, *, executing=False):
    """Called by commit owners after locking the conversation, in that transaction."""
    owner = _current.get()
    if owner is not None:
        owner.store.fence_transaction(connection, owner.item, worker_id=owner.worker_id,
                                      executing=executing)
