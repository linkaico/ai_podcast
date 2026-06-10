from __future__ import annotations

import json

import pytest

import integrations.openclaw_tools as openclaw_tools
from config.settings import Settings
from integrations.openclaw_tools import (
    episode_artifacts,
    export_transcript,
    latest_session,
    list_sessions,
    load_session,
    run_episode,
    write_episode_context,
)
from pipeline.memory import ConversationMemory


def test_write_episode_context_creates_prompt_file(tmp_path):
    result = write_episode_context(
        "Pilot Episode",
        "Research context block.",
        sources=[{"title": "Transcript", "url": "https://example.com/transcript"}],
        root_dir=tmp_path,
    )

    path = tmp_path / "config" / "prompts" / "episodes" / "Pilot_Episode.txt"
    text = path.read_text(encoding="utf-8")

    assert result["episode"] == "Pilot_Episode"
    assert result["path"] == str(path)
    assert "# Episode Context: Pilot_Episode" in text
    assert "- Transcript: https://example.com/transcript" in text
    assert "Research context block." in text


def test_session_listing_loading_and_latest_session(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    first = sessions_dir / "pilot_20260419_120000.json"
    second = sessions_dir / "pilot_20260419_130000.json"
    first.write_text(json.dumps({"episode": "pilot", "saved_at": "first", "history": []}), encoding="utf-8")
    second.write_text(
        json.dumps({"episode": "pilot", "saved_at": "second", "history": [{"role": "user", "content": "hi"}]}),
        encoding="utf-8",
    )

    sessions = list_sessions("pilot", root_dir=tmp_path)
    latest = latest_session("pilot", root_dir=tmp_path)
    payload = load_session(second, root_dir=tmp_path)

    assert [session["path"] for session in sessions] == [str(first), str(second)]
    assert latest["path"] == str(second)
    assert latest["turns"] == 1
    assert payload["saved_at"] == "second"


def test_episode_artifacts_finds_saved_files(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    input_path = memory.audio_input_dir / "turn_000000.wav"
    output_path = memory.audio_output_dir / "turn_000000.mp3"
    input_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"wav")
    output_path.write_bytes(b"mp3")
    memory.add("user", "hello", metadata={"audio_path": str(input_path)})
    memory.add("assistant", "hi", metadata={"audio_path": str(output_path)})

    result = episode_artifacts("pilot", root_dir=tmp_path)

    assert result["episode"] == "pilot"
    assert result["artifacts"]["input_wav"] == [str(input_path.relative_to(tmp_path))]
    assert result["artifacts"]["output_mp3"] == [str(output_path.relative_to(tmp_path))]


def test_export_transcript_writes_markdown(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    memory.add("user", "Hello")
    memory.add("assistant", "Hi Florian")

    result = export_transcript(memory.session_file, root_dir=tmp_path)

    output_path = tmp_path / "exports" / f"{memory.session_file.stem}.md"
    text = output_path.read_text(encoding="utf-8")
    assert result["path"] == str(output_path)
    assert result["turns"] == 2
    assert "**Florian:** Hello" in text
    assert "**AI:** Hi Florian" in text


def test_run_episode_is_drivable_with_injected_io(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")
    monkeypatch.setattr(openclaw_tools, "load_settings", lambda: settings)

    inputs = iter(["hello there", "q"])
    outputs: list[str] = []
    result = run_episode("pilot", input_fn=lambda _prompt: next(inputs), output_fn=outputs.append)

    assert result["episode"] == "pilot"
    assert result["turns"] == 2  # user + assistant
    assert any("Episode ended" in line for line in outputs)


def test_load_session_rejects_out_of_tree_absolute_path(tmp_path):
    (tmp_path / "sessions").mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"episode": "x", "history": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        load_session(outside, root_dir=tmp_path)


def test_load_session_rejects_directory_traversal(tmp_path):
    (tmp_path / "sessions").mkdir()

    with pytest.raises(ValueError, match="escapes"):
        load_session("../secret.txt", root_dir=tmp_path)


def test_export_transcript_rejects_out_of_tree_path(tmp_path):
    (tmp_path / "sessions").mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"episode": "x", "history": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        export_transcript(outside, root_dir=tmp_path)


def test_write_episode_context_rejects_empty_content(tmp_path):
    with pytest.raises(ValueError, match="must not be empty"):
        write_episode_context("pilot", "   ", root_dir=tmp_path)


def test_export_transcript_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="markdown"):
        export_transcript("whatever.json", format="srt", root_dir=tmp_path)
