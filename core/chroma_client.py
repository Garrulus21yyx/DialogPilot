"""Chroma 存储边界：部署模式必须显式，禁止连接失败后静默切换物理存储。"""
from dataclasses import dataclass
from typing import Any

import chromadb


@dataclass(frozen=True)
class ChromaBackend:
    """供健康检查与诊断使用的实际存储身份。"""

    mode: str
    location: str

    def to_dict(self) -> dict[str, str]:
        return {"mode": self.mode, "location": self.location}


def create_chroma_client(
    *,
    mode: str,
    host: str,
    port: int,
    path: str,
) -> tuple[Any, ChromaBackend]:
    """按显式模式创建客户端；remote 不可达时 fail closed，绝不改写到本地。"""
    normalized = mode.strip().lower()
    settings = chromadb.Settings(anonymized_telemetry=False)
    if normalized == "remote":
        client = chromadb.HttpClient(host=host, port=port, settings=settings)
        try:
            client.heartbeat()
        except Exception as exc:
            raise RuntimeError(
                f"CHROMA_MODE=remote but Chroma is unavailable at {host}:{port}"
            ) from exc
        return client, ChromaBackend(mode="remote", location=f"{host}:{port}")
    if normalized == "embedded":
        return (
            chromadb.PersistentClient(path=path, settings=settings),
            ChromaBackend(mode="embedded", location=path),
        )
    raise ValueError("CHROMA_MODE must be 'remote' or 'embedded'")
