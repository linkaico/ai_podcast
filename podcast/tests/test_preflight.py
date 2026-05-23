from __future__ import annotations

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
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1", input_mode="mic")

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
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1", tts_mode="elevenlabs")

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
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1", tts_mode="xai")

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
