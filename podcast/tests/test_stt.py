from __future__ import annotations

import pytest

from config.settings import Settings
from pipeline.stt import capture_text_turn, transcribe, transcribe_with_client


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
