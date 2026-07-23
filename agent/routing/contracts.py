from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

RouteStatus = Literal["resolved", "no_tool", "ambiguous", "unavailable"]
DecisionBand = Literal[
    "high_margin", "low_margin", "no_match", "not_applicable"
]
GoalRelation = Literal[
    "independent", "after", "on_success", "on_failure", "if_condition"
]
OutputKind = Literal["text", "image", "file", "data", "mixed"]
ContextRole = Literal["user", "assistant"]
ToolRequirement = Literal["required", "optional", "none"]
UncertaintyKind = Literal[
    "unresolved_reference", "missing_required_input", "operation_conflict"
]

_ROUTE_STATUSES = frozenset({"resolved", "no_tool", "ambiguous", "unavailable"})
_DECISION_BANDS = frozenset(
    {"high_margin", "low_margin", "no_match", "not_applicable"}
)
_GOAL_RELATIONS = frozenset(
    {"independent", "after", "on_success", "on_failure", "if_condition"}
)
_OUTPUT_KINDS = frozenset({"text", "image", "file", "data", "mixed"})
_CONTEXT_ROLES = frozenset({"user", "assistant"})
_TOOL_REQUIREMENTS = frozenset({"required", "optional", "none"})
_UNCERTAINTY_KINDS = frozenset(
    {"unresolved_reference", "missing_required_input", "operation_conflict"}
)
_SOURCE_TYPES = frozenset({"core", "plugin", "mcp"})
_RISKS = frozenset({"read", "write", "external_side_effect", "privileged"})
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RouteContractError(ValueError):
    """Raised when routing input or output violates a versioned contract."""


@dataclass(frozen=True, slots=True)
class RouteContextMessage:
    role: ContextRole
    content: str
    operation_ids: tuple[str, ...] = ()
    output_kinds: tuple[OutputKind, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in _CONTEXT_ROLES:
            raise RouteContractError(f"unsupported context role: {self.role!r}")
        _require_text("context content", self.content, maximum=1_000)
        _require_string_tuple_like(
            "context operation_ids",
            self.operation_ids,
            expected=tuple,
            maximum=4,
            item_maximum=128,
        )
        for operation_id in self.operation_ids:
            _require_operation_id(operation_id)
        _require_output_kinds(
            "context output_kinds", self.output_kinds, allow_empty=True
        )


@dataclass(frozen=True, slots=True)
class RouteContext:
    messages: tuple[RouteContextMessage, ...] = ()
    reply_excerpt: str | None = None
    attachment_kinds: tuple[OutputKind, ...] = ()
    previous_operation_ids: tuple[str, ...] = ()
    pending_clarifications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple) or len(self.messages) > 8:
            raise RouteContractError(
                "context messages must be a tuple with at most eight items"
            )
        if any(not isinstance(item, RouteContextMessage) for item in self.messages):
            raise RouteContractError("context messages must contain RouteContextMessage")
        if sum(len(item.content) for item in self.messages) > 6_000:
            raise RouteContractError("context message text exceeds the total limit")
        if self.reply_excerpt is not None:
            _require_text("reply_excerpt", self.reply_excerpt, maximum=800)
        _require_output_kinds(
            "attachment_kinds", self.attachment_kinds, allow_empty=True
        )
        _require_string_tuple_like(
            "previous_operation_ids",
            self.previous_operation_ids,
            expected=tuple,
            maximum=8,
            item_maximum=128,
        )
        for operation_id in self.previous_operation_ids:
            _require_operation_id(operation_id)
        _require_string_tuple_like(
            "pending_clarifications",
            self.pending_clarifications,
            expected=tuple,
            maximum=4,
            item_maximum=400,
        )


