"""
Embedding 客户端，对接 DashScope text-embedding-v3（OpenAI 兼容接口）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math

from core.net.http import HttpRequester, RequestBudget, get_default_http_requester

logger = logging.getLogger(__name__)


class Embedder:
    MAX_BATCH = 10  # DashScope 每批上限
    MAX_TEXT_LEN = 2000

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-v3",
        requester: HttpRequester | None = None,
    ) -> None:
        self._local_hash = base_url.strip().lower() == "local://hash" or model.startswith(
            "local-hash"
        )
        self._url = "" if self._local_hash else base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self._model = model
        self._requester = (
            requester
            if requester is not None
            else None
            if self._local_hash
            else get_default_http_requester("external_default")
        )

    async def embed(self, text: str) -> list[float]:
        """单条 embed"""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """分批 embed，每批 ≤ MAX_BATCH，批间 sleep 0.3s"""
        if self._local_hash:
            return [_hash_embedding(text) for text in texts]

        results: list[list[float]] = []
        truncated = [t[: self.MAX_TEXT_LEN] for t in texts]

        for i in range(0, len(truncated), self.MAX_BATCH):
            batch = truncated[i : i + self.MAX_BATCH]
            resp = await self._requester.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "input": batch},
                timeout_s=30.0,
                budget=RequestBudget(total_timeout_s=40.0),
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])
            results.extend(d["embedding"] for d in data)

            if i + self.MAX_BATCH < len(truncated):
                await asyncio.sleep(0.3)

        return results

    async def aclose(self) -> None:
        return None


def _hash_embedding(text: str, *, dim: int = 1024) -> list[float]:
    """Deterministic local embedding fallback for offline benchmarks.

    This is not a semantic embedding model. It is a hashed lexical vector that
    keeps benchmark pipelines runnable when no external embedding API is
    available.
    """
    vector = [0.0] * dim
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return vector
    tokens = normalized.split()
    features: list[str] = []
    features.extend(tokens)
    features.extend(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
    compact = normalized.replace(" ", "")
    features.extend(compact[i : i + 3] for i in range(max(0, len(compact) - 2)))
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]
