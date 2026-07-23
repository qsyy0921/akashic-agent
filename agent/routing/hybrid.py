from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol, cast

from agent.routing.contracts import DiscoverySnapshot, ToolDiscoveryDocument
from agent.routing.dense import DenseMatch
from agent.routing.lexical import LexicalMatch

HYBRID_ROUTER_VERSION = "intent-routing-v2-hybrid-1"
DEFAULT_RRF_K = 60
DEFAULT_CHANNEL_LIMIT = 8


class HybridRoutingError(RuntimeError):
    """Raised when lexical/dense fusion cannot satisfy its contract."""


class LexicalRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
    ) -> tuple[LexicalMatch, ...]: ...


class DenseRetriever(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def minimum_similarity(self) -> float: ...

    def prepare(self, snapshot: DiscoverySnapshot) -> None: ...

    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 8,
    ) -> tuple[DenseMatch, ...]: ...

    def retrieve_many(
        self,
        queries: Sequence[str],
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 8,
    ) -> tuple[tuple[DenseMatch, ...], ...]: ...

@dataclass(frozen=True, slots=True)
class HybridMatch:
    document: ToolDiscoveryDocument
    lexical_rank: int | None
    dense_rank: int | None
    fused_rank: int
    reciprocal_rank_score: float
    reason_codes: tuple[str, ...]


class HybridRouteRetriever:
    def __init__(
        self,
        lexical: LexicalRetriever,
        dense: DenseRetriever,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        channel_limit: int = DEFAULT_CHANNEL_LIMIT,
    ) -> None:
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
            raise HybridRoutingError("rrf_k must be a positive integer")
        if (
            isinstance(channel_limit, bool)
            or not isinstance(channel_limit, int)
            or not 1 <= channel_limit <= 32
        ):
            raise HybridRoutingError("channel_limit must be between 1 and 32")
        self._lexical = lexical
        self._dense = dense
        self._rrf_k = rrf_k
        self._channel_limit = channel_limit

    @property
    def model_id(self) -> str:
        return self._dense.model_id

    @property
    def minimum_similarity(self) -> float:
        return self._dense.minimum_similarity

    @property
    def rrf_k(self) -> int:
        return self._rrf_k

    def prepare(self, snapshot: DiscoverySnapshot) -> None:
        self._dense.prepare(snapshot)

    async def aprepare(self, snapshot: DiscoverySnapshot) -> None:
        async_prepare = getattr(self._dense, "aprepare", None)
        if callable(async_prepare):
            prepare = cast(
                Callable[[DiscoverySnapshot], Awaitable[None]],
                async_prepare,
            )
            await prepare(snapshot)
            return
        await asyncio.to_thread(self._dense.prepare, snapshot)

    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 3,
    ) -> tuple[HybridMatch, ...]:
        return self.retrieve_many((query,), snapshot, top_k=top_k)[0]

    async def aretrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 3,
    ) -> tuple[HybridMatch, ...]:
        return (await self.aretrieve_many((query,), snapshot, top_k=top_k))[0]

    def retrieve_many(
        self,
        queries: Sequence[str],
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 3,
    ) -> tuple[tuple[HybridMatch, ...], ...]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 8:
            raise HybridRoutingError("top_k must be between 1 and 8")
        if isinstance(queries, (str, bytes)) or not 1 <= len(queries) <= 32:
            raise HybridRoutingError("query batch must contain one to 32 items")
        validated = tuple(queries)
        if any(not isinstance(query, str) or not query.strip() for query in validated):
            raise HybridRoutingError("queries must be non-empty strings")
        lexical_batches = tuple(
            self._lexical.retrieve(query, snapshot)[: self._channel_limit]
            for query in validated
        )
        dense_batches = self._dense.retrieve_many(
            validated,
            snapshot,
            top_k=self._channel_limit,
        )
        if len(dense_batches) != len(validated):
            raise HybridRoutingError("dense query batch size mismatch")
        return tuple(
            _fuse_channel_matches(
                lexical_matches,
                dense_matches,
                rrf_k=self._rrf_k,
                top_k=top_k,
            )
            for lexical_matches, dense_matches in zip(
                lexical_batches,
                dense_batches,
                strict=True,
            )
        )

    async def aretrieve_many(
        self,
        queries: Sequence[str],
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 3,
    ) -> tuple[tuple[HybridMatch, ...], ...]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 8:
            raise HybridRoutingError("top_k must be between 1 and 8")
        if isinstance(queries, (str, bytes)) or not 1 <= len(queries) <= 32:
            raise HybridRoutingError("query batch must contain one to 32 items")
        validated = tuple(queries)
        if any(not isinstance(query, str) or not query.strip() for query in validated):
            raise HybridRoutingError("queries must be non-empty strings")
        lexical_batches = tuple(
            self._lexical.retrieve(query, snapshot)[: self._channel_limit]
            for query in validated
        )
        async_retrieve = getattr(self._dense, "aretrieve_many", None)
        if callable(async_retrieve):
            retrieve_many = cast(
                Callable[
                    ...,
                    Awaitable[tuple[tuple[DenseMatch, ...], ...]],
                ],
                async_retrieve,
            )
            dense_batches = await retrieve_many(
                validated,
                snapshot,
                top_k=self._channel_limit,
            )
        else:
            dense_batches = await asyncio.to_thread(
                self._dense.retrieve_many,
                validated,
                snapshot,
                top_k=self._channel_limit,
            )
        if len(dense_batches) != len(validated):
            raise HybridRoutingError("dense query batch size mismatch")
        return tuple(
            _fuse_channel_matches(
                lexical_matches,
                dense_matches,
                rrf_k=self._rrf_k,
                top_k=top_k,
            )
            for lexical_matches, dense_matches in zip(
                lexical_batches,
                dense_batches,
                strict=True,
            )
        )


