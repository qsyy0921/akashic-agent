from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agent.provider import LLMProvider
from agent.model_runtime.errors import RetryableTransportError
from agent.routing.contracts import (
    GoalRelation,
    HypotheticalCapability,
    IntentGoal,
    IntentView,
    OutputKind,
    RouteContext,
    ToolRequirement,
)

INTENT_VIEW_PROMPT_VERSION = "intent-view-v3-8"
_MAX_TRANSPORT_ATTEMPTS = 2
_TRANSPORT_RETRY_DELAY_SECONDS = 0.25

logger = logging.getLogger(__name__)


class IntentViewError(RuntimeError):
    """Base class for explicit IntentView failures."""


class IntentViewUnavailableError(IntentViewError):
    """Raised when the single required provider call cannot complete."""


class IntentViewInvalidError(IntentViewError):
    """Raised when provider output violates the V3 contract."""


class IntentViewProvider(Protocol):
    async def emit_intent_view(
        self,
        message: str,
        context: RouteContext,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class IntentViewAnalysis:
    view: IntentView
    provider_calls: int
    prompt_version: str

    def __post_init__(self) -> None:
        if self.provider_calls != 1:
            raise IntentViewInvalidError(
                "intent view analysis requires exactly one provider call"
            )
        if self.prompt_version != INTENT_VIEW_PROMPT_VERSION:
            raise IntentViewInvalidError("intent view prompt version mismatch")


class IntentViewAnalyzer:
    def __init__(self, provider: IntentViewProvider | None) -> None:
        self._provider = provider

    async def analyze(
        self,
        message: str,
        context: RouteContext,
        *,
        forbidden_tool_names: frozenset[str] = frozenset(),
    ) -> IntentViewAnalysis:
        _validate_message(message)
        if not isinstance(context, RouteContext):
            raise IntentViewInvalidError("route context is invalid")
        if self._provider is None:
            raise IntentViewUnavailableError("intent view provider is unavailable")
        try:
            raw = await self._provider.emit_intent_view(message, context)
        except IntentViewError:
            raise
        except Exception as exc:
            raise IntentViewUnavailableError("intent view provider failed") from exc
        view = _parse_intent_view(raw, context)
        _reject_registered_tool_language(view, forbidden_tool_names)
        return IntentViewAnalysis(
            view=view,
            provider_calls=1,
            prompt_version=INTENT_VIEW_PROMPT_VERSION,
        )


class LLMIntentViewProvider:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str,
        max_tokens: int = 2_000,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise IntentViewUnavailableError("intent view model is missing")
        if isinstance(max_tokens, bool) or not 256 <= max_tokens <= 2_000:
            raise IntentViewUnavailableError(
                "intent view max_tokens must be between 256 and 2000"
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.01 <= float(timeout_seconds) <= 60.0
        ):
            raise IntentViewUnavailableError(
                "intent view timeout_seconds must be between 0.01 and 60"
            )
        self._provider = provider
        self._model = model.strip()
        self._max_tokens = max_tokens
        self._timeout_seconds = float(timeout_seconds)

    async def emit_intent_view(
        self,
        message: str,
        context: RouteContext,
    ) -> object:
        _validate_message(message)
        payload = _serialize_request(message, context)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                for attempt in range(_MAX_TRANSPORT_ATTEMPTS):
                    try:
                        response = await self._provider.chat(
                            messages=[
                                {
                                    "role": "system",
                                    "content": _SYSTEM_PROMPT,
                                },
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        payload,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ),
                                },
                            ],
                            tools=[_EMIT_INTENT_VIEW_TOOL],
                            model=self._model,
                            max_tokens=self._max_tokens,
                            tool_choice={
                                "type": "function",
                                "function": {"name": "emit_intent_view"},
                            },
                            disable_thinking=False,
                        )
                        break
                    except RetryableTransportError:
                        if attempt + 1 >= _MAX_TRANSPORT_ATTEMPTS:
                            raise
                        logger.warning(
                            "intent view transport retry attempt=%d/%d",
                            attempt + 1,
                            _MAX_TRANSPORT_ATTEMPTS - 1,
                        )
                        await asyncio.sleep(_TRANSPORT_RETRY_DELAY_SECONDS)
        except TimeoutError as exc:
            raise IntentViewUnavailableError("intent view provider timed out") from exc
        if len(response.tool_calls) != 1:
            raise IntentViewInvalidError(
                "intent view provider must emit exactly one function call"
            )
        call = response.tool_calls[0]
        if call.name != "emit_intent_view" or not isinstance(call.arguments, dict):
            raise IntentViewInvalidError("intent view function call is invalid")
        return call.arguments


