from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentMemoryTurn:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class AgentMemoryCase:
    case_id: str
    benchmark: str
    subset: str
    question: str
    answer: str
    haystack_sessions: list[list[AgentMemoryTurn]]
    question_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])

    def to_longmemeval_item(self) -> dict[str, Any]:
        """Return the JSON shape accepted by eval.longmemeval.dataset."""
        session_dates = list(self.metadata.get("session_dates") or [])
        if len(session_dates) < len(self.haystack_sessions):
            session_dates.extend(
                str(self.metadata.get("session_date", ""))
                for _ in range(len(self.haystack_sessions) - len(session_dates))
            )
        return {
            "question_id": self.case_id,
            "question_type": self.question_type or f"{self.benchmark}:{self.subset}",
            "question": self.question,
            "answer": self.answer,
            "question_date": self.metadata.get("question_date", ""),
            "haystack_session_ids": [
                f"{self.case_id}:s{i}" for i in range(len(self.haystack_sessions))
            ],
            "haystack_dates": [str(value) for value in session_dates],
            "haystack_sessions": [
                [
                    {
                        "role": turn.role,
                        "content": turn.content,
                        "metadata": dict(turn.metadata),
                    }
                    for turn in session
                ]
                for session in self.haystack_sessions
            ],
            "answer_session_ids": list(self.metadata.get("answer_session_ids") or []),
            "source_benchmark": self.benchmark,
            "source_subset": self.subset,
            "source_metadata": dict(self.metadata),
        }


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _stringify(value)
        if text:
            return text
    return ""


def _ama_trajectory_session(row: dict[str, Any]) -> list[AgentMemoryTurn]:
    turns: list[AgentMemoryTurn] = []
    task = _stringify(row.get("task"))
    if task:
        turns.append(
            AgentMemoryTurn(
                role="user",
                content=f"Benchmark task: {task}",
                metadata={"kind": "task"},
            )
        )
    for item in row.get("trajectory") or []:
        if not isinstance(item, dict):
            continue
        turn_idx = item.get("turn_idx")
        action = _stringify(item.get("action"))
        observation = _stringify(item.get("observation"))
        if action:
            turns.append(
                AgentMemoryTurn(
                    role="assistant",
                    content=f"Turn {turn_idx} action: {action}",
                    metadata={"kind": "action", "turn_idx": turn_idx},
                )
            )
        if observation:
            # Store observations as user-side evidence so the existing
            # consolidation path treats environment facts as retrievable evidence.
            turns.append(
                AgentMemoryTurn(
                    role="user",
                    content=f"Turn {turn_idx} observation: {observation}",
                    metadata={"kind": "observation", "turn_idx": turn_idx},
                )
            )
    return turns


def ama_rows_to_cases(
    rows: list[dict[str, Any]],
    *,
    max_qa_per_episode: int = 0,
) -> list[AgentMemoryCase]:
    cases: list[AgentMemoryCase] = []
    for row_index, row in enumerate(rows):
        episode_id = _first_non_empty(row.get("episode_id"), row_index)
        session = _ama_trajectory_session(row)
        qa_pairs = list(row.get("qa_pairs") or [])
        if max_qa_per_episode > 0:
            qa_pairs = qa_pairs[:max_qa_per_episode]
        for qa_index, pair in enumerate(qa_pairs):
            if not isinstance(pair, dict):
                continue
            question = _stringify(pair.get("question"))
            answer = _stringify(pair.get("answer"))
            if not question or not answer:
                continue
            qa_id = _first_non_empty(pair.get("question_uuid"), qa_index)
            cases.append(
                AgentMemoryCase(
                    case_id=f"ama_{episode_id}_{qa_id}",
                    benchmark="ama_bench",
                    subset=str(row.get("task_type") or "default"),
                    question=question,
                    answer=answer,
                    question_type="single-session-user",
                    haystack_sessions=[session],
                    metadata={
                        "episode_id": row.get("episode_id"),
                        "domain": row.get("domain"),
                        "task_type": row.get("task_type"),
                        "qa_type": pair.get("type"),
                        "source_question_type": f"ama:{pair.get('type') or 'unknown'}",
                        "num_turns": row.get("num_turns"),
                        "total_tokens": row.get("total_tokens"),
                    },
                )
            )
    return cases


def _memoryarena_background_session(row: dict[str, Any]) -> list[AgentMemoryTurn]:
    turns: list[AgentMemoryTurn] = []
    background = ""
    if "backgrounds" in row:
        background = _stringify(row.get("backgrounds"))
    elif "base_person" in row:
        background = _stringify(row.get("base_person"))
    if background:
        turns.append(
            AgentMemoryTurn(
                role="user",
                content=f"Background context: {background}",
                metadata={"kind": "background"},
            )
        )
        turns.append(
            AgentMemoryTurn(
                role="assistant",
                content="Noted.",
                metadata={"kind": "background_ack"},
            )
        )
    return turns