def _fuse_channel_matches(
    lexical_matches: tuple[LexicalMatch, ...],
    dense_matches: tuple[DenseMatch, ...],
    *,
    rrf_k: int,
    top_k: int,
) -> tuple[HybridMatch, ...]:
    lexical_by_name = {match.document.tool_name: match for match in lexical_matches}
    dense_by_name = {match.document.tool_name: match for match in dense_matches}

    fused: list[
        tuple[
            float,
            str,
            ToolDiscoveryDocument,
            LexicalMatch | None,
            DenseMatch | None,
        ]
    ] = []
    for name in sorted(set(lexical_by_name) | set(dense_by_name)):
        lexical = lexical_by_name.get(name)
        dense = dense_by_name.get(name)
        if dense is None and not _strong_lexical_match(lexical):
            continue
        score = 0.0
        if lexical is not None:
            score += 1.0 / (rrf_k + lexical.rank)
        if dense is not None:
            score += 1.0 / (rrf_k + dense.rank)
        document = (
            dense.document if dense is not None else lexical.document  # type: ignore[union-attr]
        )
        fused.append((score, name, document, lexical, dense))

    fused.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        HybridMatch(
            document=document,
            lexical_rank=lexical.rank if lexical is not None else None,
            dense_rank=dense.rank if dense is not None else None,
            fused_rank=fused_rank,
            reciprocal_rank_score=score,
            reason_codes=_reason_codes(lexical, dense),
        )
        for fused_rank, (
            score,
            _,
            document,
            lexical,
            dense,
        ) in enumerate(fused[:top_k], start=1)
    )


def _strong_lexical_match(match: LexicalMatch | None) -> bool:
    if match is None:
        return False
    return bool(
        {"exact_name", "exact_operation", "metadata_high_evidence"}.intersection(
            match.reason_codes
        )
    )


def _reason_codes(
    lexical: LexicalMatch | None,
    dense: DenseMatch | None,
) -> tuple[str, ...]:
    codes = ["rrf"]
    if lexical is not None:
        codes.extend(lexical.reason_codes)
    if dense is not None:
        codes.append("dense")
    return tuple(dict.fromkeys(codes))


__all__ = [
    "DEFAULT_CHANNEL_LIMIT",
    "DEFAULT_RRF_K",
    "HYBRID_ROUTER_VERSION",
    "HybridMatch",
    "HybridRouteRetriever",
    "HybridRoutingError",
]
