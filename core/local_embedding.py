"""Shared local embedding provider for PostgreSQL retrieval corpora."""
from __future__ import annotations

import threading


class SentenceTransformerEmbeddingFunction:
    """Lazy, process-shared SentenceTransformer callable."""

    model_id = "sentence-transformers/all-MiniLM-L6-v2"
    dimension = 384
    _model = None
    _lock = threading.Lock()

    def __call__(self, texts):
        values = [str(item) for item in texts]
        if not values:
            return []
        model = self._load_model()
        return model.encode(
            values,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).tolist()

    @classmethod
    def _load_model(cls):
        if cls._model is None:
            with cls._lock:
                if cls._model is None:
                    from sentence_transformers import SentenceTransformer

                    cls._model = SentenceTransformer(cls.model_id)
        return cls._model
