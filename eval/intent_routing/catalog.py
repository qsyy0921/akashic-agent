from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agent.routing.advisor import build_discovery_snapshot
from agent.routing.contracts import DiscoverySnapshot
from agent.tools.base import Tool
from agent.tools.registry import ToolRegistry


class EvaluationInputError(ValueError):
    """Raised when a checked-in routing fixture is malformed."""


class _CatalogTool(Tool):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self._name = name
        self._description = description
        self._parameters = parameters

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("evaluation catalog tools cannot execute")


@dataclass(frozen=True, slots=True)
class EvaluationInputs:
    registry: ToolRegistry
    snapshot: DiscoverySnapshot
    cases: list[dict[str, Any]]
    tool_count: int
    goal_count: int


def load_evaluation_inputs(
    dataset_path: Path,
    catalog_path: Path,
) -> EvaluationInputs:
    try:
        catalog_bytes = catalog_path.read_bytes()
        raw_catalog = json.loads(catalog_bytes.decode("utf-8"))
        cases = [
            json.loads(line)
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError("evaluation input cannot be read") from exc
    if not isinstance(raw_catalog, dict) or raw_catalog.get("schema_version") != "1":
        raise EvaluationInputError("catalog schema version is invalid")
    raw_tools = raw_catalog.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise EvaluationInputError("catalog tools must be a non-empty list")
    if any(not isinstance(case, dict) for case in cases):
        raise EvaluationInputError("dataset rows must be objects")

    registry = ToolRegistry()
    for raw_tool in raw_tools:
        item = _require_object(raw_tool, "catalog tool")
        parameters = item.get("parameters")
        if not isinstance(parameters, dict):
            raise EvaluationInputError("catalog tool parameters must be an object")
        registry.register(
            _CatalogTool(
                name=_require_string(item, "tool_name"),
                description=_require_string(item, "description"),
                parameters=cast(dict[str, Any], parameters),
            ),
            risk=_require_string(item, "risk"),
            always_on=_require_bool(item, "always_on"),
            search_hint=_require_string(item, "search_hint"),
            operation_id=_require_string(item, "operation_id"),
            summary=_require_string(item, "summary"),
            parameter_terms=tuple(_require_string_list(item, "parameter_terms")),
            examples=tuple(_require_string_list(item, "examples")),
            output_kinds=tuple(_require_string_list(item, "output_kinds")),
            source_type=_require_string(item, "source_type"),
            source_name=_require_string(item, "source_name"),
        )
    runtime_snapshot_id = (
        "eval-runtime:"
        + hashlib.sha256(catalog_bytes).hexdigest()[:16]
    )
    goal_count = sum(
        len(_require_list(cast(dict[str, Any], case), "goals"))
        for case in cases
    )
    return EvaluationInputs(
        registry=registry,
        snapshot=build_discovery_snapshot(registry, runtime_snapshot_id),
        cases=cast(list[dict[str, Any]], cases),
        tool_count=len(raw_tools),
        goal_count=goal_count,
    )


def _require_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _require_string(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item or item != item.strip():
        raise EvaluationInputError(f"{field} must be a trimmed string")
    return item


def _require_string_list(value: dict[str, Any], field: str) -> list[str]:
    item = value.get(field)
    if not isinstance(item, list) or any(
        not isinstance(entry, str) or not entry or entry != entry.strip()
        for entry in item
    ):
        raise EvaluationInputError(f"{field} must be a trimmed string list")
    return cast(list[str], item)


def _require_list(value: dict[str, Any], field: str) -> list[Any]:
    item = value.get(field)
    if not isinstance(item, list):
        raise EvaluationInputError(f"{field} must be a list")
    return cast(list[Any], item)


def _require_bool(value: dict[str, Any], field: str) -> bool:
    item = value.get(field)
    if not isinstance(item, bool):
        raise EvaluationInputError(f"{field} must be a boolean")
    return item


__all__ = [
    "EvaluationInputError",
    "EvaluationInputs",
    "load_evaluation_inputs",
]
