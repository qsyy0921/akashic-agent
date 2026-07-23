from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Literal, Mapping
from urllib.parse import urlsplit

IntentRoutingMode = Literal["off", "shadow", "active"]

_ALLOWED_FIELDS = frozenset(
    {
        "mode",
        "gate_report_path",
        "dataset_path",
        "catalog_path",
        "intent_max_tokens",
        "intent_timeout_seconds",
        "embedding_batch_size",
        "embedding_timeout_seconds",
        "dense_min_similarity",
    }
)


class IntentRoutingConfigError(ValueError):
    """Raised when intent-routing configuration is unsafe or unsupported."""


@dataclass(frozen=True, slots=True)
class IntentRoutingConfig:
    mode: IntentRoutingMode = "off"
    gate_report_path: str = "eval/intent_routing/v3-gate-report.json"
    dataset_path: str = "eval/intent_routing/v3_dataset.zh.jsonl"
    catalog_path: str = "eval/intent_routing/catalog.zh.json"
    intent_max_tokens: int = 2_000
    intent_timeout_seconds: float = 30.0
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = field(default="", repr=False, compare=False)
    embedding_dimension: int = 0
    embedding_batch_size: int = 8
    embedding_timeout_seconds: float = 30.0
    dense_min_similarity: float = 0.56

    def __post_init__(self) -> None:
        if self.mode not in {"off", "shadow", "active"}:
            raise IntentRoutingConfigError(
                f"unsupported intent routing mode: {self.mode!r}"
            )
        for field_name in (
            "gate_report_path",
            "dataset_path",
            "catalog_path",
        ):
            _validate_application_relative_path(
                field_name,
                getattr(self, field_name),
            )
        if (
            isinstance(self.intent_max_tokens, bool)
            or not isinstance(self.intent_max_tokens, int)
            or not 256 <= self.intent_max_tokens <= 2_000
        ):
            raise IntentRoutingConfigError(
                "intent routing max tokens must be between 256 and 2000"
            )
        if (
            isinstance(self.intent_timeout_seconds, bool)
            or not isinstance(self.intent_timeout_seconds, (int, float))
            or not 0.01 <= float(self.intent_timeout_seconds) <= 60.0
        ):
            raise IntentRoutingConfigError(
                "intent routing timeout must be between 0.01 and 60 seconds"
            )
        if (
            isinstance(self.embedding_batch_size, bool)
            or not isinstance(self.embedding_batch_size, int)
            or not 1 <= self.embedding_batch_size <= 32
        ):
            raise IntentRoutingConfigError(
                "intent embedding batch size must be between 1 and 32"
            )
        if (
            isinstance(self.embedding_timeout_seconds, bool)
            or not isinstance(self.embedding_timeout_seconds, (int, float))
            or not 0.1 <= float(self.embedding_timeout_seconds) <= 120.0
        ):
            raise IntentRoutingConfigError(
                "intent embedding timeout must be between 0.1 and 120 seconds"
            )
        if (
            isinstance(self.dense_min_similarity, bool)
            or not isinstance(self.dense_min_similarity, (int, float))
            or not -1.0 <= float(self.dense_min_similarity) <= 1.0
        ):
            raise IntentRoutingConfigError(
                "intent dense minimum similarity must be in [-1, 1]"
            )
        if self.mode == "off":
            return
        if (
            not self.embedding_model
            or self.embedding_model != self.embedding_model.strip()
            or len(self.embedding_model) > 200
        ):
            raise IntentRoutingConfigError(
                "intent routing requires the configured memory embedding model"
            )
        _validate_embedding_base_url(self.embedding_base_url)
        if (
            isinstance(self.embedding_dimension, bool)
            or not isinstance(self.embedding_dimension, int)
            or not 1 <= self.embedding_dimension <= 4_096
        ):
            raise IntentRoutingConfigError(
                "intent embedding dimension must be between 1 and 4096"
            )


def load_intent_routing_config(
    raw: Mapping[str, object] | None,
    *,
    embedding_model: str,
    embedding_base_url: str,
    embedding_api_key: str,
    embedding_dimension: int | None,
) -> IntentRoutingConfig:
    values = dict(raw or {})
    unknown = set(values) - _ALLOWED_FIELDS
    if unknown:
        raise IntentRoutingConfigError(
            f"unknown intent routing fields: {sorted(unknown)!r}"
        )
    string_fields = {
        "mode",
        "gate_report_path",
        "dataset_path",
        "catalog_path",
    }
    for field_name in string_fields.intersection(values):
        if not isinstance(values[field_name], str):
            raise IntentRoutingConfigError(
                f"intent routing field must be a string: {field_name}"
            )
    return IntentRoutingConfig(
        **values,  # type: ignore[arg-type]
        embedding_model=embedding_model.strip(),
        embedding_base_url=embedding_base_url.strip(),
        embedding_api_key=embedding_api_key,
        embedding_dimension=int(embedding_dimension or 0),
    )


def resolve_intent_routing_path(
    application_root: Path,
    configured: str,
) -> Path:
    root = application_root.resolve()
    resolved = (root / configured).resolve()
    if not resolved.is_relative_to(root):
        raise IntentRoutingConfigError(
            "intent routing path escapes the application root"
        )
    return resolved


def _validate_application_relative_path(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise IntentRoutingConfigError(
            f"intent routing path must be non-empty and trimmed: {field_name}"
        )
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts:
        raise IntentRoutingConfigError(
            f"intent routing path must stay application-relative: {field_name}"
        )


def _validate_embedding_base_url(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise IntentRoutingConfigError(
            "OpenAI-compatible intent embedding base URL is required"
        )
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise IntentRoutingConfigError(
            "intent embedding base URL must be a plain HTTP(S) endpoint"
        )
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise IntentRoutingConfigError(
            "plaintext intent embedding is only allowed on loopback"
        )


__all__ = [
    "IntentRoutingConfig",
    "IntentRoutingConfigError",
    "IntentRoutingMode",
    "load_intent_routing_config",
    "resolve_intent_routing_path",
]
