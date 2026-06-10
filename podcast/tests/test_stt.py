from __future__ import annotations

import sys
import types

import pytest

from config.settings import Settings
from pipeline.stt import (
    _input_device,
    capture_text_turn,
    record_until_keypress,
    transcribe,
    transcribe_with_client,
)


def test_capture_text_turn_strips_input():
    assert capture_text_turn(input_fn=lambda _prompt: "  hello  ") == "hello"


def test_transcribe_requires_deepgram_key_for_audio(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        transcribe(audio_path, settings)


def test_transcribe_parses_mocked_deepgram_response(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
    )

    class FakeVersion:
        def transcribe_file(self, source, options):
            assert source == {"buffer": b"fake wav"}
            assert options["model"] == "nova-3"
            return {
                "results": {
                    "channels": [
                        {"alternatives": [{"transcript": "  mocked transcript  "}]}
                    ]
                }
            }

    class FakeRest:
        def v(self, version):
            assert version == "1"
            return FakeVersion()

    class FakeListen:
        rest = FakeRest()

    class FakeClient:
        listen = FakeListen()

    transcript = transcribe_with_client(
        audio_path,
        settings,
        client_factory=lambda api_key: FakeClient(),
        options_factory=lambda **kwargs: kwargs,
    )

    assert transcript == "mocked transcript"


def test_transcribe_parses_mocked_xai_response(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        stt_mode="xai",
        xai_api_key="test-key",
        xai_stt_language="en",
    )
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            captured["raised"] = True

        def json(self):
            return {"text": "  mocked xAI transcript  "}

    def fake_http_post(url, headers, data, files, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        captured["timeout"] = timeout
        return FakeResponse()

    transcript = transcribe_with_client(audio_path, settings, http_post=fake_http_post)

    assert transcript == "mocked xAI transcript"
    assert captured["url"] == "https://api.x.ai/v1/stt"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert captured["data"] == {"format": "true", "language": "en"}
    assert captured["files"]["file"] == ("host.wav", b"fake wav", "audio/wav")
    assert captured["timeout"] == 60
    assert captured["raised"] is True


def test_transcribe_rejects_empty_deepgram_transcript(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
    )

    class FakeVersion:
        def transcribe_file(self, source, options):
            return {"results": {"channels": [{"alternatives": [{"transcript": "   "}]}]}}

    class FakeRest:
        def v(self, version):
            return FakeVersion()

    class FakeListen:
        rest = FakeRest()

    class FakeClient:
        listen = FakeListen()

    with pytest.raises(RuntimeError, match="empty transcript"):
        transcribe_with_client(
            audio_path,
            settings,
            client_factory=lambda api_key: FakeClient(),
            options_factory=lambda **kwargs: kwargs,
        )


def test_transcribe_supports_current_deepgram_media_contract(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
    )
    captured = {}

    class FakeMedia:
        def transcribe_file(self, **kwargs):
            captured.update(kwargs)
            return {"results": {"channels": [{"alternatives": [{"transcript": "current sdk"}]}]}}

    class FakeClient:
        class listen:
            class v1:
                media = FakeMedia()

    transcript = transcribe_with_client(audio_path, settings, client_factory=lambda _key: FakeClient())

    assert transcript == "current sdk"
    assert captured["request"] == b"fake wav"
    assert captured["model"] == "nova-3"
    assert captured["multichannel"] is False


def test_input_device_parsing():
    assert _input_device("") is None
    assert _input_device("default") is None
    assert _input_device("  DEFAULT ") is None
    assert _input_device("5") == 5
    assert _input_device("Mic Name") == "Mic Name"


def test_deepgram_media_passes_multichannel_for_stereo(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
        audio_channels=2,
    )
    captured = {}

    class FakeMedia:
        def transcribe_file(self, **kwargs):
            captured.update(kwargs)
            return {"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}}

    class FakeClient:
        class listen:
            class v1:
                media = FakeMedia()

    transcribe_with_client(audio_path, settings, client_factory=lambda _key: FakeClient())

    assert captured["multichannel"] is True


def test_deepgram_rest_fallback_without_options_raises(tmp_path):
    audio_path = tmp_path / "host.wav"
    audio_path.write_bytes(b"fake wav")
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        deepgram_api_key="test-key",
    )

    class FakeVersion:
        def transcribe_file(self, source, options):
            return {"results": {"channels": [{"alternatives": [{"transcript": "x"}]}]}}

    class FakeRest:
        def v(self, _version):
            return FakeVersion()

    class FakeClient:
        class listen:
            rest = FakeRest()

    # client_factory given but no options_factory -> options is None -> the REST fallback must refuse.
    with pytest.raises(RuntimeError, match="requires options"):
        transcribe_with_client(audio_path, settings, client_factory=lambda _key: FakeClient())


def test_record_until_keypress_writes_int16_wav(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf

    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        audio_sample_rate=8000,
        audio_channels=1,
    )
    block = np.arange(160, dtype="int16").reshape(-1, 1)

    class FakeInputStream:
        def __init__(self, **kwargs):
            self._callback = kwargs["callback"]

        def __enter__(self):
            self._callback(block, 160, None, None)
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=lambda **kw: FakeInputStream(**kw)))

    out = record_until_keypress(settings, turn_index=0, input_fn=lambda _prompt: "", output_dir=tmp_path)
    data, sample_rate = sf.read(out, dtype="int16")
    assert sample_rate == 8000
    assert len(data) == 160


def test_record_until_keypress_caps_duration(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf

    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        input_mode="mic",
        audio_sample_rate=80,
        audio_channels=1,
        audio_max_record_seconds=1,  # cap = 80 frames
    )
    block = np.ones((80, 1), dtype="int16")
    outputs: list[str] = []

    class FakeInputStream:
        def __init__(self, **kwargs):
            self._callback = kwargs["callback"]

        def __enter__(self):
            self._callback(block, 80, None, None)  # accepted -> 80 frames
            self._callback(block, 80, None, None)  # at cap -> dropped
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=lambda **kw: FakeInputStream(**kw)))

    out = record_until_keypress(settings, 0, input_fn=lambda _prompt: "", output_fn=outputs.append, output_dir=tmp_path)
    data, _sr = sf.read(out, dtype="int16")
    assert len(data) == 80
    assert any("capped" in line for line in outputs)