def memoryarena_rows_to_cases(
    rows: list[dict[str, Any]],
    *,
    subset: str,
    max_steps_per_row: int = 0,
) -> list[AgentMemoryCase]:
    cases: list[AgentMemoryCase] = []
    for row_index, row in enumerate(rows):
        row_id = _first_non_empty(row.get("id"), row_index)
        questions = [_stringify(item) for item in (row.get("questions") or [])]
        answers = [_stringify(item) for item in (row.get("answers") or [])]
        limit = min(len(questions), len(answers))
        if limit < 2:
            continue
        step_indices = list(range(1, limit))
        if max_steps_per_row > 0:
            step_indices = step_indices[:max_steps_per_row]

        background_session = _memoryarena_background_session(row)
        for step_index in step_indices:
            sessions: list[list[AgentMemoryTurn]] = []
            if background_session:
                sessions.append(background_session)
            for prior_index in range(step_index):
                sessions.append(
                    [
                        AgentMemoryTurn(
                            role="user",
                            content=questions[prior_index],
                            metadata={"kind": "subtask_question", "step": prior_index},
                        ),
                        AgentMemoryTurn(
                            role="assistant",
                            content=answers[prior_index],
                            metadata={"kind": "subtask_answer", "step": prior_index},
                        ),
                    ]
                )
            cases.append(
                AgentMemoryCase(
                    case_id=f"memoryarena_{subset}_{row_id}_step{step_index}",
                    benchmark="memoryarena",
                    subset=subset,
                    question=questions[step_index],
                    answer=answers[step_index],
                    question_type="single-session-user",
                    haystack_sessions=sessions,
                    metadata={
                        "row_id": row.get("id"),
                        "category": row.get("category"),
                        "paper_name": row.get("paper_name"),
                        "target_step": step_index,
                        "total_steps": limit,
                        "source_question_type": f"memoryarena:{subset}",
                    },
                )
            )
    return cases


def _date_key(value: Any) -> str:
    return _stringify(value)[:10]


def _parse_message_indices(spec: Any) -> set[int]:
    text = _stringify(spec)
    indices: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            try:
                start = int(left.strip())
                end = int(right.strip())
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            indices.update(range(start, end + 1))
            continue
        try:
            indices.add(int(part))
        except ValueError:
            continue
    return indices


