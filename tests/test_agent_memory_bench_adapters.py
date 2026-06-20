from __future__ import annotations

from eval.agent_memory_bench.dataset import (
    ama_rows_to_cases,
    evermem_rows_to_cases,
    groupmem_domain_to_probe_cases,
    memoryarena_rows_to_cases,
    socialmem_rows_to_cases,
    write_longmemeval_json,
)
from eval.longmemeval.dataset import load_dataset


def test_ama_rows_to_cases_expands_qa_pairs_and_trajectory(tmp_path):
    cases = ama_rows_to_cases(
        [
            {
                "episode_id": 7,
                "task": "win the game",
                "domain": "Game",
                "task_type": "demo",
                "num_turns": 1,
                "total_tokens": 42,
                "trajectory": [
                    {
                        "turn_idx": 0,
                        "action": "look",
                        "observation": "saw a red key",
                    }
                ],
                "qa_pairs": [
                    {
                        "question_uuid": "q1",
                        "type": "A",
                        "question": "What key was observed?",
                        "answer": "red key",
                    },
                    {
                        "question_uuid": "q2",
                        "type": "B",
                        "question": "What action was taken?",
                        "answer": "look",
                    },
                ],
            }
        ]
    )

    assert [case.case_id for case in cases] == ["ama_7_q1", "ama_7_q2"]
    assert cases[0].question_type == "single-session-user"
    assert cases[0].metadata["source_question_type"] == "ama:A"
    session_text = "\n".join(turn.content for turn in cases[0].haystack_sessions[0])
    assert "Benchmark task: win the game" in session_text
    assert "Turn 0 action: look" in session_text
    assert "Turn 0 observation: saw a red key" in session_text

    out = tmp_path / "ama.json"
    write_longmemeval_json(cases, out)
    loaded = load_dataset(out)
    assert len(loaded) == 2
    assert loaded[0].question == "What key was observed?"
    assert loaded[0].answer == "red key"


def test_memoryarena_rows_to_cases_uses_previous_steps_as_history(tmp_path):
    cases = memoryarena_rows_to_cases(
        [
            {
                "id": 3,
                "questions": ["choose a city", "choose a hotel", "final plan"],
                "answers": ["Hangzhou", "West Lake Hotel", "Hangzhou, West Lake Hotel"],
                "base_person": {"name": "Alice", "preference": "lake views"},
            }
        ],
        subset="group_travel_planner",
    )

    assert [case.case_id for case in cases] == [
        "memoryarena_group_travel_planner_3_step1",
        "memoryarena_group_travel_planner_3_step2",
    ]
    first = cases[0]
    assert first.question == "choose a hotel"
    assert first.answer == "West Lake Hotel"
    assert len(first.haystack_sessions) == 2
    assert "Background context" in first.haystack_sessions[0][0].content
    assert first.haystack_sessions[1][0].content == "choose a city"
    assert first.haystack_sessions[1][1].content == "Hangzhou"

    second = cases[1]
    assert second.question == "final plan"
    assert len(second.haystack_sessions) == 3

    out = tmp_path / "memoryarena.json"
    write_longmemeval_json(cases, out)
    loaded = load_dataset(out)
    assert len(loaded) == 2
    assert loaded[1].question == "final plan"
    assert loaded[1].haystack_sessions[-1][0].content == "choose a hotel"


