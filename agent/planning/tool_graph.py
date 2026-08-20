from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from agent.routing.contracts import RouteAdvice
from agent.tools.registry import ToolMeta, ToolRegistry

PlanStatus = Literal[
    "ready",
    "no_plan",
    "missing_capability",
    "ambiguous_capability",
    "cycle",
    "too_large",
]


@dataclass(frozen=True, slots=True)
class ToolGraphNode:
    tool_name: str
    operation_id: str
    risk: str
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    requires_operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskPlanStep:
    index: int
    tool_name: str
    operation_id: str
    depends_on: tuple[str, ...]
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    risk: str


@dataclass(frozen=True, slots=True)
class TaskPlan:
    status: PlanStatus
    graph_digest: str
    steps: tuple[TaskPlanStep, ...] = ()
    reason_code: str = ""
    unresolved_operations: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(step.tool_name for step in self.steps)

    def step_for_tool(self, tool_name: str) -> TaskPlanStep | None:
        return next(
            (step for step in self.steps if step.tool_name == tool_name),
            None,
        )

    def to_trace_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "graph_digest": self.graph_digest,
            "reason_code": self.reason_code,
            "unresolved_operations": list(self.unresolved_operations),
            "steps": [
                {
                    "index": step.index,
                    "tool_name": step.tool_name,
                    "operation_id": step.operation_id,
                    "depends_on": list(step.depends_on),
                    "risk": step.risk,
                }
                for step in self.steps
            ],
        }

    def render(self) -> str:
        if not self.ready:
            raise ValueError("only a ready task plan can be rendered")
        lines = ["按以下工具依赖顺序完成任务；只有前置步骤成功后才能执行依赖步骤："]
        for step in self.steps:
            dependencies = ", ".join(step.depends_on) or "无"
            lines.append(
                f"{step.index}. {step.tool_name} [{step.operation_id}]；前置工具：{dependencies}"
            )
        return "\n".join(lines)


