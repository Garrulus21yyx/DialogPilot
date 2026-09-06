"""Scheduling invariants: actual completion owns capacity, including timeout."""

import threading
import time

import pytest

from infrastructure.bounded_retrieval_executor import (
    BoundedRetrievalExecutor,
    RetrievalExecutionUnavailable,
)


def test_branches_overlap_and_preserve_result_order():
    executor = BoundedRetrievalExecutor(1)
    barrier = threading.Barrier(2)

    def branch(value):
        barrier.wait(timeout=1)
        return value

    try:
        assert executor.run(
            lambda: branch("dense"),
            lambda: branch("lexical"),
            deadline=time.monotonic() + 2,
        ) == ("dense", "lexical")
    finally:
        executor.close()


def test_timeout_retains_capacity_until_workers_finish():
    executor = BoundedRetrievalExecutor(1)
    started = threading.Barrier(3)
    release = threading.Event()

    def blocked():
        started.wait(timeout=1)
        release.wait(2)

    failures = []

    def request():
        try:
            executor.run(blocked, blocked, deadline=time.monotonic() + 0.15)
        except RetrievalExecutionUnavailable as exc:
            failures.append(str(exc))

    caller = threading.Thread(target=request)
    caller.start()
    try:
        started.wait(timeout=1)
        caller.join(timeout=1)
        assert failures == ["RETRIEVAL_DEADLINE_EXCEEDED"]
        with pytest.raises(RetrievalExecutionUnavailable, match="CAPACITY_TIMEOUT"):
            executor.run(lambda: 1, lambda: 2, deadline=time.monotonic() + 0.02)
        release.set()
        assert executor.run(lambda: 1, lambda: 2, deadline=time.monotonic() + 1) == (
            1,
            2,
        )
    finally:
        release.set()
        executor.close()


def test_partial_submission_keeps_accepted_work_accounted(monkeypatch):
    executor = BoundedRetrievalExecutor(1)
    release = threading.Event()
    submit = executor._executor.submit
    calls = 0

    def partial(fn):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("submission failed")
        return submit(fn)

    monkeypatch.setattr(executor._executor, "submit", partial)
    try:
        with pytest.raises(RuntimeError, match="submission failed"):
            executor.run(
                lambda: release.wait(2), lambda: 2, deadline=time.monotonic() + 1
            )
        with pytest.raises(RetrievalExecutionUnavailable, match="CAPACITY_TIMEOUT"):
            executor.run(lambda: 1, lambda: 2, deadline=time.monotonic() + 0.02)
        release.set()
        assert executor.run(lambda: 1, lambda: 2, deadline=time.monotonic() + 1) == (
            1,
            2,
        )
    finally:
        release.set()
        executor.close()


def test_closed_executor_returns_typed_failure():
    executor = BoundedRetrievalExecutor(1)
    executor.close()
    with pytest.raises(RetrievalExecutionUnavailable, match="EXECUTOR_CLOSED"):
        executor.run(lambda: 1, lambda: 2, deadline=time.monotonic() + 1)


def test_expired_deadline_does_not_start_work():
    executor = BoundedRetrievalExecutor(1)
    called = []
    try:
        with pytest.raises(RetrievalExecutionUnavailable, match="DEADLINE_EXCEEDED"):
            executor.run(
                lambda: called.append(1),
                lambda: called.append(2),
                deadline=time.monotonic() - 1,
            )
        assert called == []
    finally:
        executor.close()