def _evermem_dialogue_index(
    dialogue_rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in dialogue_rows:
        topic_id = _stringify(row.get("topic_id"))
        date = _date_key(row.get("date"))
        dialogues = row.get("dialogues") or {}
        if not topic_id or not date or not isinstance(dialogues, dict):
            continue
        for group, messages in dialogues.items():
            if not isinstance(messages, list):
                continue
            normalized: list[dict[str, Any]] = []
            for message in messages:
                if isinstance(message, dict):
                    normalized.append(message)
            normalized.sort(key=lambda item: int(item.get("message_index") or 0))
            index[(topic_id, date, str(group))] = normalized
    return index


def _evermem_question(row: dict[str, Any]) -> str:
    question = _stringify(row.get("Q"))
    options = row.get("options")
    if not isinstance(options, dict) or not any(_stringify(v) for v in options.values()):
        return question
    option_lines = [
        f"{key}. {_stringify(value)}"
        for key, value in sorted(options.items())
        if _stringify(value)
    ]
    if not option_lines:
        return question
    return question + "\n\nOptions:\n" + "\n".join(option_lines)


def _evermem_reference_sessions(
    qar: dict[str, Any],
    index: dict[tuple[str, str, str], list[dict[str, Any]]],
    *,
    max_refs_per_case: int = 0,
    max_messages_per_ref: int = 0,
) -> tuple[list[list[AgentMemoryTurn]], list[dict[str, Any]]]:
    topic_id = _stringify(qar.get("topic_id"))
    references = list(qar.get("R") or [])
    if max_refs_per_case > 0:
        references = references[:max_refs_per_case]

    sessions: list[list[AgentMemoryTurn]] = []
    resolved_refs: list[dict[str, Any]] = []
    for ref_index, ref in enumerate(references):
        if not isinstance(ref, dict):
            continue
        date = _date_key(ref.get("date"))
        group = _stringify(ref.get("group"))
        wanted_indices = _parse_message_indices(ref.get("message_index"))
        messages = index.get((topic_id, date, group), [])
        selected = [
            message
            for message in messages
            if int(message.get("message_index") or 0) in wanted_indices
        ]
        if max_messages_per_ref > 0:
            selected = selected[:max_messages_per_ref]
        if not selected:
            resolved_refs.append(
                {
                    "date": date,
                    "group": group,
                    "message_index": ref.get("message_index"),
                    "resolved_messages": 0,
                }
            )
            continue

        session: list[AgentMemoryTurn] = []
        for message in selected:
            message_index = message.get("message_index")
            speaker = _stringify(message.get("speaker"))
            time = _stringify(message.get("time"))
            dialogue = _stringify(message.get("dialogue"))
            if not dialogue:
                continue
            session.append(
                AgentMemoryTurn(
                    role="user",
                    content=(
                        f"[EverMemBench topic={topic_id} date={date} group={group} "
                        f"message_index={message_index} time={time}] {speaker}: {dialogue}"
                    ),
                    metadata={
                        "kind": "group_message",
                        "topic_id": topic_id,
                        "date": date,
                        "group": group,
                        "message_index": message_index,
                        "speaker": speaker,
                        "time": time,
                        "reference_index": ref_index,
                    },
                )
            )
        if session:
            sessions.append(session)
        resolved_refs.append(
            {
                "date": date,
                "group": group,
                "message_index": ref.get("message_index"),
                "resolved_messages": len(session),
            }
        )
    return sessions, resolved_refs


def evermem_rows_to_cases(
    qars: list[dict[str, Any]],
    dialogue_rows: list[dict[str, Any]],
    *,
    max_refs_per_case: int = 0,
    max_messages_per_ref: int = 0,
) -> list[AgentMemoryCase]:
    index = _evermem_dialogue_index(dialogue_rows)
    cases: list[AgentMemoryCase] = []
    for row_index, qar in enumerate(qars):
        qid = _first_non_empty(qar.get("id"), row_index)
        question = _evermem_question(qar)
        answer = _stringify(qar.get("A"))
        if not question or not answer:
            continue
        sessions, resolved_refs = _evermem_reference_sessions(
            qar,
            index,
            max_refs_per_case=max_refs_per_case,
            max_messages_per_ref=max_messages_per_ref,
        )
        if not sessions:
            continue
        cases.append(
            AgentMemoryCase(
                case_id=f"evermem_dynamic_{qid}",
                benchmark="evermembench_dynamic",
                subset="qars_reference",
                question=question,
                answer=answer,
                question_type="single-session-user",
                haystack_sessions=sessions,
                metadata={
                    "topic_id": qar.get("topic_id"),
                    "qar_id": qar.get("id"),
                    "source_question_type": "evermembench_dynamic:qars",
                    "references": resolved_refs,
                    "total_resolved_messages": sum(
                        int(item.get("resolved_messages") or 0) for item in resolved_refs
                    ),
                },
            )
        )
    return cases


def _loads_json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = _stringify(value)
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _socialmem_question(row: dict[str, Any]) -> str:
    question = _stringify(row.get("question"))
    options = _loads_json_value(row.get("options_json"), [])
    if not options:
        options = _loads_json_value(row.get("options"), [])
    if isinstance(options, dict):
        option_lines = [
            f"{key}. {_stringify(value)}"
            for key, value in sorted(options.items())
            if _stringify(value)
        ]
    elif isinstance(options, list):
        option_lines = [
            f"{chr(ord('A') + i)}. {_stringify(value)}"
            for i, value in enumerate(options)
            if _stringify(value)
        ]
    else:
        option_lines = []
    if option_lines:
        return question + "\n\nOptions:\n" + "\n".join(option_lines)
    return question


def _socialmem_answer(row: dict[str, Any]) -> str:
    answer_format = _stringify(row.get("answer_format"))
    correct_option = _stringify(row.get("correct_option"))
    answer = _first_non_empty(row.get("answer"), correct_option)
    if answer_format == "multiple_choice" and correct_option:
        options = _loads_json_value(row.get("options_json"), {})
        if not options:
            options = _loads_json_value(row.get("options"), {})
        option_text = ""
        if isinstance(options, dict):
            option_text = _stringify(options.get(correct_option))
        elif isinstance(options, list) and len(correct_option) == 1:
            index = ord(correct_option.upper()) - ord("A")
            if 0 <= index < len(options):
                option_text = _stringify(options[index])
        option_answer = f"{correct_option}. {option_text}" if option_text else correct_option
        if answer and answer != correct_option and answer != option_text:
            return f"{option_answer}\n{answer}"
        return option_answer
    return answer


def _socialmem_question_type(row: dict[str, Any]) -> str:
    query_type = _stringify(row.get("query_type"))
    if query_type == "Q8":
        return "knowledge-update"
    if query_type in {"Q1", "Q6", "Q9"}:
        return "single-session-preference"
    return "single-session-user"


def _socialmem_conversation_index(
    conversation_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_network: dict[str, list[dict[str, Any]]] = {}
    for row in conversation_rows:
        network_id = _stringify(row.get("network_id"))
        if not network_id:
            continue
        by_network.setdefault(network_id, []).append(row)
    for rows in by_network.values():
        rows.sort(
            key=lambda item: (
                int(item.get("session_index") or 0),
                int(item.get("message_index") or 0),
                _stringify(item.get("turn_id")),
            )
        )
    return by_network


def _socialmem_evidence_turn_ids(row: dict[str, Any]) -> set[str]:
    anchors = _loads_json_value(row.get("evidence_anchors_json"), [])
    if not isinstance(anchors, list):
        return set()
    turn_ids: set[str] = set()
    for anchor in anchors:
        if isinstance(anchor, dict):
            turn_id = _stringify(anchor.get("turn_id"))
            if turn_id:
                turn_ids.add(turn_id)
    return turn_ids


def _socialmem_sessions_for_network(
    network_id: str,
    rows: list[dict[str, Any]],
    *,
    evidence_turn_ids: set[str] | None = None,
) -> tuple[list[list[AgentMemoryTurn]], list[str]]:
    sessions: list[list[AgentMemoryTurn]] = []
    session_dates: list[str] = []
    current_session_id: str | None = None
    current_session: list[AgentMemoryTurn] = []
    current_date = ""

    for row in rows:
        turn_id = _stringify(row.get("turn_id"))
        if evidence_turn_ids is not None and turn_id not in evidence_turn_ids:
            continue
        session_id = _stringify(row.get("session_id"))
        if current_session_id is not None and session_id != current_session_id:
            if current_session:
                sessions.append(current_session)
                session_dates.append(current_date)
            current_session = []
        current_session_id = session_id
        current_date = _first_non_empty(row.get("session_date"), current_date)
        speaker = _stringify(row.get("speaker_display_name"))
        speaker_id = _stringify(row.get("speaker_persona_id"))
        message = _stringify(row.get("message"))
        if not message:
            continue
        current_session.append(
            AgentMemoryTurn(
                role="user",
                content=(
                    f"[SocialMemBench network={network_id} session={session_id} "
                    f"session_index={row.get('session_index')} date={row.get('session_date')} "
                    f"turn_id={turn_id} speaker_id={speaker_id} "
                    f"message_index={row.get('message_index')}] {speaker}: {message}"
                ),
                metadata={
                    "kind": "group_message",
                    "network_id": network_id,
                    "session_id": session_id,
                    "session_index": row.get("session_index"),
                    "date": row.get("session_date"),
                    "turn_id": turn_id,
                    "speaker_id": speaker_id,
                    "speaker": speaker,
                    "message_index": row.get("message_index"),
                },
            )
        )

    if current_session:
        sessions.append(current_session)
        session_dates.append(current_date)
    return sessions, session_dates


def socialmem_rows_to_cases(
    qa_rows: list[dict[str, Any]],
    conversation_rows: list[dict[str, Any]],
    *,
    context: str = "network",
) -> list[AgentMemoryCase]:
    conversation_index = _socialmem_conversation_index(conversation_rows)
    cases: list[AgentMemoryCase] = []
    evidence_only = context == "evidence"
    for row_index, qa in enumerate(qa_rows):
        qa_id = _first_non_empty(qa.get("qa_id"), row_index)
        network_id = _stringify(qa.get("network_id"))
        question = _socialmem_question(qa)
        answer = _socialmem_answer(qa)
        if not network_id or not question or not answer:
            continue
        evidence_turn_ids = _socialmem_evidence_turn_ids(qa) if evidence_only else None
        sessions, session_dates = _socialmem_sessions_for_network(
            network_id,
            conversation_index.get(network_id, []),
            evidence_turn_ids=evidence_turn_ids,
        )
        if not sessions:
            continue
        cases.append(
            AgentMemoryCase(
                case_id=f"socialmem_{qa_id}",
                benchmark="socialmembench",
                subset=context,
                question=question,
                answer=answer,
                question_type=_socialmem_question_type(qa),
                haystack_sessions=sessions,
                metadata={
                    "network_id": network_id,
                    "qa_id": qa.get("qa_id"),
                    "query_type": qa.get("query_type"),
                    "difficulty": qa.get("difficulty"),
                    "answer_format": qa.get("answer_format"),
                    "source_question_type": f"socialmembench:{qa.get('query_type') or 'unknown'}",
                    "context": context,
                    "session_dates": session_dates,
                    "evidence_turn_ids": sorted(_socialmem_evidence_turn_ids(qa)),
                },
            )
        )
    return cases


def _groupmem_channel_sessions(
    domain: str,
    channel: str,
    messages: list[dict[str, Any]],
    *,
    max_context_messages: int,
) -> tuple[list[list[AgentMemoryTurn]], list[str]]:
    selected = messages[: max_context_messages or len(messages)]
    session: list[AgentMemoryTurn] = []
    for message in selected:
        content = _stringify(message.get("content"))
        if not content:
            continue
        session.append(
            AgentMemoryTurn(
                role="user",
                content=(
                    f"[GroupMemBench domain={domain} channel={channel} "
                    f"msg_node={message.get('msg_node')} timestamp={message.get('timestamp')} "
                    f"author={message.get('author')} role={message.get('role')} "
                    f"phase={message.get('phase_name')} topic={message.get('topic')}] "
                    f"{message.get('author')}: {content}"
                ),
                metadata={
                    "kind": "group_message",
                    "domain": domain,
                    "channel": channel,
                    "msg_node": message.get("msg_node"),
                    "timestamp": message.get("timestamp"),
                    "author": message.get("author"),
                    "role": message.get("role"),
                    "phase_name": message.get("phase_name"),
                    "topic": message.get("topic"),
                    "is_decision_point": message.get("is_decision_point"),
                },
            )
        )
    first_date = _stringify(selected[0].get("timestamp"))[:10] if selected else ""
    return ([session] if session else []), ([first_date] if session else [])


def groupmem_domain_to_probe_cases(
    domain: str,
    domain_data: dict[str, Any],
    *,
    limit: int,
    max_context_messages: int = 120,
) -> list[AgentMemoryCase]:
    """Create deterministic probes when the official GroupMemBench QA files are absent.

    The Hugging Face release contains the conversation logs, while the README
    points to a companion repo for official questions. If that repo is not
    accessible, these probes still exercise Akashic on the real group-channel
    logs without presenting the numbers as official benchmark scores.
    """
    case_limit = limit if limit > 0 else None
    cases: list[AgentMemoryCase] = []
    for channel, raw_messages in domain_data.items():
        if not isinstance(raw_messages, list):
            continue
        messages = [m for m in raw_messages if isinstance(m, dict)]
        if not messages:
            continue
        sessions, session_dates = _groupmem_channel_sessions(
            domain,
            str(channel),
            messages,
            max_context_messages=max_context_messages,
        )
        if not sessions:
            continue
        probe_messages = messages if max_context_messages <= 0 else messages[:max_context_messages]
        for message in probe_messages:
            if case_limit is not None and len(cases) >= case_limit:
                return cases
            msg_node = _stringify(message.get("msg_node"))
            phase_name = _stringify(message.get("phase_name"))
            if msg_node and phase_name:
                cases.append(
                    AgentMemoryCase(
                        case_id=f"groupmem_{domain}_{msg_node}_phase",
                        benchmark="groupmembench",
                        subset="metadata_probe",
                        question=(
                            f"In the {domain} domain, what phase was associated "
                            f"with message {msg_node} in the {channel} channel?"
                        ),
                        answer=phase_name,
                        question_type="single-session-user",
                        haystack_sessions=sessions,
                        metadata={
                            "domain": domain,
                            "channel": channel,
                            "msg_node": msg_node,
                            "probe_type": "phase_name",
                            "source_question_type": "groupmembench:metadata_probe",
                            "official_qa": False,
                            "session_dates": session_dates,
                        },
                    )
                )
                continue
            role = _stringify(message.get("role"))
            author = _stringify(message.get("author"))
            if msg_node and author and role:
                cases.append(
                    AgentMemoryCase(
                        case_id=f"groupmem_{domain}_{msg_node}_role",
                        benchmark="groupmembench",
                        subset="metadata_probe",
                        question=(
                            f"What role did {author} have when writing message "
                            f"{msg_node} in the {channel} channel?"
                        ),
                        answer=role,
                        question_type="single-session-user",
                        haystack_sessions=sessions,
                        metadata={
                            "domain": domain,
                            "channel": channel,
                            "msg_node": msg_node,
                            "probe_type": "role",
                            "source_question_type": "groupmembench:metadata_probe",
                            "official_qa": False,
                            "session_dates": session_dates,
                        },
                    )
                )
    return cases


def write_longmemeval_json(cases: list[AgentMemoryCase], path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [case.to_longmemeval_item() for case in cases],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
