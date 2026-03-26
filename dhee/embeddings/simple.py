"""Production-grade hash-based embedder. Zero external deps, no API key.

Deterministic: same text → same vector (SHA-256 → float array projection).
Fixed 384 dimensions (small, fast). Suitable for offline use and testing.
"""

import hashlib
import math
import struct
from typing import List, Optional

from dhee.embeddings.base import BaseEmbedder

_DEFAULT_DIMS = 384


class SimpleEmbedder(BaseEmbedder):
    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.dims = int(self.config.get("embedding_dims", _DEFAULT_DIMS))

    def embed(self, text: str, memory_action: Optional[str] = None) -> List[float]:
        """Deterministic embedding: SHA-256 hash → float vector projection.

        Uses multiple hash rounds to fill the vector space, producing
        a normalized unit vector that is stable across runs.
        """
        normalized = text.strip().lower()
        if not normalized:
            return [0.0] * self.dims

        vector = [0.0] * self.dims

        # Use sliding window of 3-grams + whole words for richer signal
        tokens = normalized.split()
        fragments = list(tokens)
        # Add bigrams for phrase sensitivity
        for i in range(len(tokens) - 1):
            fragments.append(f"{tokens[i]} {tokens[i + 1]}")
        # Add character 3-grams for typo tolerance
        for i in range(max(0, len(normalized) - 2)):
            fragments.append(normalized[i:i + 3])

        for fragment in fragments:
            # SHA-256 gives 32 bytes = 8 floats via struct unpacking
            digest = hashlib.sha256(fragment.encode("utf-8")).digest()
            # Use the digest bytes to seed multiple positions
            for offset in range(0, 32, 4):
                idx_bytes = digest[offset:offset + 4]
                idx = int.from_bytes(idx_bytes, "big") % self.dims
                # Use sign from another part of the hash
                sign_bit = digest[(offset + 2) % 32] & 1
                weight = 1.0 if sign_bit else -1.0
                vector[idx] += weight

        # L2 normalize to unit vector
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0:
            vector = [x / norm for x in vector]
        return vector

    def embed_batch(
        self, texts: List[str], memory_action: Optional[str] = None
    ) -> List[List[float]]:
        return [self.embed(t, memory_action=memory_action) for t in texts]
