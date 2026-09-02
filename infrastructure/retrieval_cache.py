"""Redis exact cache and isolated LangChain embedding-cache compatibility."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from importlib.metadata import version
from typing import Any

import redis
from langchain_core.stores import ByteStore


LANGCHAIN_CLASSIC_VERSION = "1.0.8"


class RedisRetrievalCache:
    """Cache failures are misses; retrieval semantics stay with the owner."""

    def __init__(self, client: redis.Redis, *, prefix: str = "dialogpilot:retrieval:v1:"):
        self.client = client
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str) -> bytes | None:
        try:
            value = self.client.get(self._key(key))
            return bytes(value) if value is not None else None
        except redis.RedisError:
            return None

    def set(self, key: str, value: bytes, *, ttl_seconds: int) -> bool:
        try:
            return bool(self.client.set(
                self._key(key), value, ex=max(1, int(ttl_seconds)),
            ))
        except redis.RedisError:
            return False

    def delete(self, key: str) -> bool:
        try:
            self.client.delete(self._key(key))
            return True
        except redis.RedisError:
            return False


class RedisByteStore(ByteStore):
    """Bounded ByteStore used only behind CacheBackedEmbeddings."""

    def __init__(
        self, client: redis.Redis, *, namespace: str, ttl_seconds: int = 86400,
    ):
        if not namespace.strip():
            raise ValueError("embedding cache namespace is required")
        self.client = client
        self.prefix = f"dialogpilot:embedding:v1:{namespace}:"
        self.ttl_seconds = max(1, int(ttl_seconds))

    def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        if not keys:
            return []
        try:
            values = self.client.mget([self.prefix + key for key in keys])
            return [bytes(value) if value is not None else None for value in values]
        except redis.RedisError:
            return [None] * len(keys)

    def mset(self, key_value_pairs: Sequence[tuple[str, bytes]]) -> None:
        try:
            pipeline = self.client.pipeline(transaction=False)
            for key, value in key_value_pairs:
                pipeline.set(self.prefix + key, value, ex=self.ttl_seconds)
            pipeline.execute()
        except redis.RedisError:
            return

    def mdelete(self, keys: Sequence[str]) -> None:
        if not keys:
            return
        try:
            self.client.delete(*(self.prefix + key for key in keys))
        except redis.RedisError:
            return

    def yield_keys(self, *, prefix: str | None = None) -> Iterator[str]:
        pattern = self.prefix + (prefix or "") + "*"
        try:
            for key in self.client.scan_iter(match=pattern):
                decoded = key.decode() if isinstance(key, bytes) else str(key)
                yield decoded[len(self.prefix):]
        except redis.RedisError:
            return


def cache_backed_embeddings(
    underlying_embeddings: Any,
    *,
    document_store: RedisByteStore,
    query_store: RedisByteStore,
):
    """The only module allowed to depend on langchain-classic's import path."""
    installed = version("langchain-classic")
    if installed != LANGCHAIN_CLASSIC_VERSION:
        raise RuntimeError(
            f"langchain-classic compatibility mismatch: {installed}",
        )
    from langchain_classic.embeddings import CacheBackedEmbeddings

    return CacheBackedEmbeddings.from_bytes_store(
        underlying_embeddings,
        document_store,
        namespace="dialogpilot-embedding-v1",
        query_embedding_cache=query_store,
        key_encoder="sha256",
    )
