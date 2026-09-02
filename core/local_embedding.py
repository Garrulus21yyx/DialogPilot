"""Dependency-free deterministic embeddings for the local PostgreSQL demo."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata


class LocalHashEmbeddingFunction:
    """Map ASCII words and CJK unigram/bigrams into a normalized 384-d vector."""

    model_id = "dialogpilot-hash-embedding-v1"
    dimension = 384

    def __call__(self, texts):
        return [self.embed(str(text)) for text in texts]

    @classmethod
    def embed(cls, text: str) -> list[float]:
        normalized = unicodedata.normalize("NFKC", text).lower()
        ascii_words = re.findall(r"[a-z0-9]+", normalized)
        cjk = re.findall(r"[\u3400-\u9fff]", normalized)
        tokens = ascii_words + cjk + [
            cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)
        ]
        vector = [0.0] * cls.dimension
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % cls.dimension
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