class _IntentGoalSlotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    statement: str = Field(max_length=1_000)
    rewritten_intent: str = Field(max_length=1_000)
    capability_queries: list[str] = Field(max_length=3)
    alternative_capability_queries: list[str] = Field(max_length=3)
    required_inputs: list[str] = Field(max_length=8)
    desired_outputs: list[OutputKind] = Field(max_length=5)
    missing_required_inputs: list[str] = Field(max_length=8)
    relation: GoalRelation
    depends_on_goal_numbers: list[int] = Field(max_length=4)

    @field_validator("statement", "rewritten_intent")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if value and value != value.strip():
            raise ValueError("intent goal text must be trimmed")
        return value

    @field_validator(
        "capability_queries",
        "alternative_capability_queries",
        "required_inputs",
        "missing_required_inputs",
    )
    @classmethod
    def validate_string_lists(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("intent goal string lists must be unique")
        return [
            _trimmed(item, "intent goal list item", maximum=80)
            for item in value
        ]

    @field_validator("desired_outputs")
    @classmethod
    def validate_outputs(cls, value: list[OutputKind]) -> list[OutputKind]:
        if len(value) != len(set(value)):
            raise ValueError("desired outputs must be unique")
        return value

    @field_validator("depends_on_goal_numbers")
    @classmethod
    def validate_dependencies(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("intent dependencies must be unique")
        if any(isinstance(item, bool) or not 1 <= item <= 4 for item in value):
            raise ValueError("intent dependency number is invalid")
        return value

    @model_validator(mode="after")
    def validate_slot(self) -> "_IntentGoalSlotPayload":
        if len(
            dict.fromkeys((*self.required_inputs, *self.missing_required_inputs))
        ) > 8:
            raise ValueError("intent goal has too many normalized required inputs")
        if self.enabled:
            if not self.statement or not self.rewritten_intent:
                raise ValueError("enabled intent goal requires text")
            if not self.capability_queries and (
                self.alternative_capability_queries
                or self.required_inputs
                or self.desired_outputs
                or self.missing_required_inputs
                or self.relation != "independent"
                or self.depends_on_goal_numbers
            ):
                raise ValueError(
                    "non-routing intent goal cannot declare routing fields"
                )
            return self
        if (
            self.statement
            or self.rewritten_intent
            or self.capability_queries
            or self.alternative_capability_queries
            or self.required_inputs
            or self.desired_outputs
            or self.missing_required_inputs
            or self.relation != "independent"
            or self.depends_on_goal_numbers
        ):
            raise ValueError("disabled intent goal slot must be canonical empty")
        return self


class _IntentViewEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["3"]
    goal_1: _IntentGoalSlotPayload
    goal_2: _IntentGoalSlotPayload
    goal_3: _IntentGoalSlotPayload
    goal_4: _IntentGoalSlotPayload
    unresolved_references: list[str] = Field(max_length=8)
    tool_requirement: ToolRequirement

    @property
    def goal_slots(self) -> tuple[_IntentGoalSlotPayload, ...]:
        return (self.goal_1, self.goal_2, self.goal_3, self.goal_4)

    @field_validator("unresolved_references")
    @classmethod
    def validate_references(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("unresolved references must be unique")
        return [_trimmed(item, "unresolved reference", maximum=200) for item in value]

    @model_validator(mode="after")
    def validate_envelope(self) -> "_IntentViewEnvelope":
        enabled = [slot.enabled for slot in self.goal_slots]
        enabled_count = sum(enabled)
        if enabled_count < 1 or enabled != [
            index < enabled_count for index in range(4)
        ]:
            raise ValueError("enabled intent goal slots must be contiguous")
        active = self.goal_slots[:enabled_count]
        for slot in active:
            if slot.relation == "independent" and slot.depends_on_goal_numbers:
                raise ValueError("independent intent goal cannot have dependencies")
            if slot.relation != "independent" and not slot.depends_on_goal_numbers:
                raise ValueError("dependent intent goal requires dependencies")
        capability_count = sum(len(goal.capability_queries) for goal in active)
        alternative_count = sum(
            len(goal.alternative_capability_queries) for goal in active
        )
        missing_input_count = sum(
            len(goal.missing_required_inputs) for goal in active
        )
        if missing_input_count > 8:
            raise ValueError("intent view has too many missing required inputs")
        if self.tool_requirement == "none":
            if capability_count or alternative_count:
                raise ValueError("tool-free intent cannot declare capabilities")
        if self.tool_requirement == "required" and capability_count == 0:
            raise ValueError("required tool view must declare a capability")
        return self


def _parse_intent_view(raw: object, context: RouteContext) -> IntentView:
    if not isinstance(raw, dict):
        raise IntentViewInvalidError("intent view payload must be an object")
    try:
        envelope = _IntentViewEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise IntentViewInvalidError("intent view payload is invalid") from exc
    numbered_slots = tuple(
        (goal_number, slot)
        for goal_number, slot in enumerate(envelope.goal_slots, start=1)
        if slot.enabled
    )
    projected_dependencies: dict[int, tuple[GoalRelation, tuple[int, ...]]] = {}
    for goal_number, slot in numbered_slots:
        relation = slot.relation
        dependencies = tuple(slot.depends_on_goal_numbers)
        if any(number >= goal_number for number in dependencies):
            if (
                goal_number == 1
                and relation == "after"
                and dependencies == (1,)
                and _has_prior_route_evidence(context)
            ):
                relation = "independent"
                dependencies = ()
            else:
                raise IntentViewInvalidError(
                    "intent dependencies must reference earlier goals"
                )
        projected_dependencies[goal_number] = (relation, dependencies)
    routing_slots = (
        numbered_slots
        if envelope.tool_requirement == "none"
        else tuple(
            item for item in numbered_slots if item[1].capability_queries
        )
    )
    retained_numbers = {number for number, _ in routing_slots}
    if any(
        dependency not in retained_numbers
        for goal_number, _ in routing_slots
        for dependency in projected_dependencies[goal_number][1]
    ):
        raise IntentViewInvalidError(
            "routing goal cannot depend on a non-routing goal"
        )
    renumber = {
        original_number: projected_number
        for projected_number, (original_number, _) in enumerate(
            routing_slots, start=1
        )
    }
    return IntentView(
        schema_version=envelope.schema_version,
        goals=tuple(
            IntentGoal(
                goal_id=f"goal-{projected_number}",
                statement=goal.statement,
                rewritten_intent=goal.rewritten_intent,
                hypothetical_capabilities=tuple(
                    HypotheticalCapability(
                        name=query,
                        description=query,
                        required_inputs=(
                            _normalized_required_inputs(goal)
                            if index == 0
                            else ()
                        ),
                        desired_outputs=tuple(goal.desired_outputs),
                        missing_required_inputs=(
                            tuple(goal.missing_required_inputs)
                            if index == 0
                            else ()
                        ),
                    )
                    for index, query in enumerate(goal.capability_queries)
                ),
                alternative_capability_queries=tuple(
                    goal.alternative_capability_queries
                ),
                relation=projected_dependencies[original_number][0],
                depends_on=tuple(
                    f"goal-{renumber[number]}"
                    for number in projected_dependencies[original_number][1]
                ),
            )
            for projected_number, (original_number, goal) in enumerate(
                routing_slots,
                start=1,
            )
        ),
        unresolved_references=tuple(envelope.unresolved_references),
        tool_requirement=envelope.tool_requirement,
    )


def _has_prior_route_evidence(context: RouteContext) -> bool:
    return bool(
        context.reply_excerpt
        or context.previous_operation_ids
        or any(
            message.operation_ids or message.output_kinds
            for message in context.messages
        )
    )


def _normalized_required_inputs(
    goal: _IntentGoalSlotPayload,
) -> tuple[str, ...]:
    """Project wire-level missing inputs into the stricter internal contract."""
    return tuple(
        dict.fromkeys((*goal.required_inputs, *goal.missing_required_inputs))
    )


def _reject_registered_tool_language(
    view: IntentView,
    forbidden_tool_names: frozenset[str],
) -> None:
    normalized_names = tuple(
        name.strip().casefold()
        for name in forbidden_tool_names
        if isinstance(name, str) and len(name.strip()) >= 3
    )
    for goal in view.goals:
        texts = [
            goal.statement,
            goal.rewritten_intent,
            *goal.alternative_capability_queries,
        ]
        for capability in goal.hypothetical_capabilities:
            texts.extend((capability.name, capability.description))
        for text in texts:
            normalized = text.casefold()
            if "mcp_" in normalized or "__" in normalized:
                raise IntentViewInvalidError(
                    "intent view must use generic capability language"
                )
            if any(name in normalized for name in normalized_names):
                raise IntentViewInvalidError(
                    "intent view contains a registered tool identifier"
                )


def _serialize_request(message: str, context: RouteContext) -> dict[str, object]:
    return {
        "current_message": message,
        "route_context": {
            "attachment_kinds": list(context.attachment_kinds),
            "messages": [
                {
                    "content": item.content,
                    "operation_ids": list(item.operation_ids),
                    "output_kinds": list(item.output_kinds),
                    "role": item.role,
                }
                for item in context.messages
            ],
            "pending_clarifications": list(context.pending_clarifications),
            "previous_operation_ids": list(context.previous_operation_ids),
            "reply_excerpt": context.reply_excerpt,
        },
        "schema_version": "3",
    }


def _trimmed(value: str, field_name: str, *, maximum: int | None = None) -> str:
    if value != value.strip() or not value:
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{field_name} is too long")
    return value


def _validate_message(message: str) -> None:
    if (
        not isinstance(message, str)
        or not message.strip()
        or message != message.strip()
        or len(message) > 16_000
    ):
        raise IntentViewInvalidError("intent view message is invalid")


_SYSTEM_PROMPT_BASE = (
    "你是 Akashic Intent Routing V3 的意图视图生成器。"
    "根据 current_message 和 route_context 还原用户此刻的真实目标，处理省略、指代、"
    "意图切换、多目标、顺序和条件依赖，并保留当前消息中的明确约束。"
    "一个 goal 只表示一个需要独立选择 operation 的目标；读取后写入、搜索后生图、"
    "查询后条件通知必须拆成多个 goal。使用 goal_1 到 goal_4 固定槽，已启用目标必须从"
    " goal_1 连续排列，后续槽保持禁用和全空。第一个 goal 必须 independent 且依赖为空；"
    "只有后续目标真实依赖较早目标时才能使用 "
    "after、on_success、on_failure 或 if_condition，禁止自依赖和向后依赖。"
    "depends_on_goal_numbers 只引用本次 current_message 拆出的较早目标；上一轮已经产生的"
    "operation 或输出属于 route_context，消费它的本轮第一个目标仍必须 independent。"
    "每个需要外部能力的目标生成一到三个通用 capability_queries，每项不超过八十字符，"
    "并用 required_inputs 和 desired_outputs 描述所需输入和期望输出。"
    "默认每个目标只生成一个最准确的 capability_query；只有语义检索视角确实不同且都"
    "属于同一 operation 时才生成第二或第三个，禁止为凑数量而改写同义句。"
    "当 current_message 主要不是英文且目标需要外部能力时，第二个 capability_query 必须"
    "是同一 operation 的简短英文能力描述，使用 operation 动词、对象和输入输出语义，"
    "例如 generate image from text description；这是跨语言目录检索视图，不是新目标。"
    "capability_queries 是同一 operation 的等价检索视图；只有用户表达确实可能对应两到"
    "三个不同 operation 类别时，才把这些类别写入 alternative_capability_queries，"
    "否则该数组必须为空。"
    "不得输出或猜测注册工具名、MCP server、provider、权限、"
    "批准结论、执行计划、最终工具参数或思维过程。"
    "纯对话的 tool_requirement 必须为 none，且 capability_queries、required_inputs、"
    "desired_outputs、missing_required_inputs 都必须为空。"
    "例如，用通俗的话解释什么是递归属于纯对话，不需要外部工具。"
    "unresolved_references 只记录会改变 operation 选择、且无法从有界上下文解析的指代；"
    "仅影响执行参数的缺失不要放在这里。required_inputs 表示识别该通用能力所需的信息。"
    "missing_required_inputs 只记录 operation 已经确定、但执行前必须向用户询问的关键输入；"
    "不要记录工具可自行获取、Reasoner 可从完整会话补全、或不影响执行的可选信息，且每项"
    "必须是同一能力 required_inputs 的子集。"
    "current_message 或 route_context 已明确给出的值绝对不能标成 missing；明早八点这类"
    "足以建立提醒的相对日期加明确时刻也不是缺失输入。"
    "创作请求已有高层主题时，风格细节属于可选信息；自然语言联系人姓名已足够交给后续"
    "解析；当地天气位置可由运行时上下文补全，这三类都不要标成 missing。"
    "生成海报、照片、插画或其他视觉内容时 desired_outputs 必须是 image；file 只表示"
    "非图像文件产物。"
    "判例：周六户外烧烤是否受雨影响应识别天气查询，不把当地位置写成未解析或缺失；"
    "这款显示器多少钱应识别联网价格查询，并把显示器型号写为 missing_required_inputs；"
    "明早提醒带伞应拆出提醒目标，并把具体提醒时间写为 missing_required_inputs；"
    "route_context 已给出上一轮 operation 或输出时，再来一张、那周日呢、它会不会影响行程"
    "都必须解析为上一轮目标，不写 unresolved_references；"
    "当前消息出现算了、不用了、改成时，以当前新目标为准，不继承已取消目标。"
    "取消尚未执行的旧动作不是新 goal；只有已经执行且确实需要外部反向操作时才建立目标。"
    "必须仅输出定义过的字段，绝对不要输出 placeholder、临时字段、Markdown、解释、"
    "代码围栏或其他字段。"
)


_GOAL_WIRE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {"type": "boolean"},
        "statement": {"type": "string"},
        "rewritten_intent": {"type": "string"},
        "capability_queries": {
            "type": "array",
            "items": {"type": "string"},
        },
        "alternative_capability_queries": {
            "type": "array",
            "items": {"type": "string"},
        },
        "required_inputs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "desired_outputs": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["text", "image", "file", "data", "mixed"],
            },
        },
        "missing_required_inputs": {
            "type": "array",
            "items": {"type": "string"},
        },
        "relation": {
            "type": "string",
            "enum": [
                "independent",
                "after",
                "on_success",
                "on_failure",
                "if_condition",
            ],
        },
        "depends_on_goal_numbers": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 4},
        },
    },
    "required": [
        "enabled",
        "statement",
        "rewritten_intent",
        "capability_queries",
        "alternative_capability_queries",
        "required_inputs",
        "desired_outputs",
        "missing_required_inputs",
        "relation",
        "depends_on_goal_numbers",
    ],
}

