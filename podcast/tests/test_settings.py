from __future__ import annotations

import pytest

from config.settings import SettingsError, load_settings


API_ENV = [
    "ACTIVE_LLM",
    "ACTIVE_MODEL",
    "CONVERSATION_MODE",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "XAI_API_KEY",
    "INPUT_MODE",
    "STT_MODE",
    "TTS_MODE",
    "DEEPGRAM_MODEL",
    "ELEVENLABS_MODEL",
    "XAI_STT_LANGUAGE",
    "XAI_TTS_VOICE",
    "XAI_TTS_LANGUAGE",
    "AUDIO_SAMPLE_RATE",
    "AUDIO_CHANNELS",
    "CONFIRM_TRANSCRIPT",
    "PROVIDER_TIMEOUT_SECONDS",
    "PROVIDER_MAX_RETRIES",
    "OPENAI_API_MODE",
    "PLAYBACK_MODE",
    "ELEVENLABS_OUTPUT_FORMAT",
    "ELEVENLABS_STABILITY",
    "ELEVENLABS_SIMILARITY_BOOST",
    "ELEVENLABS_STYLE",
    "ELEVENLABS_SPEED",
    "REALTIME_MODEL",
    "REALTIME_VOICE",
    "REALTIME_TRANSCRIPTION_MODEL",
    "REALTIME_VAD_MODE",
    "REALTIME_SAMPLE_RATE",
]


def test_load_settings_defaults_to_dry_run(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)

    settings = load_settings(tmp_path)

    assert settings.root_dir == tmp_path.resolve()
    assert settings.active_llm == "dry-run"
    assert settings.active_model == "dry-run-v1"
    assert settings.conversation_mode == "dry-run"
    assert settings.is_dry_run is True
    assert settings.input_mode == "text"
    assert settings.stt_mode == "deepgram"
    assert settings.tts_mode == "dry-run"
    assert settings.deepgram_model == "nova-3"
    assert settings.elevenlabs_model == "eleven_flash_v2_5"
    assert settings.xai_stt_language == "en"
    assert settings.xai_tts_voice == "eve"
    assert settings.xai_tts_language == "en"
    assert settings.audio_sample_rate == 16000
    assert settings.audio_channels == 1
    assert settings.confirm_transcript is True
    assert settings.provider_timeout_seconds == 60
    assert settings.provider_max_retries == 1
    assert settings.openai_api_mode == "responses"
    assert settings.playback_mode == "file-only"
    assert settings.elevenlabs_output_format == "mp3_22050_32"
    assert settings.realtime_model == "gpt-realtime"
    assert settings.realtime_voice == "marin"


def test_real_provider_requires_matching_api_key(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ACTIVE_LLM", "anthropic")
    monkeypatch.setenv("CONVERSATION_MODE", "chained")

    with pytest.raises(SettingsError, match="ANTHROPIC_API_KEY"):
        load_settings(tmp_path)


def test_real_provider_accepts_matching_api_key(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ACTIVE_LLM", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CONVERSATION_MODE", "chained")

    settings = load_settings(tmp_path)

    assert settings.active_llm == "openai"
    assert settings.openai_api_key == "test-key"


def test_mic_input_requires_deepgram_key(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("INPUT_MODE", "mic")

    with pytest.raises(SettingsError, match="DEEPGRAM_API_KEY"):
        load_settings(tmp_path)


def test_mic_input_can_use_xai_stt_without_deepgram_key(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("INPUT_MODE", "mic")
    monkeypatch.setenv("STT_MODE", "xai")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    settings = load_settings(tmp_path)

    assert settings.uses_xai_stt is True
    assert settings.deepgram_api_key == ""
    assert settings.xai_api_key == "test-key"


def test_xai_stt_requires_xai_key_for_mic_input(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("INPUT_MODE", "mic")
    monkeypatch.setenv("STT_MODE", "xai")

    with pytest.raises(SettingsError, match="XAI_API_KEY"):
        load_settings(tmp_path)


def test_invalid_stt_mode_is_rejected(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("STT_MODE", "unknown")

    with pytest.raises(SettingsError, match="STT_MODE"):
        load_settings(tmp_path)


def test_elevenlabs_tts_requires_api_key_and_voice_id(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("TTS_MODE", "elevenlabs")

    with pytest.raises(SettingsError, match="ELEVENLABS_API_KEY"):
        load_settings(tmp_path)

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    with pytest.raises(SettingsError, match="ELEVENLABS_VOICE_ID"):
        load_settings(tmp_path)


def test_xai_tts_requires_xai_key(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("TTS_MODE", "xai")

    with pytest.raises(SettingsError, match="XAI_API_KEY"):
        load_settings(tmp_path)


def test_xai_tts_accepts_default_voice_and_language(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("TTS_MODE", "xai")
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    settings = load_settings(tmp_path)

    assert settings.uses_xai_tts is True
    assert settings.xai_tts_voice == "eve"
    assert settings.xai_tts_language == "en"


def test_audio_modes_are_independent_from_llm_provider(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ACTIVE_LLM", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CONVERSATION_MODE", "chained")
    monkeypatch.setenv("INPUT_MODE", "text")
    monkeypatch.setenv("TTS_MODE", "dry-run")

    settings = load_settings(tmp_path)

    assert settings.active_llm == "anthropic"
    assert settings.uses_text_input is True
    assert settings.uses_dry_run_tts is True


def test_load_settings_can_skip_validation_for_doctor(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INPUT_MODE", "mic")

    settings = load_settings(tmp_path, validate=False)

    assert settings.input_mode == "mic"
    assert settings.deepgram_api_key == ""


def test_realtime_mode_requires_openai_key_and_microphone(tmp_path, monkeypatch):
    for name in API_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_MODE", "realtime")

    with pytest.raises(SettingsError, match="OPENAI_API_KEY"):
        load_settings(tmp_path)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(SettingsError, match="INPUT_MODE=mic"):
        load_settings(tmp_path)

    monkeypatch.setenv("INPUT_MODE", "mic")
    settings = load_settings(tmp_path)
    assert settings.uses_realtime is True