@dataclass(frozen=True, slots=True)
class HypotheticalCapability:
    name: str
    description: str
    required_inputs: tuple[str, ...] = ()
    desired_outputs: tuple[OutputKind, ...] = ()
    missing_required_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("hypothetical capability name", self.name, maximum=80)
        _require_text(
            "hypothetical capability description", self.description, maximum=400
        )
        _require_string_tuple_like(
            "hypothetical required_inputs",
            self.required_inputs,
            expected=tuple,
            maximum=8,
            item_maximum=80,
        )
        _require_output_kinds(
            "hypothetical desired_outputs", self.desired_outputs, allow_empty=True
        )
        _require_string_tuple_like(
            "hypothetical missing_required_inputs",
            self.missing_required_inputs,
            expected=tuple,
            maximum=8,
            item_maximum=80,
        )
        if not set(self.missing_required_inputs).issubset(self.required_inputs):
            raise RouteContractError(
                "missing required inputs must be declared required inputs"
            )

    @property
    def retrieval_query(self) -> str:
        parts = [self.name]
        if self.description != self.name:
            parts.append(self.description)
        parts.extend(self.required_inputs)
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class IntentGoal:
    goal_id: str
    statement: str
    rewritten_intent: str
    hypothetical_capabilities: tuple[HypotheticalCapability, ...] = ()
    alternative_capability_queries: tuple[str, ...] = ()
    relation: GoalRelation = "independent"
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("intent goal_id", self.goal_id, maximum=64)
        _require_text("intent statement", self.statement, maximum=1_000)
        _require_text("rewritten_intent", self.rewritten_intent, maximum=1_000)
        if (
            not isinstance(self.hypothetical_capabilities, tuple)
            or len(self.hypothetical_capabilities) > 3
            or any(
                not isinstance(item, HypotheticalCapability)
                for item in self.hypothetical_capabilities
            )
        ):
            raise RouteContractError(
                "hypothetical_capabilities must contain at most three capabilities"
            )
        _require_string_tuple_like(
            "alternative_capability_queries",
            self.alternative_capability_queries,
            expected=tuple,
            maximum=3,
            item_maximum=80,
        )
        if self.relation not in _GOAL_RELATIONS:
            raise RouteContractError(f"unsupported goal relation: {self.relation!r}")
        _require_string_tuple_like(
            "intent depends_on", self.depends_on, expected=tuple, maximum=4
        )
        if self.goal_id in self.depends_on:
            raise RouteContractError("intent goal cannot depend on itself")
        if self.relation == "independent" and self.depends_on:
            raise RouteContractError(
                "independent intent goal cannot declare dependencies"
            )
        if self.relation != "independent" and not self.depends_on:
            raise RouteContractError("dependent intent goal must declare depends_on")


@dataclass(frozen=True, slots=True)
class IntentView:
    schema_version: str
    goals: tuple[IntentGoal, ...]
    unresolved_references: tuple[str, ...]
    tool_requirement: ToolRequirement

    def __post_init__(self) -> None:
        if self.schema_version != "3":
            raise RouteContractError("unsupported intent view schema version")
        if (
            not isinstance(self.goals, tuple)
            or not 1 <= len(self.goals) <= 4
            or any(not isinstance(goal, IntentGoal) for goal in self.goals)
        ):
            raise RouteContractError("intent view must contain one to four goals")
        goal_ids = [goal.goal_id for goal in self.goals]
        if len(goal_ids) != len(set(goal_ids)):
            raise RouteContractError("intent goal ids must be unique")
        _validate_dependency_dag(self.goals)
        _require_string_tuple_like(
            "unresolved_references",
            self.unresolved_references,
            expected=tuple,
            maximum=8,
            item_maximum=200,
        )
        if self.tool_requirement not in _TOOL_REQUIREMENTS:
            raise RouteContractError(
                f"unsupported tool requirement: {self.tool_requirement!r}"
            )
        capability_count = sum(
            len(goal.hypothetical_capabilities) for goal in self.goals
        )
        missing_input_count = sum(
            len(capability.missing_required_inputs)
            for goal in self.goals
            for capability in goal.hypothetical_capabilities
        )
        if missing_input_count > 8:
            raise RouteContractError(
                "intent view may contain at most eight missing required inputs"
            )
        alternative_count = sum(
            len(goal.alternative_capability_queries) for goal in self.goals
        )
        if self.tool_requirement == "none" and (
            capability_count or alternative_count
        ):
            raise RouteContractError(
                "tool-free intent view cannot declare hypothetical capabilities"
            )
        if self.tool_requirement == "required" and not capability_count:
            raise RouteContractError(
                "required tool intent view must declare a hypothetical capability"
            )


