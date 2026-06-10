from __future__ import annotations

import asyncio
import base64
import json
import sys
import types
from pathlib import Path

import pytest

from config.settings import Settings
from pipeline.memory import ConversationMemory
from pipeline.realtime import (
    ConnectionClosed,
    RealtimeEventProcessor,
    _receive_events,
    build_session_update,
    run_realtime_episode,
)


class _NullOutput:
    def abort(self):
        return None

    def start(self):
        return None

    def write(self, _audio):
        return None


class _NullWriter:
    def buffer_write(self, _audio, dtype):
        return dtype


class _NullWebsocket:
    async def send(self, _payload):
        return None


def test_build_session_update_configures_native_audio_and_vad(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="realtime",
        input_mode="mic",
        openai_api_key="test-key",
    )

    event = build_session_update(settings, "Speak naturally.")

    session = event["session"]
    assert session["model"] == "gpt-realtime"
    assert session["audio"]["input"]["transcription"]["model"] == "gpt-4o-transcribe"
    assert session["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert session["audio"]["output"]["voice"] == "marin"


def test_realtime_events_persist_transcripts_queue_audio_and_cancel_on_barge_in(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    flushed = []
    written = []
    sent = []

    class FakeOutput:
        def abort(self):
            flushed.append("abort")

        def start(self):
            flushed.append("start")

    class FakeWriter:
        def buffer_write(self, audio, dtype):
            written.append((audio, dtype))

    class FakeWebsocket:
        async def send(self, payload):
            sent.append(json.loads(payload))

    async def exercise():
        playback_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=16)
        processor = RealtimeEventProcessor(memory, FakeOutput(), playback_queue, FakeWriter(), lambda _line: None)
        ws = FakeWebsocket()
        await processor.handle({"type": "conversation.item.created", "item": {"id": "u1", "role": "user"}}, ws)
        await processor.handle(
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "hello"},
            ws,
        )
        await processor.handle(
            {"type": "conversation.item.created", "previous_item_id": "u1", "item": {"id": "a1", "role": "assistant"}},
            ws,
        )
        await processor.handle({"type": "response.created"}, ws)
        await processor.handle(
            {"type": "response.output_audio.delta", "delta": base64.b64encode(b"pcm").decode("ascii")},
            ws,
        )
        # Audio is enqueued for the background playback consumer, not written inline.
        assert playback_queue.get_nowait() == b"pcm"
        await processor.handle(
            {"type": "response.output_audio_transcript.done", "item_id": "a1", "response_id": "r1", "transcript": "hi Florian"},
            ws,
        )
        await processor.handle({"type": "input_audio_buffer.speech_started", "item_id": "u2"}, ws)

    asyncio.run(exercise())

    assert [turn["content"] for turn in memory.get()] == ["hello", "hi Florian"]
    assert memory.get()[0]["metadata"]["turn_id"] == memory.get()[1]["metadata"]["turn_id"]
    assert written == [(b"pcm", "int16")]
    assert flushed == ["abort", "start"]  # barge-in flushed the output stream
    assert {"type": "response.cancel"} in sent


def test_realtime_transcripts_are_ordered_by_items_not_completion_timing(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    processor = RealtimeEventProcessor(memory, _NullOutput(), asyncio.Queue(), _NullWriter(), lambda _line: None)

    async def exercise():
        ws = _NullWebsocket()
        await processor.handle({"type": "conversation.item.created", "item": {"id": "u1", "role": "user"}}, ws)
        await processor.handle(
            {"type": "conversation.item.created", "previous_item_id": "u1", "item": {"id": "a1", "role": "assistant"}},
            ws,
        )
        await processor.handle(
            {"type": "response.output_audio_transcript.done", "item_id": "a1", "transcript": "reply"},
            ws,
        )
        await processor.handle(
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "question"},
            ws,
        )

    asyncio.run(exercise())

    assert [turn["content"] for turn in memory.get()] == ["question", "reply"]


def test_realtime_error_event_raises_runtime_error(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    processor = RealtimeEventProcessor(memory, _NullOutput(), asyncio.Queue(), _NullWriter(), lambda _line: None)

    async def exercise():
        await processor.handle({"type": "error", "error": {"message": "boom"}}, _NullWebsocket())

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(exercise())
    assert any(event["stage"] == "realtime" and event["status"] == "failed" for event in memory.events)


def test_receive_events_handles_connection_closed_cleanly(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    logged: list[str] = []
    processor = RealtimeEventProcessor(memory, _NullOutput(), asyncio.Queue(), _NullWriter(), logged.append)

    class DroppingWebsocket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionClosed(None, None)

    # A mid-session drop must be swallowed (clean stop), not propagated.
    asyncio.run(_receive_events(DroppingWebsocket(), processor))

    assert any("connection closed" in line.lower() for line in logged)
    assert any(event["stage"] == "realtime" and event["status"] == "disconnected" for event in memory.events)


def test_run_realtime_episode_connects_with_model_param_and_publishes_wavs(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")

    class FakeStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

        def abort(self):
            return None

        def write(self, _audio):
            return None

    fake_sd = types.SimpleNamespace(
        RawInputStream=lambda **kw: FakeStream(**kw),
        RawOutputStream=lambda **kw: FakeStream(**kw),
    )

    class FakeSoundFile:
        def __init__(self, path, mode="r", **kwargs):
            self.path = Path(path)

        def __enter__(self):
            self.path.write_bytes(b"RIFFfake-wav-bytes")  # non-empty so _publish_wav renames it
            return self

        def __exit__(self, *_exc):
            return False

        def buffer_write(self, _audio, _dtype):
            return None

    fake_sf = types.SimpleNamespace(SoundFile=FakeSoundFile)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    captured: dict[str, object] = {}

    class FakeWebsocket:
        def __init__(self):
            self.sent: list[dict] = []
            self._events = [
                json.dumps({"type": "conversation.item.created", "item": {"id": "u1", "role": "user"}}),
                json.dumps(
                    {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "hello there"}
                ),
            ]

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._events:
                return self._events.pop(0)
            await asyncio.sleep(0.02)  # stay "open" so the stopper ends the session, not the receiver
            raise StopAsyncIteration

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    fake_ws = FakeWebsocket()

    def fake_connect(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return fake_ws

    settings = Settings(
        root_dir=tmp_path,
        active_llm="dry-run",
        active_model="dry-run-v1",
        conversation_mode="realtime",
        input_mode="mic",
        openai_api_key="test-key",
    )

    memory = asyncio.run(
        run_realtime_episode(
            "pilot",
            settings,
            input_fn=lambda _prompt: "",  # stopper returns immediately -> ends the session
            output_fn=lambda _line: None,
            websocket_connect=fake_connect,
        )
    )

    assert "?model=gpt-realtime" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert any(message.get("type") == "session.update" for message in fake_ws.sent)
    # In VAD mode the client must NOT send a manual commit on stop.
    assert all(message.get("type") != "input_audio_buffer.commit" for message in fake_ws.sent)
    assert (memory.audio_input_dir / "live_host.wav").exists()
    assert (memory.audio_output_dir / "live_ai.wav").exists()
