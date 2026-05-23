from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings
from main import main, run_episode


def test_run_episode_dry_run_saves_session_and_voice_artifact(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")

    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")
    inputs = iter(["Hello from the host", "q"])
    outputs: list[str] = []

    memory = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    session_files = list((tmp_path / "sessions").glob("pilot_*.json"))
    voice_files = list(memory.audio_output_dir.glob("turn_000000.txt"))

    assert memory.get()[0]["content"] == "Hello from the host"
    assert len(session_files) == 1
    assert len(voice_files) == 1
    assert "Hello from the host" in voice_files[0].read_text(encoding="utf-8")

    payload = json.loads(session_files[0].read_text(encoding="utf-8"))
    assert [turn["role"] for turn in payload["history"]] == ["user", "assistant"]
    assert any("Episode ended" in line for line in outputs)


def test_run_episode_mic_mode_uses_recording_and_transcription(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")

    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
        confirm_transcript=False,
    )
    outputs: list[str] = []

    def fake_record(settings, turn_index, input_fn, output_fn, output_dir=None):
        assert turn_index == 0
        path = output_dir / "turn_000000.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake wav")
        return str(path)

    monkeypatch.setattr("main.record_until_keypress", fake_record)
    monkeypatch.setattr("main.transcribe", lambda audio_path, settings: "mic transcript")

    memory = run_episode("pilot", settings=settings, output_fn=outputs.append, max_turns=1)

    assert memory.get()[0]["content"] == "mic transcript"
    assert any("Host audio saved:" in line for line in outputs)
    assert any("Transcript: mic transcript" in line for line in outputs)


def test_run_episode_mic_mode_accepts_confirmed_transcript(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
        confirm_transcript=True,
    )

    def fake_record(settings, turn_index, input_fn, output_fn, output_dir=None):
        path = output_dir / "turn_000000.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake wav")
        return str(path)

    monkeypatch.setattr("main.record_until_keypress", fake_record)
    monkeypatch.setattr("main.transcribe", lambda audio_path, settings: "accepted transcript")

    memory = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: "",
        output_fn=lambda _line: None,
        max_turns=1,
    )

    assert memory.get()[0]["content"] == "accepted transcript"
    assert any(event["stage"] == "transcript_confirmed" for event in memory.events)


def test_run_episode_mic_mode_can_edit_skip_and_quit(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
        confirm_transcript=True,
    )

    def fake_record(settings, turn_index, input_fn, output_fn, output_dir=None):
        path = output_dir / f"turn_{turn_index:06d}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake wav")
        return str(path)

    monkeypatch.setattr("main.record_until_keypress", fake_record)
    monkeypatch.setattr("main.transcribe", lambda audio_path, settings: "raw transcript")

    edit_inputs = iter(["e", "edited transcript"])
    edited = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(edit_inputs),
        output_fn=lambda _line: None,
        max_turns=1,
    )
    assert edited.get()[0]["content"] == "edited transcript"

    skip_inputs = iter(["s", "q"])
    skipped = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(skip_inputs),
        output_fn=lambda _line: None,
        max_turns=1,
    )
    assert skipped.get() == []

    quit_inputs = iter(["q"])
    ended = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(quit_inputs),
        output_fn=lambda _line: None,
    )
    assert ended.get() == []


def test_run_episode_mic_mode_can_re_record(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
        confirm_transcript=True,
    )
    calls = {"count": 0}

    def fake_record(settings, turn_index, input_fn, output_fn, output_dir=None):
        calls["count"] += 1
        path = output_dir / f"turn_{turn_index:06d}_{calls['count']}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake wav")
        return str(path)

    monkeypatch.setattr("main.record_until_keypress", fake_record)
    monkeypatch.setattr("main.transcribe", lambda audio_path, settings: f"transcript {calls['count']}")
    inputs = iter(["r", ""])

    memory = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
        max_turns=1,
    )

    assert calls["count"] == 2
    assert memory.get()[0]["content"] == "transcript 2"