@dataclass(frozen=True, slots=True)
class RouteRequest:
    turn_id: str
    capability_snapshot_id: str
    message: str
    disabled_tools: frozenset[str] = frozenset()
    context: RouteContext | None = None

    def __post_init__(self) -> None:
        _require_text("turn_id", self.turn_id, maximum=256)
        _require_text(
            "capability_snapshot_id", self.capability_snapshot_id, maximum=256
        )
        _require_text("message", self.message, maximum=16_000)
        _require_string_tuple_like(
            "disabled_tools", self.disabled_tools, expected=frozenset, maximum=256
        )
        if self.context is not None and not isinstance(self.context, RouteContext):
            raise RouteContractError("context must be a RouteContext")


@dataclass(frozen=True, slots=True)
class GoalSpec:
    goal_id: str
    statement: str
    operation_query: str
    relation: GoalRelation = "independent"
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("goal_id", self.goal_id, maximum=64)
        _require_text("statement", self.statement, maximum=1_000)
        _require_text("operation_query", self.operation_query, maximum=1_000)
        if self.relation not in _GOAL_RELATIONS:
            raise RouteContractError(f"unsupported goal relation: {self.relation!r}")
        _require_string_tuple_like(
            "depends_on", self.depends_on, expected=tuple, maximum=4
        )
        if self.goal_id in self.depends_on:
            raise RouteContractError("goal cannot depend on itself")
        if self.relation == "independent" and self.depends_on:
            raise RouteContractError("independent goal cannot declare dependencies")
        if self.relation != "independent" and not self.depends_on:
            raise RouteContractError("dependent goal must declare depends_on")


@dataclass(frozen=True, slots=True)
class ToolCandidate:
    tool_name: str
    operation_id: str
    lexical_rank: int | None
    dense_rank: int | None
    fused_rank: int
    reason_codes: tuple[str, ...]
    llm_view_rank: int | None = None

    def __post_init__(self) -> None:
        _require_text("tool_name", self.tool_name, maximum=256)
        _require_operation_id(self.operation_id)
        _require_optional_rank("lexical_rank", self.lexical_rank)
        _require_optional_rank("dense_rank", self.dense_rank)
        _require_optional_rank("llm_view_rank", self.llm_view_rank)
        if (
            self.lexical_rank is None
            and self.dense_rank is None
            and self.llm_view_rank is None
        ):
            raise RouteContractError("candidate must be returned by at least one channel")
        _require_rank("fused_rank", self.fused_rank)
        _require_string_tuple_like(
            "reason_codes", self.reason_codes, expected=tuple, maximum=16
        )


@dataclass(frozen=True, slots=True)
class RouteUncertainty:
    kind: UncertaintyKind
    goal_id: str
    field: str | None
    alternatives: tuple[str, ...]
    reason_code: str

    def __post_init__(self) -> None:
        if self.kind not in _UNCERTAINTY_KINDS:
            raise RouteContractError(f"unsupported uncertainty kind: {self.kind!r}")
        _require_text("uncertainty goal_id", self.goal_id, maximum=64)
        if self.field is not None:
            _require_text("uncertainty field", self.field, maximum=200)
            if any(char in self.field for char in "\r\n\t"):
                raise RouteContractError("uncertainty field must be one line")
        _require_string_tuple_like(
            "uncertainty alternatives",
            self.alternatives,
            expected=tuple,
            maximum=3,
            item_maximum=128,
        )
        if any(any(char in item for char in "\r\n\t") for item in self.alternatives):
            raise RouteContractError("uncertainty alternatives must be one line")
        _require_text("uncertainty reason_code", self.reason_code, maximum=64)
        if any(char in self.reason_code for char in "\r\n\t"):
            raise RouteContractError("uncertainty reason_code must be one line")
        if self.kind in {"unresolved_reference", "missing_required_input"}:
            if self.field is None or self.alternatives:
                raise RouteContractError(
                    "field uncertainty requires one field and no alternatives"
                )
        if self.kind == "operation_conflict" and (
            self.field is not None or not 2 <= len(self.alternatives) <= 3
        ):
            raise RouteContractError(
                "operation conflict requires two or three alternatives"
            )


