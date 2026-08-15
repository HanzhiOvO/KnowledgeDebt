from __future__ import annotations

import hashlib
import math
import re


class HashEmbeddingProvider:
    """Tiny deterministic local fallback for development and tests.

    It is not presented as semantic AI. It provides a stable vector-shaped index
    when no external embedding provider has been consented to or configured.
    """

    requires_external_upload = False

    def __init__(self, dimensions: int = 96):
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
