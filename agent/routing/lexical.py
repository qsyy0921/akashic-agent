from __future__ import annotations

import re
from dataclasses import dataclass

from agent.routing.contracts import DiscoverySnapshot, ToolDiscoveryDocument
from agent.tools.registry import ToolRegistry

_MIN_STANDALONE_FACTUAL_TOKENS = 3


@dataclass(frozen=True, slots=True)
class LexicalMatch:
    document: ToolDiscoveryDocument
    rank: int
    reason_codes: tuple[str, ...]
    evidence_count: int


class LexicalRouteRetriever:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
    ) -> tuple[LexicalMatch, ...]:
        documents = {document.tool_name: document for document in snapshot.documents}
        results = self._registry.search(query, top_k=max(1, len(documents)))
        matches: list[LexicalMatch] = []
        for rank, result in enumerate(results, start=1):
            name = result.get("name")
            if not isinstance(name, str):
                continue
            document = documents.get(name)
            if document is None:
                continue
            raw_reasons = result.get("why_matched")
            reasons = (
                tuple(item for item in raw_reasons if isinstance(item, str))
                if isinstance(raw_reasons, list)
                else ()
            )
            matches.append(
                LexicalMatch(
                    document=document,
                    rank=rank,
                    reason_codes=_reason_codes(reasons),
                    evidence_count=max(1, len(reasons)),
                )
            )
        return tuple(matches)


class MetadataLexicalRouteRetriever:
    """Ranks discovery metadata without the legacy CJK unigram expansion."""

    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
    ) -> tuple[LexicalMatch, ...]:
        normalized_query = query.strip().lower()
        query_tokens = _metadata_tokens(normalized_query)
        ranked: list[tuple[int, str, LexicalMatch]] = []
        for document in snapshot.documents:
            exact_name = normalized_query == document.tool_name.lower()
            exact_operation = normalized_query == document.operation_id.lower()
            name_overlap = query_tokens & _metadata_tokens(document.tool_name)
            operation_overlap = query_tokens & _metadata_tokens(document.operation_id)
            summary_overlap = query_tokens & _metadata_tokens(document.summary)
            parameter_overlap = query_tokens & _metadata_tokens(
                " ".join(document.parameter_terms)
            )
            example_overlap = query_tokens & _metadata_tokens(
                " ".join(document.examples)
            )
            score = (
                (100 if exact_name or exact_operation else 0)
                + 8 * len(name_overlap)
                + 8 * len(operation_overlap)
                + 3 * len(summary_overlap)
                + 3 * len(parameter_overlap)
                + 4 * len(example_overlap)
            )
            if score < 1:
                continue
            reasons = ["lexical", "metadata_lexical"]
            if exact_name:
                reasons.extend(("exact_name", "name_match"))
            if exact_operation:
                reasons.append("exact_operation")
            if name_overlap:
                reasons.append("name_match")
            if operation_overlap:
                reasons.append("operation_match")
            if summary_overlap:
                reasons.append("summary_match")
            if parameter_overlap:
                reasons.append("parameter_match")
            if example_overlap:
                reasons.append("example_match")
            factual_evidence = set().union(
                name_overlap,
                operation_overlap,
                summary_overlap,
                parameter_overlap,
            )
            if (
                score >= 6
                and len(factual_evidence) >= _MIN_STANDALONE_FACTUAL_TOKENS
            ):
                reasons.append("metadata_high_evidence")
            evidence = set().union(
                name_overlap,
                operation_overlap,
                summary_overlap,
                parameter_overlap,
                example_overlap,
            )
            ranked.append(
                (
                    score,
                    document.tool_name,
                    LexicalMatch(
                        document=document,
                        rank=1,
                        reason_codes=tuple(dict.fromkeys(reasons)),
                        evidence_count=max(1, len(evidence)),
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            LexicalMatch(
                document=match.document,
                rank=rank,
                reason_codes=match.reason_codes,
                evidence_count=match.evidence_count,
            )
            for rank, (_, _, match) in enumerate(ranked, start=1)
        )


def _reason_codes(reasons: tuple[str, ...]) -> tuple[str, ...]:
    codes = ["lexical"]
    if any("精确匹配" in reason for reason in reasons):
        codes.append("exact_name")
    if any(reason.startswith("名称") for reason in reasons):
        codes.append("name_match")
    if any(reason.startswith("提示") for reason in reasons):
        codes.append("hint_match")
    if any(reason.startswith("描述") for reason in reasons):
        codes.append("description_match")
    return tuple(codes)


def _metadata_tokens(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ").replace(".", " ").replace("-", " ")
    tokens = {
        token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) >= 2
    }
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


__all__ = [
    "LexicalMatch",
    "LexicalRouteRetriever",
    "MetadataLexicalRouteRetriever",
]
