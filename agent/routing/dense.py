from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Protocol, cast

import httpx
import numpy as np
from numpy.typing import NDArray

from agent.routing.contracts import DiscoverySnapshot, ToolDiscoveryDocument

DEFAULT_DENSE_MODEL_ID = "BAAI/bge-small-zh-v1.5"
DEFAULT_DENSE_DIMENSION = 512
DEFAULT_DENSE_MIN_SIMILARITY = 0.56


class DenseRoutingError(RuntimeError):
    """Raised when the authoritative dense retrieval path is invalid."""


class DenseEncoder(Protocol):
    model_id: str
    dimension: int

    def encode(self, texts: Sequence[str]) -> NDArray[np.float32]: ...

    def ensure_ready(self) -> None: ...


class OpenAICompatibleDenseEncoder:
    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        dimension: int,
        api_key: str = "",
        batch_size: int = 10,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not model_id or model_id != model_id.strip():
            raise DenseRoutingError("dense model id must be non-empty and trimmed")
        if not base_url or base_url != base_url.strip():
            raise DenseRoutingError("dense embedding base URL is required")
        if (
            isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or not 1 <= dimension <= 4_096
        ):
            raise DenseRoutingError("dense embedding dimension must be 1..4096")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 32
        ):
            raise DenseRoutingError("dense embedding batch size must be 1..32")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= float(timeout_seconds) <= 120.0
        ):
            raise DenseRoutingError("dense embedding timeout must be 0.1..120s")
        self.model_id = model_id
        self.dimension = dimension
        self._url = base_url.rstrip("/") + "/embeddings"
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout_seconds = float(timeout_seconds)

    def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
        normalized_texts = _validate_texts(texts)
        vectors: list[list[float]] = []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                for offset in range(0, len(normalized_texts), self._batch_size):
                    batch = normalized_texts[offset : offset + self._batch_size]
                    response = client.post(
                        self._url,
                        headers=headers,
                        json={
                            "model": self.model_id,
                            "input": batch,
                            "dimensions": self.dimension,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw_items = payload.get("data")
                    if not isinstance(raw_items, list) or len(raw_items) != len(batch):
                        raise DenseRoutingError(
                            "dense embedding response count mismatch"
                        )
                    if any(not isinstance(item, dict) for item in raw_items):
                        raise DenseRoutingError(
                            "dense embedding response item is invalid"
                        )
                    items = cast(list[dict[str, object]], raw_items)
                    indexed_items: list[tuple[int, dict[str, object]]] = []
                    for item in items:
                        index = item.get("index")
                        if isinstance(index, bool) or not isinstance(index, int):
                            raise DenseRoutingError(
                                "dense embedding response indexes are invalid"
                            )
                        indexed_items.append((index, item))
                    indexed_items.sort(key=lambda pair: pair[0])
                    if [index for index, _ in indexed_items] != list(
                        range(len(batch))
                    ):
                        raise DenseRoutingError(
                            "dense embedding response indexes are invalid"
                        )
                    for _, item in indexed_items:
                        embedding = item.get("embedding")
                        if not isinstance(embedding, list):
                            raise DenseRoutingError(
                                "dense embedding response vector is invalid"
                            )
                        vectors.append(embedding)
        except DenseRoutingError:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise DenseRoutingError(
                "OpenAI-compatible dense embedding request failed"
            ) from exc
        return _normalize_matrix(
            vectors,
            expected_rows=len(normalized_texts),
            expected_dimension=self.dimension,
        )

    def ensure_ready(self) -> None:
        _ = self.encode(("Akashic intent routing readiness check",))


def build_dense_encoder(
    *,
    backend: str,
    model_id: str,
    dimension: int,
    cache_dir: str | Path,
    threads: int | None,
    base_url: str,
    api_key: str,
    batch_size: int,
    timeout_seconds: float,
) -> DenseEncoder:
    _ = cache_dir, threads
    if backend != "openai_compatible":
        raise DenseRoutingError(f"unsupported dense backend: {backend!r}")
    return OpenAICompatibleDenseEncoder(
        model_id=model_id,
        base_url=base_url,
        api_key=api_key,
        dimension=dimension,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
    )


@dataclass(frozen=True, slots=True)
class DenseMatch:
    document: ToolDiscoveryDocument
    rank: int
    similarity: float


@dataclass(frozen=True, slots=True)
class _DenseIndex:
    snapshot_id: str
    documents: tuple[ToolDiscoveryDocument, ...]
    segment_document_indexes: NDArray[np.int64]
    vectors: NDArray[np.float32]


class DenseRouteRetriever:
    def __init__(
        self,
        encoder: DenseEncoder,
        *,
        minimum_similarity: float = DEFAULT_DENSE_MIN_SIMILARITY,
        cache_capacity: int = 4,
    ) -> None:
        if (
            isinstance(minimum_similarity, bool)
            or not isinstance(minimum_similarity, (int, float))
            or not np.isfinite(minimum_similarity)
            or not -1.0 <= minimum_similarity <= 1.0
        ):
            raise DenseRoutingError("minimum_similarity must be finite in [-1, 1]")
        if (
            isinstance(cache_capacity, bool)
            or not isinstance(cache_capacity, int)
            or not 1 <= cache_capacity <= 16
        ):
            raise DenseRoutingError("cache_capacity must be between 1 and 16")
        if encoder.dimension < 1:
            raise DenseRoutingError("encoder dimension must be positive")
        self._encoder = encoder
        self._minimum_similarity = float(minimum_similarity)
        self._cache_capacity = cache_capacity
        self._indexes: OrderedDict[str, _DenseIndex] = OrderedDict()
        self._lock = RLock()

    @property
    def model_id(self) -> str:
        return self._encoder.model_id

    @property
    def minimum_similarity(self) -> float:
        return self._minimum_similarity

    def prepare(self, snapshot: DiscoverySnapshot) -> None:
        with self._lock:
            if snapshot.snapshot_id in self._indexes:
                self._indexes.move_to_end(snapshot.snapshot_id)
                return
            self._indexes[snapshot.snapshot_id] = self._build_index(snapshot)
            self._indexes.move_to_end(snapshot.snapshot_id)
            while len(self._indexes) > self._cache_capacity:
                _ = self._indexes.popitem(last=False)

    async def aprepare(self, snapshot: DiscoverySnapshot) -> None:
        await asyncio.to_thread(self.prepare, snapshot)

    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 8,
    ) -> tuple[DenseMatch, ...]:
        return self.retrieve_many((query,), snapshot, top_k=top_k)[0]

    async def aretrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 8,
    ) -> tuple[DenseMatch, ...]:
        return (await self.aretrieve_many((query,), snapshot, top_k=top_k))[0]

    def retrieve_many(
        self,
        queries: Sequence[str],
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 8,
    ) -> tuple[tuple[DenseMatch, ...], ...]:
        if isinstance(top_k, bool) or not 1 <= top_k <= 32:
            raise DenseRoutingError("top_k must be between 1 and 32")
        normalized_queries = _validate_texts(queries)
        if len(normalized_queries) > 32:
            raise DenseRoutingError("dense query batch may contain at most 32 items")
        with self._lock:
            self.prepare(snapshot)
            index = self._indexes[snapshot.snapshot_id]
            if not index.documents:
                return tuple(() for _ in normalized_queries)
            query_matrix = _normalize_matrix(
                self._encoder.encode(normalized_queries),
                expected_rows=len(normalized_queries),
                expected_dimension=self._encoder.dimension,
            )
            if index.vectors.shape[1] != query_matrix.shape[1]:
                raise DenseRoutingError("query and discovery vector dimensions differ")

            segment_scores = index.vectors @ query_matrix.T
            results: list[tuple[DenseMatch, ...]] = []
            for query_index in range(len(normalized_queries)):
                best_by_document = np.full(
                    len(index.documents),
                    -np.inf,
                    dtype=np.float32,
                )
                np.maximum.at(
                    best_by_document,
                    index.segment_document_indexes,
                    segment_scores[:, query_index],
                )
                results.append(
                    self._rank_matches(
                        index.documents,
                        best_by_document,
                        top_k=top_k,
                    )
                )
            return tuple(results)

    async def aretrieve_many(
        self,
        queries: Sequence[str],
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 8,
    ) -> tuple[tuple[DenseMatch, ...], ...]:
        return await asyncio.to_thread(
            self.retrieve_many,
            queries,
            snapshot,
            top_k=top_k,
        )

    def _rank_matches(
        self,
        documents: tuple[ToolDiscoveryDocument, ...],
        best_by_document: NDArray[np.float32],
        *,
        top_k: int,
    ) -> tuple[DenseMatch, ...]:
        ranked = [
            (float(score), document.tool_name, document)
            for document, score in zip(documents, best_by_document, strict=True)
            if float(score) >= self._minimum_similarity
        ]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            DenseMatch(document=document, rank=rank, similarity=score)
            for rank, (score, _, document) in enumerate(ranked[:top_k], start=1)
        )

    def _build_index(self, snapshot: DiscoverySnapshot) -> _DenseIndex:
        if not snapshot.documents:
            return _DenseIndex(
                snapshot_id=snapshot.snapshot_id,
                documents=(),
                segment_document_indexes=np.empty(0, dtype=np.int64),
                vectors=np.empty((0, self._encoder.dimension), dtype=np.float32),
            )

        segments: list[str] = []
        document_indexes: list[int] = []
        for document_index, document in enumerate(snapshot.documents):
            document_segments = _discovery_segments(document)
            segments.extend(document_segments)
            document_indexes.extend([document_index] * len(document_segments))
        vectors = _normalize_matrix(
            self._encoder.encode(tuple(segments)),
            expected_rows=len(segments),
            expected_dimension=self._encoder.dimension,
        )
        return _DenseIndex(
            snapshot_id=snapshot.snapshot_id,
            documents=snapshot.documents,
            segment_document_indexes=np.asarray(document_indexes, dtype=np.int64),
            vectors=vectors,
        )


