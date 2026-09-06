"""Bounded branch scheduling; timed-out work retains capacity until it stops."""

from concurrent.futures import ThreadPoolExecutor, wait
import threading
import time


class RetrievalExecutionUnavailable(RuntimeError):
    pass


class BoundedRetrievalExecutor:
    def __init__(self, max_requests=2):
        if not 1 <= max_requests <= 16:
            raise ValueError("invalid retrieval concurrency")
        self._slots = threading.BoundedSemaphore(max_requests)
        self._executor = ThreadPoolExecutor(
            max_workers=2 * max_requests, thread_name_prefix="knowledge-retrieval"
        )
        self._closed = False
        self._lock = threading.Lock()

    def run(self, dense, lexical, *, deadline):
        if deadline <= time.monotonic():
            raise RetrievalExecutionUnavailable("RETRIEVAL_DEADLINE_EXCEEDED")
        if not self._slots.acquire(timeout=max(0, deadline - time.monotonic())):
            raise RetrievalExecutionUnavailable("RETRIEVAL_CAPACITY_TIMEOUT")
        futures = []
        try:
            with self._lock:
                if self._closed:
                    raise RetrievalExecutionUnavailable("RETRIEVAL_EXECUTOR_CLOSED")
                if deadline <= time.monotonic():
                    raise RetrievalExecutionUnavailable("RETRIEVAL_DEADLINE_EXCEEDED")
                for branch in (dense, lexical):
                    futures.append(self._executor.submit(branch))
        finally:
            if not futures:
                self._slots.release()
            else:
                completed = 0
                completion_lock = threading.Lock()

                def finished(_future):
                    nonlocal completed
                    with completion_lock:
                        completed += 1
                        if completed == len(futures):
                            self._slots.release()

                for future in futures:
                    future.add_done_callback(finished)
        _, pending = wait(futures, timeout=max(0, deadline - time.monotonic()))
        if pending:
            for future in pending:
                future.cancel()
            raise RetrievalExecutionUnavailable("RETRIEVAL_DEADLINE_EXCEEDED")
        return tuple(future.result() for future in futures)

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