@dataclass(frozen=True, slots=True)
class GoalRoute:
    goal: GoalSpec
    candidates: tuple[ToolCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.goal, GoalSpec):
            raise RouteContractError("goal must be a GoalSpec")
        if not isinstance(self.candidates, tuple):
            raise RouteContractError("candidates must be a tuple")
        if len(self.candidates) > 3:
            raise RouteContractError("a goal may contain at most three candidates")
        names = [candidate.tool_name for candidate in self.candidates]
        if len(names) != len(set(names)):
            raise RouteContractError("candidate tool names must be unique per goal")
        if [candidate.fused_rank for candidate in self.candidates] != list(
            range(1, len(self.candidates) + 1)
        ):
            raise RouteContractError("candidate fused ranks must be contiguous")


@dataclass(frozen=True, slots=True)
class RouteAdvice:
    schema_version: str
    router_version: str
    turn_id: str
    capability_snapshot_id: str
    discovery_snapshot_id: str
    status: RouteStatus
    decision_band: DecisionBand
    goals: tuple[GoalRoute, ...]
    preloaded_tool_names: tuple[str, ...]
    reason_codes: tuple[str, ...]
    latency_ms: int
    uncertainties: tuple[RouteUncertainty, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version not in {"2", "3"}:
            raise RouteContractError("unsupported route advice schema version")
        _require_text("router_version", self.router_version, maximum=64)
        _require_text("turn_id", self.turn_id, maximum=256)
        _require_text(
            "capability_snapshot_id", self.capability_snapshot_id, maximum=256
        )
        _require_text(
            "discovery_snapshot_id", self.discovery_snapshot_id, maximum=256
        )
        if self.status not in _ROUTE_STATUSES:
            raise RouteContractError(f"unsupported route status: {self.status!r}")
        if self.decision_band not in _DECISION_BANDS:
            raise RouteContractError(
                f"unsupported decision band: {self.decision_band!r}"
            )
        if not isinstance(self.goals, tuple) or len(self.goals) > 4:
            raise RouteContractError("goals must be a tuple with at most four items")
        goal_ids = [route.goal.goal_id for route in self.goals]
        if len(goal_ids) != len(set(goal_ids)):
            raise RouteContractError("goal ids must be unique")
        self._validate_goal_dependencies(set(goal_ids))
        _require_string_tuple_like(
            "preloaded_tool_names",
            self.preloaded_tool_names,
            expected=tuple,
            maximum=5,
        )
        if len(self.preloaded_tool_names) != len(set(self.preloaded_tool_names)):
            raise RouteContractError("preloaded tool names must be unique")
        candidate_names = {
            candidate.tool_name
            for route in self.goals
            for candidate in route.candidates
        }
        if not set(self.preloaded_tool_names).issubset(candidate_names):
            raise RouteContractError("preloaded tools must be route candidates")
        if self.schema_version == "2" and any(
            candidate.llm_view_rank is not None
            for route in self.goals
            for candidate in route.candidates
        ):
            raise RouteContractError("V2 advice cannot contain LLM view ranks")
        if (
            not isinstance(self.uncertainties, tuple)
            or len(self.uncertainties) > 20
            or any(
                not isinstance(item, RouteUncertainty)
                for item in self.uncertainties
            )
        ):
            raise RouteContractError(
                "uncertainties must contain at most twenty RouteUncertainty items"
            )
        if self.schema_version == "2" and self.uncertainties:
            raise RouteContractError("V2 advice cannot contain uncertainties")
        uncertainty_keys = [
            (
                item.kind,
                item.goal_id,
                item.field,
                item.alternatives,
                item.reason_code,
            )
            for item in self.uncertainties
        ]
        if len(uncertainty_keys) != len(set(uncertainty_keys)):
            raise RouteContractError("route uncertainties must be unique")
        if self.goals and not {
            item.goal_id for item in self.uncertainties
        }.issubset(goal_ids):
            raise RouteContractError("uncertainty goal must exist in route goals")
        if self.status != "resolved" and self.preloaded_tool_names:
            raise RouteContractError("only resolved advice may preload tools")
        if self.status == "resolved" and not self.goals:
            raise RouteContractError("resolved advice requires at least one goal")
        if self.status == "no_tool" and self.goals:
            raise RouteContractError("no_tool advice cannot contain goals")
        _require_string_tuple_like(
            "reason_codes", self.reason_codes, expected=tuple, maximum=16
        )
        if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, int):
            raise RouteContractError("latency_ms must be an integer")
        if self.latency_ms < 0:
            raise RouteContractError("latency_ms must be non-negative")

    def _validate_goal_dependencies(self, goal_ids: set[str]) -> None:
        graph: dict[str, tuple[str, ...]] = {}
        for route in self.goals:
            missing = set(route.goal.depends_on) - goal_ids
            if missing:
                raise RouteContractError(
                    f"goal dependencies are missing: {sorted(missing)!r}"
                )
            graph[route.goal.goal_id] = route.goal.depends_on

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(goal_id: str) -> None:
            if goal_id in visiting:
                raise RouteContractError("goal dependencies must form a DAG")
            if goal_id in visited:
                return
            visiting.add(goal_id)
            for dependency in graph[goal_id]:
                visit(dependency)
            visiting.remove(goal_id)
            visited.add(goal_id)

        for goal_id in graph:
            visit(goal_id)