def test_evermem_rows_to_cases_resolves_reference_messages(tmp_path):
    cases = evermem_rows_to_cases(
        [
            {
                "topic_id": "01",
                "id": "F_SH_Top01_001",
                "Q": "What was the peak CPU usage?",
                "A": "65%",
                "R": [
                    {
                        "date": "2025-10-22",
                        "group": "Group 3",
                        "message_index": "1, 4-5",
                    }
                ],
                "options": None,
            }
        ],
        [
            {
                "topic_id": "01",
                "date": "2025-10-22T00:00:00",
                "dialogues": {
                    "Group 3": [
                        {
                            "message_index": 1,
                            "speaker": "Alice",
                            "time": "2025-10-22 09:00:00",
                            "dialogue": "We will run the stress test today.",
                        },
                        {
                            "message_index": 4,
                            "speaker": "Bob",
                            "time": "2025-10-22 11:00:00",
                            "dialogue": "The peak CPU usage was 65%.",
                        },
                        {
                            "message_index": 5,
                            "speaker": "Alice",
                            "time": "2025-10-22 11:01:00",
                            "dialogue": "Record that result in the report.",
                        },
                    ],
                    "Group 1": None,
                    "Group 2": None,
                },
            }
        ],
    )

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "evermem_dynamic_F_SH_Top01_001"
    assert case.question_type == "single-session-user"
    assert case.metadata["total_resolved_messages"] == 3
    session_text = "\n".join(turn.content for turn in case.haystack_sessions[0])
    assert "topic=01 date=2025-10-22 group=Group 3 message_index=4" in session_text
    assert "Bob: The peak CPU usage was 65%." in session_text

    out = tmp_path / "evermem.json"
    write_longmemeval_json(cases, out)
    loaded = load_dataset(out)
    assert len(loaded) == 1
    assert loaded[0].question == "What was the peak CPU usage?"
    assert loaded[0].answer == "65%"


def test_socialmem_rows_to_cases_joins_qa_to_network_sessions(tmp_path):
    cases = socialmem_rows_to_cases(
        [
            {
                "qa_id": "q1",
                "network_id": "grp_1",
                "query_type": "Q8",
                "difficulty": "hard",
                "question": "How did Mei's preference change?",
                "answer": "She moved from tea to coffee.",
                "answer_format": "short_answer",
                "evidence_anchors_json": '[{"turn_id": "t1"}]',
            }
        ],
        [
            {
                "network_id": "grp_1",
                "session_id": "s1",
                "session_index": 1,
                "session_date": "2025-10-01",
                "turn_id": "t1",
                "speaker_persona_id": "p1",
                "speaker_display_name": "Mei",
                "message": "I used to prefer tea, but now coffee works better.",
                "message_index": 1,
            }
        ],
    )

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "socialmem_q1"
    assert case.question_type == "knowledge-update"
    assert case.metadata["session_dates"] == ["2025-10-01"]
    assert "speaker_id=p1" in case.haystack_sessions[0][0].content

    out = tmp_path / "socialmem.json"
    write_longmemeval_json(cases, out)
    loaded = load_dataset(out)
    assert loaded[0].question == "How did Mei's preference change?"
    assert loaded[0].haystack_dates == ["2025-10-01"]


def test_socialmem_multiple_choice_answer_preserves_option_text():
    cases = socialmem_rows_to_cases(
        [
            {
                "qa_id": "q1",
                "network_id": "grp_1",
                "question": "What does Mei prefer now?",
                "answer": "Mei now prefers coffee.",
                "answer_format": "multiple_choice",
                "correct_option": "B",
                "options_json": '{"A": "Tea", "B": "Coffee"}',
            }
        ],
        [
            {
                "network_id": "grp_1",
                "session_id": "s1",
                "session_index": 1,
                "session_date": "2025-10-01",
                "turn_id": "t1",
                "speaker_persona_id": "p1",
                "speaker_display_name": "Mei",
                "message": "Coffee works better for me now.",
                "message_index": 1,
            }
        ],
    )

    assert cases[0].answer == "B. Coffee\nMei now prefers coffee."


def test_groupmem_domain_to_probe_cases_builds_metadata_probe(tmp_path):
    cases = groupmem_domain_to_probe_cases(
        "Finance",
        {
            "Regulatory Compliance Program": [
                {
                    "msg_node": "Msg_1",
                    "content": "We are in the review phase.",
                    "author": "User_1",
                    "role": "Compliance Officer",
                    "timestamp": "2025-07-19T00:00:08",
                    "phase_name": "Identify Applicable Regulations",
                    "topic": "Regulatory Requirements Assessment",
                    "is_decision_point": False,
                }
            ]
        },
        limit=1,
    )

    assert len(cases) == 1
    case = cases[0]
    assert case.benchmark == "groupmembench"
    assert case.subset == "metadata_probe"
    assert case.answer == "Identify Applicable Regulations"
    assert case.metadata["official_qa"] is False
    assert "msg_node=Msg_1" in case.haystack_sessions[0][0].content

    out = tmp_path / "groupmem.json"
    write_longmemeval_json(cases, out)
    loaded = load_dataset(out)
    assert loaded[0].question_type == "single-session-user"
