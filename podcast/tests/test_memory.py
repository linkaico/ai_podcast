from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pipeline.memory import ConversationMemory


def fixed_now():
    return datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)


def test_memory_adds_turns_and_saves_json(tmp_path):
    memory = ConversationMemory("pilot episode", sessions_dir=tmp_path, now_fn=fixed_now)

    memory.add("user", "Hello")
    memory.add("assistant", "Hi Florian")

    assert memory.episode_name == "pilot_episode"
    assert memory.session_file.exists()

    payload = json.loads(memory.session_file.read_text(encoding="utf-8"))
    assert payload["episode"] == "pilot_episode"
    assert payload["history"] == [
        {
            "role": "user",
            "content": "Hello",
            "created_at": "2026-04-19T12:00:00+00:00",
        },
        {
            "role": "assistant",
            "content": "Hi Florian",
            "created_at": "2026-04-19T12:00:00+00:00",
        },
    ]


def test_memory_trims_to_max_turn_pairs(tmp_path):
    memory = ConversationMemory("pilot", max_turns=2, sessions_dir=tmp_path, now_fn=fixed_now)

    for index in range(6):
        role = "user" if index % 2 == 0 else "assistant"
        memory.add(role, f"turn {index}")

    assert [turn["content"] for turn in memory.get()] == ["turn 2", "turn 3", "turn 4", "turn 5"]


def test_memory_rejects_unknown_roles(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path, now_fn=fixed_now)

    with pytest.raises(ValueError, match="role"):
        memory.add("system", "Nope")


def test_memory_loads_from_session_file_and_appends_to_same_json(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path, now_fn=fixed_now)
    memory.add("user", "First")

    resumed = ConversationMemory.from_session_file(memory.session_file, now_fn=fixed_now)
    resumed.add("assistant", "Second", metadata={"audio_path": "audio/output/ai_turn_0.mp3"})

    assert resumed.episode_name == "pilot"
    assert resumed.session_file == memory.session_file

    payload = json.loads(memory.session_file.read_text(encoding="utf-8"))
    assert [turn["content"] for turn in payload["history"]] == ["First", "Second"]
    assert payload["history"][1]["metadata"] == {"audio_path": "audio/output/ai_turn_0.mp3"}
    assert "artifacts" in payload


def test_latest_for_episode_loads_newest_matching_session(tmp_path):
    first = tmp_path / "pilot_20260419_120000.json"
    second = tmp_path / "pilot_20260419_130000.json"
    first.write_text(json.dumps({"episode": "pilot", "history": []}), encoding="utf-8")
    second.write_text(json.dumps({"episode": "pilot", "history": [{"role": "user", "content": "new"}]}), encoding="utf-8")

    latest = ConversationMemory.latest_for_episode("pilot", tmp_path, now_fn=fixed_now)

    assert latest.session_file == second
    assert latest.get() == [{"role": "user", "content": "new"}]


def test_memory_artifacts_discovers_audio_outputs(tmp_path):
    root = tmp_path
    memory = ConversationMemory("pilot", sessions_dir=root / "sessions", root_dir=root, now_fn=fixed_now)
    input_path = memory.audio_input_dir / "turn_000000.wav"
    output_path = memory.audio_output_dir / "turn_000000.mp3"
    text_path = memory.audio_output_dir / "turn_000000.txt"
    input_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"wav")
    output_path.write_bytes(b"mp3")
    text_path.write_text("text", encoding="utf-8")
    memory.register_artifact(input_path)
    memory.register_artifact(output_path)
    memory.register_artifact(text_path)

    assert memory.artifacts() == {
        "input_wav": [str(input_path.relative_to(root))],
        "output_mp3": [str(output_path.relative_to(root))],
        "dryrun_text": [str(text_path.relative_to(root))],
    }


def test_sessions_use_distinct_media_directories_and_monotonic_turn_ids(tmp_path):
    first = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path, now_fn=fixed_now)
    second = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path, now_fn=fixed_now)

    assert first.session_id != second.session_id
    assert first.audio_output_dir != second.audio_output_dir
    for expected in range(43):
        assert first.reserve_turn_id() == expected
    assert first.next_turn_index() == 43
