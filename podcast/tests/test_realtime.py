from __future__ import annotations

import asyncio
import base64
import json

from config.settings import Settings
from pipeline.memory import ConversationMemory
from pipeline.realtime import RealtimeEventProcessor, build_session_update


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


def test_realtime_events_persist_transcripts_play_audio_and_cancel_on_barge_in(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)
    played = []
    written = []
    sent = []

    class FakeOutput:
        def write(self, audio):
            played.append(audio)

        def abort(self):
            played.append(b"abort")

        def start(self):
            played.append(b"start")

    class FakeWriter:
        def buffer_write(self, audio, dtype):
            written.append((audio, dtype))

    class FakeWebsocket:
        async def send(self, payload):
            sent.append(json.loads(payload))

    processor = RealtimeEventProcessor(memory, FakeOutput(), FakeWriter(), lambda _line: None)

    async def exercise():
        ws = FakeWebsocket()
        await processor.handle(
            {"type": "conversation.item.created", "item": {"id": "u1", "role": "user"}},
            ws,
        )
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
        await processor.handle(
            {"type": "response.output_audio_transcript.done", "item_id": "a1", "response_id": "r1", "transcript": "hi Florian"},
            ws,
        )
        await processor.handle({"type": "input_audio_buffer.speech_started", "item_id": "u2"}, ws)

    asyncio.run(exercise())

    assert [turn["content"] for turn in memory.get()] == ["hello", "hi Florian"]
    assert memory.get()[0]["metadata"]["turn_id"] == memory.get()[1]["metadata"]["turn_id"]
    assert written == [(b"pcm", "int16")]
    assert b"pcm" in played
    assert {"type": "response.cancel"} in sent


def test_realtime_transcripts_are_ordered_by_items_not_completion_timing(tmp_path):
    memory = ConversationMemory("pilot", sessions_dir=tmp_path / "sessions", root_dir=tmp_path)

    class NullOutput:
        def write(self, _audio):
            return None

    class NullWriter:
        def buffer_write(self, _audio, dtype):
            return dtype

    class NullWebsocket:
        async def send(self, _payload):
            return None

    processor = RealtimeEventProcessor(memory, NullOutput(), NullWriter(), lambda _line: None)

    async def exercise():
        ws = NullWebsocket()
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