_EMIT_INTENT_VIEW_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "emit_intent_view",
        "description": "Emit one bounded semantic intent view for routing.",
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "schema_version": {"type": "string", "enum": ["3"]},
                "goal_1": _GOAL_WIRE_SCHEMA,
                "goal_2": _GOAL_WIRE_SCHEMA,
                "goal_3": _GOAL_WIRE_SCHEMA,
                "goal_4": _GOAL_WIRE_SCHEMA,
                "unresolved_references": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "tool_requirement": {
                    "type": "string",
                    "enum": ["required", "optional", "none"],
                },
            },
            "required": [
                "schema_version",
                "goal_1",
                "goal_2",
                "goal_3",
                "goal_4",
                "unresolved_references",
                "tool_requirement",
            ],
        },
    },
}

def _wire_goal_slot(
    *,
    enabled: bool,
    statement: str = "",
    rewritten_intent: str = "",
    capability_queries: list[str] | None = None,
    alternative_capability_queries: list[str] | None = None,
    required_inputs: list[str] | None = None,
    desired_outputs: list[str] | None = None,
    missing_required_inputs: list[str] | None = None,
    relation: str = "independent",
    depends_on_goal_numbers: list[int] | None = None,
) -> dict[str, object]:
    return {
        "enabled": enabled,
        "statement": statement,
        "rewritten_intent": rewritten_intent,
        "capability_queries": capability_queries or [],
        "alternative_capability_queries": alternative_capability_queries or [],
        "required_inputs": required_inputs or [],
        "desired_outputs": desired_outputs or [],
        "missing_required_inputs": missing_required_inputs or [],
        "relation": relation,
        "depends_on_goal_numbers": depends_on_goal_numbers or [],
    }


