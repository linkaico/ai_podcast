from __future__ import annotations

import pytest

from config.settings import Settings
from pipeline.tts import speak, speak_with_client


def test_dry_run_tts_writes_text_artifact(tmp_path):
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    output_path = speak("hello voice", 3, settings)

    assert output_path == tmp_path / "audio" / "output" / "turn_000003.txt"
    assert output_path.read_text(encoding="utf-8") == "hello voice"


def test_elevenlabs_tts_requires_config(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        tts_mode="elevenlabs",
    )

    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        speak("hello", 0, settings)


def test_xai_tts_requires_config(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        tts_mode="xai",
    )

    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        speak("hello", 0, settings)


def test_elevenlabs_tts_writes_mp3_from_mocked_audio(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        tts_mode="elevenlabs",
        elevenlabs_api_key="test-key",
        elevenlabs_voice_id="voice-123",
        output_audio_device="file-only",
    )

    class FakeTextToSpeech:
        def convert(self, voice_id, text, model_id, output_format=None, voice_settings=None):
            assert voice_id == "voice-123"
            assert text == "hello"
            assert model_id == "eleven_flash_v2_5"
            assert output_format == "mp3_22050_32"
            assert voice_settings is not None
            return [b"mp3", b"-bytes"]

    class FakeClient:
        text_to_speech = FakeTextToSpeech()

    output_path = speak_with_client(
        "hello",
        2,
        settings,
        client_factory=lambda api_key: FakeClient(),
    )

    assert output_path == tmp_path / "audio" / "output" / "turn_000002.mp3"
    assert output_path.read_bytes() == b"mp3-bytes"


def test_xai_tts_writes_mp3_from_mocked_audio(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        tts_mode="xai",
        xai_api_key="test-key",
        xai_tts_voice="ara",
        xai_tts_language="en",
    )
    captured = {}

    class FakeResponse:
        content = b"xai-mp3"

        def raise_for_status(self):
            captured["raised"] = True

    def fake_http_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    output_path = speak_with_client(
        "hello",
        4,
        settings,
        http_post=fake_http_post,
    )

    assert output_path == tmp_path / "audio" / "output" / "turn_000004.mp3"
    assert output_path.read_bytes() == b"xai-mp3"
    assert captured["url"] == "https://api.x.ai/v1/tts"
    assert captured["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
    }
    assert captured["json"] == {
        "text": "hello",
        "voice_id": "ara",
        "language": "en",
    }
    assert captured["timeout"] == 60
    assert captured["raised"] is True


def test_playback_failure_keeps_saved_mp3(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        tts_mode="elevenlabs",
        elevenlabs_api_key="test-key",
        elevenlabs_voice_id="voice-123",
        playback_mode="sdk",
        provider_max_retries=0,
    )
    outputs: list[str] = []

    class FakeTextToSpeech:
        def convert(self, **kwargs):
            return [b"mp3"]

    class FakeClient:
        text_to_speech = FakeTextToSpeech()

    output_path = speak_with_client(
        "hello",
        1,
        settings,
        output_fn=outputs.append,
        client_factory=lambda api_key: FakeClient(),
        stream_fn=lambda _audio: (_ for _ in ()).throw(RuntimeError("speaker unavailable")),
    )

    assert output_path.read_bytes() == b"mp3"
    assert any("playback skipped" in line for line in outputs)


def test_elevenlabs_tts_uses_extension_from_output_format(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        tts_mode="elevenlabs",
        elevenlabs_api_key="test-key",
        elevenlabs_voice_id="voice-123",
        elevenlabs_output_format="pcm_24000",
    )

    class FakeClient:
        class text_to_speech:
            @staticmethod
            def convert(**kwargs):
                return [b"pcm"]

    output_path = speak_with_client("hello", 5, settings, client_factory=lambda _key: FakeClient())
    assert output_path.suffix == ".pcm"


def test_output_device_parsing():
    from pipeline.tts import _output_device

    assert _output_device("") is None
    assert _output_device("default") is None
    assert _output_device("  DEFAULT ") is None
    assert _output_device("7") == 7
    assert _output_device("CABLE Input") == "CABLE Input"


def test_coerce_audio_bytes_handles_generator_and_raw():
    from pipeline.tts import _coerce_audio_bytes

    def chunks():
        yield b"ab"
        yield b""  # empty chunk skipped
        yield b"cd"

    assert _coerce_audio_bytes(chunks()) == b"abcd"
    assert _coerce_audio_bytes(b"raw") == b"raw"
    assert _coerce_audio_bytes(bytearray(b"ba")) == b"ba"


def test_elevenlabs_extension_rejects_unsupported_format():
    from pipeline.tts import _elevenlabs_extension

    assert _elevenlabs_extension("mp3_22050_32") == "mp3"
    with pytest.raises(RuntimeError, match="Unsupported"):
        _elevenlabs_extension("flac_44100")


def test_system_play_windows_uses_startfile(tmp_path, monkeypatch):
    from pipeline import tts

    called: list[str] = []
    monkeypatch.setattr(tts.sys, "platform", "win32")
    monkeypatch.setattr(tts.os, "startfile", lambda path: called.append(path), raising=False)

    tts._system_play(tmp_path / "voice.mp3")

    assert called == [str(tmp_path / "voice.mp3")]


def test_system_play_unsupported_platform_raises(tmp_path, monkeypatch):
    from pipeline import tts

    monkeypatch.setattr(tts.sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="file-only"):
        tts._system_play(tmp_path / "voice.mp3")