def _discovery_segments(document: ToolDiscoveryDocument) -> tuple[str, ...]:
    terms = "、".join(document.parameter_terms) or "无"
    outputs = "、".join(document.output_kinds)
    primary = (
        f"操作 {document.operation_id}。{document.summary}。"
        f"参数或对象：{terms}。输出：{outputs}。"
    )
    return (primary, *(f"用户示例：{example}" for example in document.examples))


def _validate_texts(texts: Sequence[str]) -> list[str]:
    if isinstance(texts, (str, bytes)) or not texts:
        raise DenseRoutingError("encoder input must contain at least one text")
    validated: list[str] = []
    for text in texts:
        if not isinstance(text, str) or not text.strip() or len(text) > 16_000:
            raise DenseRoutingError("encoder text must be non-empty and bounded")
        validated.append(text)
    return validated


def _normalize_matrix(
    vectors: object,
    *,
    expected_rows: int,
    expected_dimension: int,
) -> NDArray[np.float32]:
    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise DenseRoutingError("encoder output is not a numeric matrix") from exc
    if matrix.ndim != 2 or matrix.shape != (expected_rows, expected_dimension):
        raise DenseRoutingError(
            "encoder output shape mismatch: "
            f"expected {(expected_rows, expected_dimension)}, got {matrix.shape}"
        )
    if not bool(np.isfinite(matrix).all()):
        raise DenseRoutingError("encoder output contains non-finite values")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not bool((norms > 0).all()) or not bool(np.isfinite(norms).all()):
        raise DenseRoutingError("encoder output contains zero or invalid vectors")
    normalized = matrix / norms
    return cast(NDArray[np.float32], np.asarray(normalized, dtype=np.float32))


__all__ = [
    "DEFAULT_DENSE_DIMENSION",
    "DEFAULT_DENSE_MIN_SIMILARITY",
    "DEFAULT_DENSE_MODEL_ID",
    "DenseEncoder",
    "DenseMatch",
    "DenseRouteRetriever",
    "DenseRoutingError",
    "OpenAICompatibleDenseEncoder",
    "build_dense_encoder",
]