def test_run_episode_can_resume_existing_session(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")

    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")
    first_inputs = iter(["First turn", "q"])
    first = run_episode("pilot", settings=settings, input_fn=lambda _prompt: next(first_inputs), output_fn=lambda _line: None)

    second_inputs = iter(["Second turn"])
    resumed = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(second_inputs),
        output_fn=lambda _line: None,
        resume=True,
        max_turns=1,
    )

    assert resumed.session_file == first.session_file
    assert [turn["content"] for turn in resumed.get() if turn["role"] == "user"] == ["First turn", "Second turn"]


def test_run_episode_records_error_event_on_llm_failure(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")
    inputs = iter(["turn before failure"])

    def fail_llm(history, system_prompt, settings):
        raise RuntimeError("model down")

    monkeypatch.setattr("main.call_llm", fail_llm)

    memory = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(inputs),
        output_fn=lambda _line: None,
        max_turns=1,
    )

    assert [turn["role"] for turn in memory.get()] == ["user"]
    assert any(event["stage"] == "llm_completed" and event["status"] == "failed" for event in memory.events)


def test_run_episode_keeps_ai_text_when_tts_fails(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    monkeypatch.setattr("main.call_llm", lambda *_args: "usable ai answer")
    monkeypatch.setattr("main.speak", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("voice down")))

    memory = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: "host turn",
        output_fn=lambda _line: None,
        max_turns=1,
    )

    assert [turn["content"] for turn in memory.get()] == ["host turn", "usable ai answer"]
    assert memory.get()[-1]["metadata"]["status"] == "tts_failed"


def test_run_episode_uses_exact_session_path(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")

    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")
    first_inputs = iter(["Existing turn", "q"])
    first = run_episode("pilot", settings=settings, input_fn=lambda _prompt: next(first_inputs), output_fn=lambda _line: None)

    second_inputs = iter(["Exact session turn"])
    resumed = run_episode(
        "pilot",
        settings=settings,
        input_fn=lambda _prompt: next(second_inputs),
        output_fn=lambda _line: None,
        session_path=first.session_file,
        max_turns=1,
    )

    assert resumed.session_file == first.session_file
    assert resumed.get()[-2]["content"] == "Exact session turn"


def test_main_passes_resume_and_session_flags(monkeypatch):
    calls = []

    def fake_run_episode(episode_name, settings=None, resume=False, session_path=None, max_turns=None, confirm_transcript=None):
        calls.append({"episode": episode_name, "resume": resume, "session_path": session_path})

    monkeypatch.setattr("main.run_episode", fake_run_episode)

    assert main(["pilot", "--resume"]) == 0
    assert main(["pilot", "--session", "sessions/custom.json"]) == 0
    assert calls == [
        {"episode": "pilot", "resume": True, "session_path": None},
        {"episode": "pilot", "resume": False, "session_path": "sessions/custom.json"},
    ]


def test_main_doctor_runs_preflight(monkeypatch, capsys):
    monkeypatch.setattr("main.run_preflight", lambda settings: {"ok": True, "checks": []})
    monkeypatch.setattr("main.format_preflight_report", lambda result: "doctor ok")

    assert main(["pilot", "--doctor"]) == 0

    captured = capsys.readouterr()
    assert "doctor ok" in captured.out


def test_main_dispatches_realtime_mode(monkeypatch):
    calls = []
    settings = Settings(
        root_dir=Path("."),
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="realtime",
        input_mode="mic",
        openai_api_key="test-key",
    )

    async def fake_run_realtime_episode(episode_name, passed_settings, resume=False, session_path=None):
        calls.append((episode_name, passed_settings, resume, session_path))

    monkeypatch.setattr("main.load_settings", lambda validate=True: settings)
    monkeypatch.setattr("main.run_realtime_episode", fake_run_realtime_episode)

    assert main(["pilot"]) == 0
    assert calls == [("pilot", settings, False, None)]


def test_main_list_devices_prints_devices(monkeypatch, capsys):
    monkeypatch.setattr(
        "main.list_input_devices",
        lambda: [{"index": 3, "name": "Test Mic", "max_input_channels": 1, "default_samplerate": 48000}],
    )

    assert main(["--list-devices"]) == 0

    captured = capsys.readouterr()
    assert "3: Test Mic" in captured.out


def test_main_invalid_session_path_returns_error(tmp_path, monkeypatch, capsys):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = main(["pilot", "--session", "missing.json"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Session file does not exist" in captured.err
