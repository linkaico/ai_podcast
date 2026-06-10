from __future__ import annotations

import types

from config.settings import Settings
from pipeline.preflight import format_preflight_report, run_preflight


def test_preflight_passes_in_default_dry_run(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    result = run_preflight(settings)

    assert result["ok"] is True
    assert "Result: OK" in format_preflight_report(result)


def test_preflight_reports_missing_base_prompt(tmp_path):
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any(check["name"] == "base_prompt" and check["status"] == "error" for check in result["checks"])


def test_preflight_reports_missing_deepgram_key_for_mic(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="chained",
        input_mode="mic",
    )

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any("DEEPGRAM_API_KEY" in check["message"] for check in result["checks"])


def test_preflight_reports_missing_xai_key_for_xai_stt(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="chained",
        input_mode="mic",
        stt_mode="xai",
    )

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any("XAI_API_KEY" in check["message"] for check in result["checks"])


def test_preflight_xai_stt_does_not_require_deepgram_sdk(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="chained",
        input_mode="mic",
        stt_mode="xai",
        xai_api_key="test-key",
        audio_device_index="default",
    )

    monkeypatch.setattr("pipeline.preflight.list_input_devices", lambda: [])
    monkeypatch.setattr(
        "pipeline.preflight.importlib.util.find_spec",
        lambda module_name: None if module_name == "deepgram" else object(),
    )

    result = run_preflight(settings)

    assert result["ok"] is True
    assert not any(check["name"] == "sdk:deepgram-sdk" for check in result["checks"])
    assert any(check["name"] == "sdk:requests" for check in result["checks"])


def test_preflight_reports_missing_elevenlabs_config(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="chained",
        tts_mode="elevenlabs",
    )

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any("ELEVENLABS_API_KEY" in check["message"] for check in result["checks"])


def test_preflight_reports_missing_elevenlabs_voice_id(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="chained",
        tts_mode="elevenlabs",
        elevenlabs_api_key="test-key",
    )

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any("ELEVENLABS_VOICE_ID" in check["message"] for check in result["checks"])


def test_preflight_reports_missing_xai_key_for_xai_tts(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="chained",
        tts_mode="xai",
    )

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any("XAI_API_KEY" in check["message"] for check in result["checks"])


def test_preflight_reports_unwritable_runtime_path(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    (tmp_path / "sessions").write_text("not a directory", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    result = run_preflight(settings)

    assert result["ok"] is False
    assert any(check["name"] == "sessions" and check["status"] == "error" for check in result["checks"])


def test_preflight_realtime_requires_websocket_not_deepgram(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="realtime",
        openai_api_key="test-key",
        input_mode="mic",
        audio_device_index="default",
    )
    monkeypatch.setattr("pipeline.preflight.list_input_devices", lambda: [])
    monkeypatch.setattr("pipeline.preflight.importlib.util.find_spec", lambda _module_name: object())

    result = run_preflight(settings)

    assert result["ok"] is True
    assert any(check["name"] == "sdk:websockets" for check in result["checks"])
    assert not any(check["name"] == "sdk:deepgram-sdk" for check in result["checks"])


def test_preflight_checks_real_audio_root_and_disk_space(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    result = run_preflight(settings)
    names = {check["name"] for check in result["checks"]}

    assert "audio" in names  # the real recording parent, not the legacy flat dirs
    assert "audio_input" not in names and "audio_output" not in names
    assert "disk_space" in names
    assert result["ok"] is True


def test_preflight_warns_on_low_disk_space(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    monkeypatch.setattr(
        "pipeline.preflight.shutil.disk_usage",
        lambda _path: types.SimpleNamespace(total=10 * 1024 * 1024, used=9 * 1024 * 1024, free=1 * 1024 * 1024),
    )

    result = run_preflight(settings)
    disk = next(check for check in result["checks"] if check["name"] == "disk_space")

    assert disk["status"] == "warn"
    assert result["ok"] is True  # warn is non-fatal


def test_preflight_warns_on_missing_output_device(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        playback_mode="sdk",
        output_audio_device="CABLE Out",
    )

    monkeypatch.setattr("pipeline.preflight.list_output_devices", lambda: [{"index": 0, "name": "Speakers"}])

    result = run_preflight(settings)
    output_device = next(check for check in result["checks"] if check["name"] == "output_device")

    assert output_device["status"] == "warn"
    assert result["ok"] is True  # warn is non-fatal