class ToolGraph:
    """Immutable graph derived from one ToolRegistry snapshot."""

    def __init__(self, nodes: tuple[ToolGraphNode, ...]) -> None:
        names = [node.tool_name for node in nodes]
        if len(names) != len(set(names)):
            raise ValueError("tool graph names must be unique")
        self._nodes = {node.tool_name: node for node in nodes}
        self._order = tuple(names)
        providers: dict[str, list[str]] = {}
        for node in nodes:
            providers.setdefault(node.operation_id, []).append(node.tool_name)
        self._providers = {
            operation: tuple(tool_names)
            for operation, tool_names in providers.items()
        }
        payload: list[dict[str, object]] = [
            {
                "tool_name": node.tool_name,
                "operation_id": node.operation_id,
                "risk": node.risk,
                "consumes": list(node.consumes),
                "produces": list(node.produces),
                "requires_operations": list(node.requires_operations),
            }
            for node in nodes
        ]
        self.digest = "sha256:" + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_registry(cls, registry: ToolRegistry) -> "ToolGraph":
        nodes: list[ToolGraphNode] = []
        for name in registry.get_registered_order():
            meta = registry.get_tool_meta(name)
            if meta is None:
                raise RuntimeError(f"tool graph metadata missing for {name!r}")
            nodes.append(_node_from_meta(name, meta))
        return cls(tuple(nodes))

    def plan(
        self,
        selected_tools: tuple[str, ...],
        *,
        dependencies: dict[str, tuple[str, ...]] | None = None,
        disabled_tools: frozenset[str] = frozenset(),
        max_steps: int = 5,
    ) -> TaskPlan:
        if not selected_tools:
            return TaskPlan("no_plan", self.digest, reason_code="no_selected_tools")
        if len(selected_tools) != len(set(selected_tools)):
            raise ValueError("selected tools must be unique")
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")

        selected = list(selected_tools)
        edges: dict[str, set[str]] = {name: set() for name in selected}
        explicit = dependencies or {}
        for name in selected:
            if name not in self._nodes or name in disabled_tools:
                return TaskPlan(
                    "missing_capability",
                    self.digest,
                    reason_code="selected_tool_unavailable",
                    unresolved_operations=(name,),
                )
            for dependency in explicit.get(name, ()):
                if dependency not in selected:
                    raise ValueError("goal dependency must reference a selected tool")
                edges[name].add(dependency)

        unresolved: set[str] = set()
        queue = list(selected)
        cursor = 0
        while cursor < len(queue):
            tool_name = queue[cursor]
            cursor += 1
            node = self._nodes[tool_name]
            for operation_id in node.requires_operations:
                provider = self._resolve_provider(
                    operation_id,
                    selected=set(queue),
                    disabled_tools=disabled_tools,
                )
                if provider is None:
                    unresolved.add(operation_id)
                    continue
                if provider == "":
                    return TaskPlan(
                        "ambiguous_capability",
                        self.digest,
                        reason_code="required_operation_has_multiple_providers",
                        unresolved_operations=(operation_id,),
                    )
                if provider not in edges:
                    queue.append(provider)
                    edges[provider] = set()
                    if len(queue) > max_steps:
                        return TaskPlan(
                            "too_large",
                            self.digest,
                            reason_code="task_plan_step_limit_exceeded",
                        )
                edges[tool_name].add(provider)

        if unresolved:
            return TaskPlan(
                "missing_capability",
                self.digest,
                reason_code="required_operation_missing",
                unresolved_operations=tuple(sorted(unresolved)),
            )
        if len(queue) > max_steps:
            return TaskPlan(
                "too_large",
                self.digest,
                reason_code="task_plan_step_limit_exceeded",
            )

        ordered = self._topological_order(tuple(queue), edges)
        if ordered is None:
            return TaskPlan(
                "cycle",
                self.digest,
                reason_code="tool_dependency_cycle",
            )
        steps = tuple(
            TaskPlanStep(
                index=index,
                tool_name=name,
                operation_id=self._nodes[name].operation_id,
                depends_on=tuple(
                    dependency
                    for dependency in ordered
                    if dependency in edges[name]
                ),
                consumes=self._nodes[name].consumes,
                produces=self._nodes[name].produces,
                risk=self._nodes[name].risk,
            )
            for index, name in enumerate(ordered, start=1)
        )
        return TaskPlan("ready", self.digest, steps=steps)

    def _resolve_provider(
        self,
        operation_id: str,
        *,
        selected: set[str],
        disabled_tools: frozenset[str],
    ) -> str | None:
        providers = tuple(
            name
            for name in self._providers.get(operation_id, ())
            if name not in disabled_tools
        )
        selected_providers = tuple(name for name in providers if name in selected)
        if len(selected_providers) == 1:
            return selected_providers[0]
        if len(selected_providers) > 1:
            return ""
        if len(providers) == 1:
            return providers[0]
        if not providers:
            return None
        return ""

    def _topological_order(
        self,
        names: tuple[str, ...],
        dependencies: dict[str, set[str]],
    ) -> tuple[str, ...] | None:
        position = {name: index for index, name in enumerate(self._order)}
        preferred = {name: index for index, name in enumerate(names)}
        remaining = {name: set(dependencies[name]) for name in names}
        ordered: list[str] = []
        while remaining:
            ready = [name for name, deps in remaining.items() if not deps]
            if not ready:
                return None
            ready.sort(
                key=lambda name: (
                    preferred.get(name, len(preferred)),
                    position.get(name, len(position)),
                    name,
                )
            )
            for name in ready:
                ordered.append(name)
                _ = remaining.pop(name)
                for deps in remaining.values():
                    deps.discard(name)
        return tuple(ordered)


def build_task_plan(
    advice: RouteAdvice,
    *,
    registry: ToolRegistry,
    disabled_tools: set[str] | None = None,
    max_steps: int = 5,
) -> TaskPlan:
    graph = ToolGraph.from_registry(registry)
    if advice.status != "resolved":
        return TaskPlan(
            "no_plan",
            graph.digest,
            reason_code=f"route_{advice.status}",
        )

    goal_tools: dict[str, str] = {}
    selected: list[str] = []
    for route in advice.goals:
        if not route.candidates:
            return TaskPlan(
                "missing_capability",
                graph.digest,
                reason_code="resolved_goal_has_no_candidate",
                unresolved_operations=(route.goal.goal_id,),
            )
        name = route.candidates[0].tool_name
        goal_tools[route.goal.goal_id] = name
        if name not in selected:
            selected.append(name)

    dependencies: dict[str, list[str]] = {name: [] for name in selected}
    for route in advice.goals:
        current = goal_tools[route.goal.goal_id]
        for goal_id in route.goal.depends_on:
            dependency = goal_tools.get(goal_id)
            if dependency is None:
                raise ValueError("route goal dependency is unavailable")
            if dependency != current and dependency not in dependencies[current]:
                dependencies[current].append(dependency)

    return graph.plan(
        tuple(selected),
        dependencies={name: tuple(items) for name, items in dependencies.items()},
        disabled_tools=frozenset(disabled_tools or set()),
        max_steps=max_steps,
    )


def _node_from_meta(name: str, meta: ToolMeta) -> ToolGraphNode:
    return ToolGraphNode(
        tool_name=name,
        operation_id=meta.operation_id,
        risk=meta.risk,
        consumes=meta.consumes,
        produces=meta.produces,
        requires_operations=meta.requires_operations,
    )
