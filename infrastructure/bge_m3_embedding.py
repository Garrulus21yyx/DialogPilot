"""Offline-only BGE-M3 dense embedding provider.

The local directory is a deployment locator, not model identity.  A pinned
upstream revision and an artifact SHA-256 are required separately so the same
``EmbeddingProfile`` can be reproduced when deployments use different paths.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from application.hybrid_retrieval import EmbeddingProfile, EmbeddingProviderKind


BGE_M3_MODEL_ID = "BAAI/bge-m3"
BGE_M3_DIMENSION = 1024
BGE_M3_PROVIDER_ID = (
    "dialogpilot-sentence-transformers-5.7.0-local-bge-m3-v1"
)
BGE_M3_PREPROCESSING = (
    "raw-text-unmodified+model-tokenizer+no-prompt+l2-normalized-v1"
)


class BGEM3EmbeddingConfigurationError(ValueError):
    """The declared local artifact cannot satisfy the BGE-M3 profile."""


class BGEM3EmbeddingUnavailable(RuntimeError):
    """The pinned local model dependency or artifact could not be loaded."""


@dataclass(frozen=True)
class BGEM3EmbeddingConfig:
    """Explicit runtime locator and immutable identity for one local model."""

    model_path: Path
    model_revision: str
    model_digest: str
    device: str = "cpu"
    batch_size: int = 32

    def __post_init__(self) -> None:
        try:
            model_path = self.model_path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 model path must resolve to an existing local directory"
            ) from exc
        if not model_path.is_dir():
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 model path must be a local directory"
            )
        if not self.model_revision.strip():
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 model revision is required"
            )
        if (
            len(self.model_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.model_digest)
        ):
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 artifact digest must be lowercase SHA-256"
            )
        if not self.device.strip():
            raise BGEM3EmbeddingConfigurationError("BGE-M3 device is required")
        if self.batch_size < 1:
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 batch size must be positive"
            )
        object.__setattr__(self, "model_path", model_path)

    @classmethod
    def from_env(
        cls, values: Mapping[str, str] | None = None,
    ) -> "BGEM3EmbeddingConfig":
        env = os.environ if values is None else values
        try:
            model_path = env["BGE_M3_LOCAL_MODEL_PATH"]
            model_revision = env["BGE_M3_MODEL_REVISION"]
            model_digest = env["BGE_M3_MODEL_SHA256"]
        except KeyError as exc:
            raise BGEM3EmbeddingConfigurationError(
                f"missing BGE-M3 configuration: {exc.args[0]}"
            ) from exc
        try:
            batch_size = int(env.get("BGE_M3_BATCH_SIZE", "32"))
        except ValueError as exc:
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 batch size must be an integer"
            ) from exc
        return cls(
            model_path=Path(model_path),
            model_revision=model_revision,
            model_digest=model_digest,
            device=env.get("BGE_M3_DEVICE", "cpu"),
            batch_size=batch_size,
        )


class _SentenceEncoder(Protocol):
    def get_sentence_embedding_dimension(self) -> int | None: ...

    def encode(self, sentences: list[str], **kwargs): ...


_ModelLoader = Callable[[BGEM3EmbeddingConfig], _SentenceEncoder]


class LocalBGEM3EmbeddingProvider:
    """Learned dense provider backed only by a pre-provisioned local artifact."""

    def __init__(
        self,
        config: BGEM3EmbeddingConfig,
        *,
        model_loader: _ModelLoader | None = None,
    ):
        self.config = config
        self.profile = EmbeddingProfile(
            provider=BGE_M3_PROVIDER_ID,
            provider_kind=EmbeddingProviderKind.MODEL,
            model=BGE_M3_MODEL_ID,
            model_version=config.model_revision,
            dimension=BGE_M3_DIMENSION,
            model_digest=config.model_digest,
            document_preprocessing=BGE_M3_PREPROCESSING,
            query_preprocessing=BGE_M3_PREPROCESSING,
        )
        loader = model_loader or _load_local_sentence_transformer
        try:
            self._model = loader(config)
        except (BGEM3EmbeddingConfigurationError, BGEM3EmbeddingUnavailable):
            raise
        except Exception as exc:
            raise BGEM3EmbeddingUnavailable(
                "pinned local BGE-M3 model could not be loaded"
            ) from exc
        dimension = self._model.get_sentence_embedding_dimension()
        if dimension != self.profile.dimension:
            raise BGEM3EmbeddingConfigurationError(
                "local BGE-M3 dimension does not match the declared 1024d profile"
            )

    def embed_documents(
        self, raw_source_chunks: Sequence[str],
    ) -> Sequence[Sequence[float]]:
        return self._encode(raw_source_chunks)

    def embed_queries(
        self, raw_queries: Sequence[str],
    ) -> Sequence[Sequence[float]]:
        return self._encode(raw_queries)

    def _encode(self, raw_texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if isinstance(raw_texts, (str, bytes)):
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 embedding requires a text batch"
            )
        texts = list(raw_texts)
        if not texts or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise BGEM3EmbeddingConfigurationError(
                "BGE-M3 embedding requires non-blank raw text"
            )
        try:
            vectors = self._model.encode(
                texts,
                batch_size=self.config.batch_size,
                show_progress_bar=False,
                output_value="sentence_embedding",
                precision="float32",
                convert_to_numpy=True,
                convert_to_tensor=False,
                normalize_embeddings=True,
            )
        except Exception as exc:
            raise BGEM3EmbeddingUnavailable(
                "local BGE-M3 encoding failed"
            ) from exc
        return vectors.tolist() if hasattr(vectors, "tolist") else vectors


def _load_local_sentence_transformer(
    config: BGEM3EmbeddingConfig,
) -> _SentenceEncoder:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise BGEM3EmbeddingUnavailable(
            "BGE-M3 requires dependencies from requirements-semantic.txt"
        ) from exc
    try:
        return SentenceTransformer(
            str(config.model_path),
            device=config.device,
            trust_remote_code=False,
            local_files_only=True,
        )
    except Exception as exc:
        raise BGEM3EmbeddingUnavailable(
            "pinned local BGE-M3 model could not be loaded"
        ) from exc
