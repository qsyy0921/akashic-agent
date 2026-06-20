from __future__ import annotations

import asyncio
import math

from memory2.embedder import Embedder


def test_local_hash_embedder_returns_normalized_1024_dim_vector():
    embedder = Embedder(base_url="local://hash", api_key="", model="local-hash-embedding")

    vector = asyncio.run(embedder.embed("Token pruning improves efficient vision transformers."))

    assert len(vector) == 1024
    assert any(value != 0 for value in vector)
    norm = math.sqrt(sum(value * value for value in vector))
    assert abs(norm - 1.0) < 1e-6


def test_local_hash_embedder_is_deterministic():
    embedder = Embedder(base_url="local://hash", api_key="", model="local-hash-embedding")

    first, second = asyncio.run(embedder.embed_batch(["hello world", "hello world"]))

    assert first == second

