from __future__ import annotations

from memory2.store import MemoryStore2
from plugins.default_memory.engine import DefaultMemoryEngine


def _item_id(result: str) -> str:
    return result.split(":", 1)[1]


def test_structured_schema_tables_are_created(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")

    tables = {
        str(row[0])
        for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    assert "memory_raw_events" in tables
    assert "memory_entities" in tables
    assert "memory_event_facts" in tables
    assert "memory_assertions" in tables
    assert "memory_relation_facts" in tables


def test_upsert_item_populates_structured_evidence(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")
    result = store.upsert_item(
        "preference",
        "Maya treats cilantro as a dealbreaker when choosing restaurants.",
        embedding=None,
        source_ref="lme:socialmem_Q1_a1b2c3d4:7",
        extra={
            "session_key": "lme:socialmem_Q1_a1b2c3d4",
            "speaker_id": "person:maya",
            "speaker": "Maya",
            "message_index": 7,
            "timestamp": "2025-01-06T12:30:00+00:00",
            "content": "They put cilantro in like everything.",
        },
        happened_at="2025-01-06T12:30:00+00:00",
    )
    item_id = _item_id(result)

    evidence = store.get_structured_evidence_for_items([item_id])[item_id]

    assert evidence["assertion"]["kind"] == "preference"
    assert evidence["assertion"]["status"] == "active"
    assert evidence["source_refs"] == ["lme:socialmem_Q1_a1b2c3d4:7"]
    assert evidence["raw_events"][0]["speaker_id"] == "person:maya"
    assert evidence["raw_events"][0]["speaker"] == "Maya"
    assert evidence["raw_events"][0]["message_index"] == 7
    assert evidence["raw_events"][0]["date"] == "2025-01-06"
    assert evidence["event_facts"][0]["predicate"] == "preference"
    assert evidence["entities"][0]["name"] == "Maya"


def test_replacements_version_structured_assertions(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")
    old_id = _item_id(
        store.upsert_item(
            "profile",
            "Maya prefers loud restaurants.",
            embedding=None,
            source_ref="lme:socialmem_Q2_case:4",
        )
    )
    new_id = _item_id(
        store.upsert_item(
            "profile",
            "Maya prefers quiet restaurants.",
            embedding=None,
            source_ref="lme:socialmem_Q2_case:9",
        )
    )
    old_item = store.get_items_by_ids([old_id])[0]
    new_item = store.get_items_by_ids([new_id])[0]

    store.mark_superseded(old_id)
    store.record_replacements(old_items=[old_item], new_item=new_item)
    evidence = store.get_structured_evidence_for_items([old_id, new_id])

    assert evidence[old_id]["assertion"]["status"] == "superseded"
    assert evidence[old_id]["assertion"]["valid_to"]
    assert evidence[new_id]["assertion"]["version_of"] == old_id


def test_default_memory_engine_attaches_structured_evidence_signal(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")
    item_id = _item_id(
        store.upsert_item(
            "preference",
            "Jordan prefers quieter venues for real conversation.",
            embedding=None,
            source_ref="lme:socialmem_Q4_d4e5f6a7:13",
            extra={
                "speaker_id": "person:jordan",
                "speaker": "Jordan",
                "message_index": 13,
                "content": "Quieter. Better.",
            },
        )
    )
    item = store.get_items_by_ids([item_id])[0]
    item["score"] = 0.9
    engine = DefaultMemoryEngine.__new__(DefaultMemoryEngine)
    engine._v2_store = store

    engine._attach_structured_evidence([item])
    record = engine._build_record(item)

    structured = record.signals["structured_evidence"]
    assert structured["assertion"]["item_id"] == item_id
    assert structured["raw_events"][0]["speaker"] == "Jordan"
    assert structured["raw_events"][0]["message_index"] == 13


def test_structured_evidence_backfills_existing_memory_items(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")
    item_id = _item_id(
        store.upsert_item(
            "event",
            "Emeka first hesitated because Adaeze's schedule was unknown.",
            embedding=None,
            source_ref="lme:socialmem_Q4_n2e5f6a7:3",
        )
    )
    store._db.execute("DELETE FROM memory_assertions WHERE item_id=?", (item_id,))
    store._db.execute("DELETE FROM memory_event_facts WHERE item_id=?", (item_id,))
    store._db.execute("DELETE FROM memory_raw_events WHERE source_ref=?", ("lme:socialmem_Q4_n2e5f6a7:3",))
    store._db.commit()

    evidence = store.get_structured_evidence_for_items([item_id])[item_id]

    assert evidence["assertion"]["item_id"] == item_id
    assert evidence["source_refs"] == ["lme:socialmem_Q4_n2e5f6a7:3"]
    assert evidence["raw_events"][0]["session_key"] == "lme:socialmem_Q4_n2e5f6a7"
    assert evidence["raw_events"][0]["message_index"] == 3


def test_upsert_raw_events_from_messages_parses_socialmem_metadata(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")

    count = store.upsert_raw_events_from_messages(
        [
            {
                "id": "lme:socialmem_Q4_d4e5f6a7:13",
                "session_key": "lme:socialmem_Q4_d4e5f6a7",
                "seq": 13,
                "role": "user",
                "timestamp": "2025-01-06T00:00:00+00:00",
                "content": (
                    "[SocialMemBench network=grp session=grp_s01 date=2025-01-06 "
                    "turn_id=t013 speaker_id=p_jordan message_index=14] "
                    "Jordan: Quieter. Better."
                ),
            }
        ]
    )

    row = store._db.execute(
        "SELECT speaker_id, speaker, message_index, seq, date, content "
        "FROM memory_raw_events WHERE source_ref=?",
        ("lme:socialmem_Q4_d4e5f6a7:13",),
    ).fetchone()

    assert count == 1
    assert row[0] == "p_jordan"
    assert row[1] == "Jordan"
    assert row[2] == 14
    assert row[3] == 13
    assert row[4] == "2025-01-06"
    assert "Quieter. Better." in row[5]


def test_memory_item_projection_does_not_overwrite_raw_message_index(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")
    item_id = _item_id(
        store.upsert_item(
            "event",
            "Jordan prefers quieter venues.",
            embedding=None,
            source_ref="lme:socialmem_Q4_d4e5f6a7:13",
        )
    )
    store.upsert_raw_events_from_messages(
        [
            {
                "id": "lme:socialmem_Q4_d4e5f6a7:13",
                "session_key": "lme:socialmem_Q4_d4e5f6a7",
                "seq": 13,
                "timestamp": "2025-01-06T00:00:00+00:00",
                "content": (
                    "[SocialMemBench date=2025-01-06 speaker_id=p_jordan "
                    "message_index=14] Jordan: Quieter. Better."
                ),
            }
        ]
    )

    store._sync_structured_item_by_id(item_id)
    evidence = store.get_structured_evidence_for_items([item_id])[item_id]

    assert evidence["raw_events"][0]["message_index"] == 14
    assert evidence["raw_events"][0]["seq"] == 13
    assert evidence["raw_events"][0]["speaker"] == "Jordan"


def test_search_raw_events_reads_structured_table(tmp_path) -> None:
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(
        [
            {
                "id": "lme:socialmem_Q4_d4e5f6a7:13",
                "session_key": "lme:socialmem_Q4_d4e5f6a7",
                "seq": 13,
                "timestamp": "2025-01-06T00:00:00+00:00",
                "content": (
                    "[SocialMemBench date=2025-01-06 speaker_id=p_jordan "
                    "message_index=14] Jordan: Quieter. Better."
                ),
            },
            {
                "id": "lme:socialmem_Q4_d4e5f6a7:15",
                "session_key": "lme:socialmem_Q4_d4e5f6a7",
                "seq": 15,
                "timestamp": "2025-01-06T00:00:00+00:00",
                "content": (
                    "[SocialMemBench date=2025-01-06 speaker_id=p_priya "
                    "message_index=16] Priya: I want somewhere you can actually have a conversation."
                ),
            },
        ]
    )

    rows = store.search_raw_events("quieter conversation Jordan Priya", limit=5)

    assert rows
    assert rows[0]["source_ref"] == "lme:socialmem_Q4_d4e5f6a7:13"
    assert rows[0]["speaker"] == "Jordan"
    assert rows[0]["message_index"] == 14
