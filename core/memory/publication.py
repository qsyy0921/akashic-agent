from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

PENDING_MEMORY_TAGS = frozenset(
    {
        "identity",
        "preference",
        "key_info",
        "health_long_term",
        "requested_memory",
        "correction",
        "agent_context",
    }
)

_TAGGED_CANDIDATE = re.compile(r"^- \[([a-z_]+)\]\s+(.+)$")
_KEY_VALUE = re.compile(r"^([^:：]{1,64})[:：]\s*(.+)$")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"\b(?:api[_ -]?key|access[_ -]?token|bot[_ -]?token|password|secret)"
        r"\s*[:=：]\s*[^\s,;]{8,}",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class MemoryPublicationPrivacyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    digest: str
    tag: str
    content: str
    rendered: str


@dataclass(frozen=True, slots=True)
class MemoryCandidateRejection:
    digest: str
    tag: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class MemoryPublicationDecision:
    pending_digest: str
    accepted: tuple[MemoryCandidate, ...]
    rejected: tuple[MemoryCandidateRejection, ...]

    @property
    def accepted_markdown(self) -> str:
        return "\n".join(candidate.rendered for candidate in self.accepted)

    def audit_payload(self) -> dict[str, object]:
        return {
            "pending_digest": self.pending_digest,
            "accepted": [
                {"candidate_digest": item.digest, "tag": item.tag}
                for item in self.accepted
            ],
            "rejected": [
                {
                    "candidate_digest": item.digest,
                    "tag": item.tag,
                    "reason_code": item.reason_code,
                }
                for item in self.rejected
            ],
        }


class MemoryPublicationGate:
    def evaluate(self, *, pending: str, current_memory: str) -> MemoryPublicationDecision:
        accepted: list[MemoryCandidate] = []
        rejected: list[MemoryCandidateRejection] = []
        seen: set[str] = set()
        current_entries, current_values = _current_memory_index(current_memory)

        for raw_line in pending.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            candidate, parse_rejection = _parse_candidate(line)
            if parse_rejection is not None:
                rejected.append(parse_rejection)
                continue
            assert candidate is not None
            if candidate.digest in seen:
                rejected.append(_reject(candidate, "duplicate_candidate"))
                continue
            seen.add(candidate.digest)
            if contains_secret(candidate.content):
                rejected.append(_reject(candidate, "privacy_secret"))
                continue
            normalized_content = _normalize(candidate.content)
            if normalized_content in current_entries:
                rejected.append(_reject(candidate, "already_published"))
                continue
            key_value = _split_key_value(candidate.content)
            if key_value is not None and candidate.tag != "correction":
                key, value = key_value
                existing_values = current_values.get(key, frozenset())
                if existing_values and value not in existing_values:
                    rejected.append(_reject(candidate, "conflict_requires_correction"))
                    continue
            accepted.append(candidate)

        return MemoryPublicationDecision(
            pending_digest=_digest(pending),
            accepted=tuple(accepted),
            rejected=tuple(rejected),
        )


def ensure_memory_has_no_secrets(content: str) -> None:
    if contains_secret(content):
        raise MemoryPublicationPrivacyError(
            "MEMORY.md candidate contains secret-bearing content"
        )


def contains_secret(content: str) -> bool:
    return any(pattern.search(content) is not None for pattern in _SECRET_PATTERNS)


def memory_content_digest(content: str) -> str:
    return _digest(content)


def _parse_candidate(
    line: str,
) -> tuple[MemoryCandidate | None, MemoryCandidateRejection | None]:
    match = _TAGGED_CANDIDATE.fullmatch(line)
    if match is not None:
        tag = match.group(1)
        content = _collapse_whitespace(match.group(2))
        candidate = _candidate(tag=tag, content=content, rendered=f"- [{tag}] {content}")
        if tag not in PENDING_MEMORY_TAGS:
            return None, _reject(candidate, "unsupported_tag")
        if len(content) > 1000:
            return None, _reject(candidate, "candidate_too_long")
        return candidate, None
    if line.startswith("- "):
        content = _collapse_whitespace(line[2:])
        candidate = _candidate(tag="legacy", content=content, rendered=f"- {content}")
        if not content:
            return None, _reject(candidate, "invalid_format")
        if len(content) > 1000:
            return None, _reject(candidate, "candidate_too_long")
        return candidate, None
    digest = _digest(line)
    return None, MemoryCandidateRejection(
        digest=digest,
        tag="invalid",
        reason_code="invalid_format",
    )


def _candidate(*, tag: str, content: str, rendered: str) -> MemoryCandidate:
    return MemoryCandidate(
        digest=_digest(f"{tag}\0{_normalize(content)}"),
        tag=tag,
        content=content,
        rendered=rendered,
    )


def _reject(candidate: MemoryCandidate, reason_code: str) -> MemoryCandidateRejection:
    return MemoryCandidateRejection(
        digest=candidate.digest,
        tag=candidate.tag,
        reason_code=reason_code,
    )


def _current_memory_index(
    content: str,
) -> tuple[set[str], dict[str, frozenset[str]]]:
    entries: set[str] = set()
    mutable_values: dict[str, set[str]] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        entry = _TAGGED_CANDIDATE.sub(r"- \2", line)[2:].strip()
        entries.add(_normalize(entry))
        key_value = _split_key_value(entry)
        if key_value is not None:
            key, value = key_value
            mutable_values.setdefault(key, set()).add(value)
    return entries, {key: frozenset(values) for key, values in mutable_values.items()}


def _split_key_value(content: str) -> tuple[str, str] | None:
    match = _KEY_VALUE.fullmatch(content.strip())
    if match is None:
        return None
    return _normalize_key(match.group(1)), _normalize(match.group(2))


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _normalize(value: str) -> str:
    return _collapse_whitespace(value).casefold()


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "MemoryCandidate",
    "MemoryCandidateRejection",
    "MemoryPublicationDecision",
    "MemoryPublicationGate",
    "MemoryPublicationPrivacyError",
    "PENDING_MEMORY_TAGS",
    "contains_secret",
    "ensure_memory_has_no_secrets",
    "memory_content_digest",
]