@dataclass(frozen=True, slots=True)
class ToolDiscoveryDocument:
    tool_name: str
    operation_id: str
    summary: str
    parameter_terms: tuple[str, ...]
    examples: tuple[str, ...]
    output_kinds: tuple[OutputKind, ...]
    schema_digest: str
    source_type: str
    source_id: str
    risk: str
    always_on: bool
    healthy: bool

    def __post_init__(self) -> None:
        _require_text("tool_name", self.tool_name, maximum=256)
        _require_operation_id(self.operation_id)
        _require_text("summary", self.summary, maximum=512)
        _require_string_tuple_like(
            "parameter_terms", self.parameter_terms, expected=tuple, maximum=32,
            item_maximum=80,
        )
        _require_string_tuple_like(
            "examples", self.examples, expected=tuple, maximum=8, item_maximum=160
        )
        if not isinstance(self.output_kinds, tuple) or not self.output_kinds:
            raise RouteContractError("output_kinds must be a non-empty tuple")
        if len(self.output_kinds) > 5 or len(set(self.output_kinds)) != len(
            self.output_kinds
        ):
            raise RouteContractError("output_kinds must be unique and bounded")
        if any(kind not in _OUTPUT_KINDS for kind in self.output_kinds):
            raise RouteContractError("unsupported output kind")
        if not _DIGEST.fullmatch(self.schema_digest):
            raise RouteContractError("schema_digest must be a sha256 digest")
        if self.source_type not in _SOURCE_TYPES:
            raise RouteContractError("unsupported source_type")
        _require_text("source_id", self.source_id, maximum=256)
        if self.risk not in _RISKS:
            raise RouteContractError("unsupported risk")
        if not isinstance(self.always_on, bool) or not isinstance(self.healthy, bool):
            raise RouteContractError("always_on and healthy must be booleans")

    def to_payload(self) -> dict[str, object]:
        return {
            "always_on": self.always_on,
            "examples": list(self.examples),
            "healthy": self.healthy,
            "operation_id": self.operation_id,
            "output_kinds": list(self.output_kinds),
            "parameter_terms": list(self.parameter_terms),
            "risk": self.risk,
            "schema_digest": self.schema_digest,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "summary": self.summary,
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    snapshot_id: str
    capability_snapshot_id: str
    documents: tuple[ToolDiscoveryDocument, ...]

    def __post_init__(self) -> None:
        _require_text("capability_snapshot_id", self.capability_snapshot_id, maximum=256)
        if not isinstance(self.documents, tuple):
            raise RouteContractError("documents must be a tuple")
        names = [document.tool_name for document in self.documents]
        if names != sorted(names) or len(names) != len(set(names)):
            raise RouteContractError("discovery documents must be unique and sorted")
        expected = discovery_snapshot_id(
            self.capability_snapshot_id, self.documents
        )
        if self.snapshot_id != expected:
            raise RouteContractError("discovery snapshot id does not match payload")

    @classmethod
    def build(
        cls,
        capability_snapshot_id: str,
        documents: tuple[ToolDiscoveryDocument, ...],
    ) -> "DiscoverySnapshot":
        ordered = tuple(sorted(documents, key=lambda document: document.tool_name))
        return cls(
            snapshot_id=discovery_snapshot_id(capability_snapshot_id, ordered),
            capability_snapshot_id=capability_snapshot_id,
            documents=ordered,
        )


def discovery_snapshot_id(
    capability_snapshot_id: str,
    documents: tuple[ToolDiscoveryDocument, ...],
) -> str:
    payload = {
        "capability_snapshot_id": capability_snapshot_id,
        "documents": [document.to_payload() for document in documents],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"discovery-v1:{digest}"


def _require_operation_id(value: str) -> None:
    if not isinstance(value, str) or not _OPERATION_ID.fullmatch(value):
        raise RouteContractError(f"invalid operation_id: {value!r}")


def _validate_dependency_dag(goals: tuple[IntentGoal, ...]) -> None:
    goal_ids = {goal.goal_id for goal in goals}
    graph: dict[str, tuple[str, ...]] = {}
    for goal in goals:
        missing = set(goal.depends_on) - goal_ids
        if missing:
            raise RouteContractError(
                f"intent goal dependencies are missing: {sorted(missing)!r}"
            )
        graph[goal.goal_id] = goal.depends_on

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(goal_id: str) -> None:
        if goal_id in visiting:
            raise RouteContractError("intent goal dependencies must form a DAG")
        if goal_id in visited:
            return
        visiting.add(goal_id)
        for dependency in graph[goal_id]:
            visit(dependency)
        visiting.remove(goal_id)
        visited.add(goal_id)

    for goal_id in graph:
        visit(goal_id)


def _require_output_kinds(
    field_name: str,
    values: tuple[OutputKind, ...],
    *,
    allow_empty: bool,
) -> None:
    if not isinstance(values, tuple):
        raise RouteContractError(f"{field_name} must be a tuple")
    if (not allow_empty and not values) or len(values) > 5:
        raise RouteContractError(f"{field_name} must be bounded")
    if len(values) != len(set(values)):
        raise RouteContractError(f"{field_name} must be unique")
    if any(value not in _OUTPUT_KINDS for value in values):
        raise RouteContractError(f"{field_name} contains an unsupported output kind")


def _require_text(field_name: str, value: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RouteContractError(f"{field_name} must be non-empty and trimmed")
    if len(value) > maximum or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise RouteContractError(f"{field_name} is too long or contains control characters")


def _require_string_tuple_like(
    field_name: str,
    values: tuple[str, ...] | frozenset[str],
    *,
    expected: type[tuple] | type[frozenset],
    maximum: int,
    item_maximum: int = 256,
) -> None:
    if not isinstance(values, expected):
        raise RouteContractError(f"{field_name} must be a {expected.__name__}")
    if len(values) > maximum or len(values) != len(set(values)):
        raise RouteContractError(f"{field_name} must be unique and bounded")
    for value in values:
        _require_text(field_name, value, maximum=item_maximum)


def _require_optional_rank(field_name: str, value: int | None) -> None:
    if value is not None:
        _require_rank(field_name, value)


def _require_rank(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RouteContractError(f"{field_name} must be a positive integer")


__all__ = [
    "ContextRole",
    "DecisionBand",
    "DiscoverySnapshot",
    "GoalRelation",
    "GoalRoute",
    "GoalSpec",
    "HypotheticalCapability",
    "IntentGoal",
    "IntentView",
    "OutputKind",
    "RouteAdvice",
    "RouteContext",
    "RouteContextMessage",
    "RouteContractError",
    "RouteRequest",
    "RouteStatus",
    "ToolRequirement",
    "ToolCandidate",
    "ToolDiscoveryDocument",
    "discovery_snapshot_id",
]