def _wire_output(
    goals: list[dict[str, object]],
    *,
    tool_requirement: str,
    unresolved_references: list[str] | None = None,
) -> dict[str, object]:
    slots = [*goals, *(_wire_goal_slot(enabled=False) for _ in range(4 - len(goals)))]
    return {
        "schema_version": "3",
        **{f"goal_{index}": slot for index, slot in enumerate(slots, start=1)},
        "unresolved_references": unresolved_references or [],
        "tool_requirement": tool_requirement,
    }


_INTENT_VIEW_JSON_TEMPLATE = _wire_output(
    [
        _wire_goal_slot(
            enabled=True,
            statement="用户目标",
            rewritten_intent="结合有界上下文补全后的完整目标",
            capability_queries=["不含真实工具名的通用能力描述"],
            required_inputs=["识别该能力所需的信息"],
            desired_outputs=["text"],
        )
    ],
    tool_requirement="required",
)

_INTENT_VIEW_EXAMPLES: tuple[dict[str, object], ...] = (
    {
        "input": "读取 README，总结后保存到 notes.md",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="读取 README",
                    rewritten_intent="读取项目 README 文件内容",
                    capability_queries=["只读获取指定文本文件内容"],
                    required_inputs=["文件标识"],
                    desired_outputs=["text"],
                ),
                _wire_goal_slot(
                    enabled=True,
                    statement="保存 README 摘要",
                    rewritten_intent="把前序 README 摘要写入 notes.md",
                    capability_queries=["把已有文本保存到指定文件"],
                    required_inputs=["目标文件", "文本内容"],
                    desired_outputs=["file"],
                    relation="after",
                    depends_on_goal_numbers=[1],
                ),
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "处理一下这张图",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="处理用户提供的图片",
                    rewritten_intent="查看或编辑当前已有图片",
                    capability_queries=["处理用户已经提供的图片"],
                    alternative_capability_queries=[
                        "查看并识别已有图片内容",
                        "按用户要求编辑已有图片",
                    ],
                    required_inputs=["图片"],
                    desired_outputs=["mixed"],
                )
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "谢谢你刚才的解释",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="表达感谢",
                    rewritten_intent="对上一轮解释表达感谢",
                )
            ],
            tool_requirement="none",
        ),
    },
    {
        "input": "不用发给他了，改成明早八点提醒我",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="创建明早八点的个人提醒",
                    rewritten_intent="明早八点提醒用户处理原事项",
                    capability_queries=["在明确时间创建个人提醒"],
                    required_inputs=["提醒时间", "提醒内容"],
                    desired_outputs=["text"],
                )
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "提醒一下小王会议改期了",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="提醒小王会议改期",
                    rewritten_intent="通知或定时提醒小王会议已经改期",
                    capability_queries=["处理给指定对象的提醒需求"],
                    alternative_capability_queries=[
                        "立即向指定联系人发送通知消息",
                        "创建未来时间触发的个人提醒",
                    ],
                    required_inputs=["提醒对象", "提醒内容"],
                    desired_outputs=["text"],
                )
            ],
            tool_requirement="required",
            unresolved_references=["提醒方式", "会议新时间"],
        ),
    },
    {
        "input": "查一下明天上海天气，再提醒我早上带伞",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="查询上海明天天气",
                    rewritten_intent="查询上海明天的天气和降雨情况",
                    capability_queries=["查询指定城市和日期的天气预报"],
                    required_inputs=["城市", "日期"],
                    desired_outputs=["data"],
                ),
                _wire_goal_slot(
                    enabled=True,
                    statement="创建明早带伞提醒",
                    rewritten_intent="明早提醒用户带伞",
                    capability_queries=["在明确时间创建个人提醒"],
                    required_inputs=["提醒内容", "具体提醒时间"],
                    desired_outputs=["text"],
                    missing_required_inputs=["具体提醒时间"],
                    relation="after",
                    depends_on_goal_numbers=[1],
                ),
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "把刚才的摘要保存到 summary.md",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="保存上一轮摘要",
                    rewritten_intent="把 route_context 中已有摘要保存到 summary.md",
                    capability_queries=["把已有文本保存到指定文件"],
                    required_inputs=["已有文本", "目标文件"],
                    desired_outputs=["file"],
                )
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "我想知道这款显示器现在大概卖多少钱",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="查询指定显示器当前售价",
                    rewritten_intent="联网查询指定显示器当前市场价格",
                    capability_queries=["联网查询指定商品当前价格"],
                    required_inputs=["商品型号"],
                    desired_outputs=["data"],
                    missing_required_inputs=["商品型号"],
                )
            ],
            tool_requirement="required",
            unresolved_references=["这款显示器"],
        ),
    },
    {
        "input": "搜索最新的火星探测进展，再根据结果生成一张科普海报",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="搜索最新火星探测进展",
                    rewritten_intent="联网搜索最新火星探测新闻和资料",
                    capability_queries=["联网搜索指定主题的最新公开资料"],
                    required_inputs=["搜索主题"],
                    desired_outputs=["text"],
                ),
                _wire_goal_slot(
                    enabled=True,
                    statement="根据搜索结果生成科普海报",
                    rewritten_intent="根据前序资料生成火星探测科普海报图片",
                    capability_queries=["根据已有文字资料生成新的海报图片"],
                    required_inputs=["文字资料", "海报主题"],
                    desired_outputs=["image"],
                    relation="after",
                    depends_on_goal_numbers=[1],
                ),
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "查明天杭州天气，如果下雨就给小王发消息取消徒步",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="查询杭州明天天气",
                    rewritten_intent="查询杭州明天是否下雨",
                    capability_queries=["查询指定城市和日期的天气预报"],
                    required_inputs=["城市", "日期"],
                    desired_outputs=["data"],
                ),
                _wire_goal_slot(
                    enabled=True,
                    statement="下雨时通知小王取消徒步",
                    rewritten_intent="如果前序结果显示下雨，向小王发送取消徒步消息",
                    capability_queries=["向指定联系人发送文字消息"],
                    required_inputs=["联系人", "消息内容"],
                    desired_outputs=["text"],
                    relation="if_condition",
                    depends_on_goal_numbers=[1],
                ),
            ],
            tool_requirement="required",
        ),
    },
    {
        "input": "它会不会影响周六的行程",
        "output": _wire_output(
            [
                _wire_goal_slot(
                    enabled=True,
                    statement="判断前序天气对周六行程的影响",
                    rewritten_intent="结合 route_context 中的周六杭州天气判断出行影响",
                    capability_queries=["查询并解释指定日期地点的天气影响"],
                    required_inputs=["前序天气结果", "行程日期"],
                    desired_outputs=["text"],
                )
            ],
            tool_requirement="optional",
        ),
    },
)

_SYSTEM_PROMPT = (
    _SYSTEM_PROMPT_BASE
    + "严格使用以下字段名和层级；desired_outputs 只能取 text、image、file、data、mixed，"
    "tool_requirement 只能取 required、optional、none。禁用槽必须 enabled=false、文本和数组"
    "为空、relation=independent；depends_on_goal_numbers 只填更早的已启用槽编号："
    + json.dumps(
        _INTENT_VIEW_JSON_TEMPLATE,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    + "以下 JSON 示例只说明拆分和字段规则，不是工具目录："
    + json.dumps(
        _INTENT_VIEW_EXAMPLES,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)


def intent_view_prompt_digest() -> str:
    payload = {
        "prompt": _SYSTEM_PROMPT,
        "prompt_version": INTENT_VIEW_PROMPT_VERSION,
        "output_contract": _EMIT_INTENT_VIEW_TOOL["function"]["parameters"],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


__all__ = [
    "INTENT_VIEW_PROMPT_VERSION",
    "IntentViewAnalysis",
    "IntentViewAnalyzer",
    "IntentViewError",
    "IntentViewInvalidError",
    "IntentViewProvider",
    "IntentViewUnavailableError",
    "LLMIntentViewProvider",
    "intent_view_prompt_digest",
]
