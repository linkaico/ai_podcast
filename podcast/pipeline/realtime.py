from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import json
import os
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable
from uuid import uuid4

from config.settings import Settings
from pipeline.llm import load_system_prompt
from pipeline.memory import ConversationMemory


REALTIME_URL = "wss://api.openai.com/v1/realtime"


def build_session_update(settings: Settings, system_prompt: str) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": settings.realtime_model,
            "instructions": system_prompt,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": settings.realtime_sample_rate},
                    "transcription": {"model": settings.realtime_transcription_model},
                    "turn_detection": {
                        "type": settings.realtime_vad_mode,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": settings.realtime_sample_rate},
                    "voice": settings.realtime_voice,
                },
            },
        },
    }


class RealtimeEventProcessor:
    def __init__(
        self,
        memory: ConversationMemory,
        output_stream: Any,
        ai_writer: Any,
        output_fn: Callable[[str], None],
    ) -> None:
        self.memory = memory
        self.output_stream = output_stream
        self.ai_writer = ai_writer
        self.output_fn = output_fn
        self.turn_by_item_id: dict[str, int] = {}
        self.sequence_by_item_id: dict[str, int] = {}
        self.next_item_sequence = 0
        self.latest_user_turn_id: int | None = None
        self.response_active = False

    async def handle(self, event: dict[str, Any], websocket: Any) -> None:
        event_type = event.get("type", "")
        if event_type == "response.created":
            self.response_active = True
            return
        if event_type == "response.done":
            self.response_active = False
            return
        if event_type == "conversation.item.created":
            item = event.get("item", {})
            item_id = str(item.get("id", "")) if isinstance(item, dict) else ""
            role = item.get("role") if isinstance(item, dict) else None
            if role == "user" and item_id:
                self._item_sequence(item_id)
                turn_id = self._turn_id_for_user_item(item_id)
                self.latest_user_turn_id = turn_id
            elif role == "assistant" and item_id:
                self._item_sequence(item_id)
                previous_item_id = str(event.get("previous_item_id", ""))
                if previous_item_id in self.turn_by_item_id:
                    self.turn_by_item_id[item_id] = self.turn_by_item_id[previous_item_id]
                elif self.latest_user_turn_id is not None:
                    self.turn_by_item_id[item_id] = self.latest_user_turn_id
            return
        if event_type == "response.output_audio.delta":
            audio = base64.b64decode(event.get("delta", ""))
            if audio:
                self.ai_writer.buffer_write(audio, dtype="int16")
                self.output_stream.write(audio)
            return

        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript", "")).strip()
            if transcript:
                item_id = str(event.get("item_id", ""))
                turn_id = self._turn_id_for_user_item(item_id)
                self.latest_user_turn_id = turn_id
                self.memory.add(
                    "user",
                    transcript,
                    metadata={
                        "status": "realtime_transcribed",
                        "turn_id": turn_id,
                        "item_id": item_id,
                        "item_sequence": self._item_sequence(item_id),
                    },
                )
                self.memory.order_realtime_transcripts()
                self.output_fn(f"FLORIAN: {transcript}")
            return

        if event_type == "response.output_audio_transcript.done":
            transcript = str(event.get("transcript", "")).strip()
            if transcript:
                item_id = str(event.get("item_id", ""))
                turn_id = self.turn_by_item_id.get(item_id)
                if turn_id is None:
                    turn_id = self.memory.reserve_turn_id()
                    if item_id:
                        self.turn_by_item_id[item_id] = turn_id
                self.memory.add(
                    "assistant",
                    transcript,
                    metadata={
                        "status": "realtime_spoken",
                        "turn_id": turn_id,
                        "item_id": item_id,
                        "item_sequence": self._item_sequence(item_id),
                        "response_id": event.get("response_id", ""),
                    },
                )
                self.memory.order_realtime_transcripts()
                self.output_fn(f"AI: {transcript}")
            return

        if event_type == "input_audio_buffer.speech_started":
            self._flush_playback()
            self.memory.add_event(
                "realtime_interruption",
                "speech_started",
                details={"item_id": str(event.get("item_id", ""))},
            )
            if self.response_active:
                await websocket.send(json.dumps({"type": "response.cancel"}))
                self.response_active = False
            return

        if event_type == "error":
            error = event.get("error", {})
            message = error.get("message", "Unknown realtime error") if isinstance(error, dict) else str(error)
            self.memory.add_event("realtime", "failed", details={"error": message})
            raise RuntimeError(f"Realtime API error: {message}")

    def _flush_playback(self) -> None:
        with suppress(Exception):
            self.output_stream.abort()
            self.output_stream.start()

    def _turn_id_for_user_item(self, item_id: str) -> int:
        if item_id and item_id in self.turn_by_item_id:
            return self.turn_by_item_id[item_id]
        turn_id = self.memory.reserve_turn_id()
        if item_id:
            self.turn_by_item_id[item_id] = turn_id
        return turn_id

    def _item_sequence(self, item_id: str) -> int:
        if item_id in self.sequence_by_item_id:
            return self.sequence_by_item_id[item_id]
        sequence = self.next_item_sequence
        self.next_item_sequence += 1
        if item_id:
            self.sequence_by_item_id[item_id] = sequence
        return sequence


async def run_realtime_episode(
    episode_name: str,
    settings: Settings,
    *,
    resume: bool = False,
    session_path: str | Path | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    websocket_connect: Callable[[str, dict[str, str]], Any] | None = None,
) -> ConversationMemory:
    settings.validate_runtime()
    memory = _load_memory(episode_name, settings, resume, session_path)
    memory.audio_input_dir.mkdir(parents=True, exist_ok=True)
    memory.audio_output_dir.mkdir(parents=True, exist_ok=True)
    prompt = load_system_prompt(memory.episode_name, settings=settings)
    host_path = memory.audio_input_dir / "live_host.wav"
    ai_path = memory.audio_output_dir / "live_ai.wav"
    host_temp = _temporary_wav_path(host_path)
    ai_temp = _temporary_wav_path(ai_path)

    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("Install sounddevice and soundfile to use CONVERSATION_MODE=realtime.") from exc

    connector = websocket_connect or _default_websocket_connect
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    microphone_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_running_loop()

    def input_callback(indata: Any, _frames: int, _time: Any, status: Any) -> None:
        if status:
            loop.call_soon_threadsafe(output_fn, f"[audio status] {status}")
        payload = bytes(indata)
        loop.call_soon_threadsafe(_enqueue_audio, microphone_queue, payload)

    input_device = _audio_device(settings.audio_device_index)
    output_device = _audio_device(settings.output_audio_device)
    input_stream = sd.RawInputStream(
        samplerate=settings.realtime_sample_rate,
        channels=1,
        dtype="int16",
        device=input_device,
        callback=input_callback,
    )
    output_stream = sd.RawOutputStream(
        samplerate=settings.realtime_sample_rate,
        channels=1,
        dtype="int16",
        device=output_device,
    )

    memory.add_event("realtime", "starting", details={"model": settings.realtime_model})
    output_fn(f"Episode: {memory.episode_name}")
    output_fn(f"Session: {memory.session_file}")
    output_fn("Realtime microphone conversation active. Press ENTER to end the episode.")

    try:
        with sf.SoundFile(host_temp, mode="w", samplerate=settings.realtime_sample_rate, channels=1, subtype="PCM_16") as host_writer:
            with sf.SoundFile(ai_temp, mode="w", samplerate=settings.realtime_sample_rate, channels=1, subtype="PCM_16") as ai_writer:
                async with connector(REALTIME_URL, headers) as websocket:
                    await websocket.send(json.dumps(build_session_update(settings, prompt)))
                    processor = RealtimeEventProcessor(memory, output_stream, ai_writer, output_fn)
                    input_stream.start()
                    output_stream.start()
                    sender = asyncio.create_task(_send_microphone_audio(websocket, microphone_queue, host_writer))
                    receiver = asyncio.create_task(_receive_events(websocket, processor))
                    stopper = asyncio.create_task(_wait_for_stop(input_fn))
                    done, _pending = await asyncio.wait({receiver, stopper}, return_when=asyncio.FIRST_COMPLETED)
                    if receiver in done:
                        await receiver
                    else:
                        await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    sender.cancel()
                    receiver.cancel()
                    stopper.cancel()
                    with suppress(asyncio.CancelledError):
                        await sender
                    with suppress(asyncio.CancelledError):
                        await receiver
                    with suppress(asyncio.CancelledError):
                        await stopper
    finally:
        with suppress(Exception):
            input_stream.stop()
            input_stream.close()
        with suppress(Exception):
            output_stream.stop()
            output_stream.close()
        _publish_wav(host_temp, host_path, memory, "input_wav")
        _publish_wav(ai_temp, ai_path, memory, "output_wav")
        memory.add_event("realtime", "ended")
    return memory


async def _send_microphone_audio(websocket: Any, queue: asyncio.Queue[bytes], writer: Any) -> None:
    while True:
        audio = await queue.get()
        writer.buffer_write(audio, dtype="int16")
        await websocket.send(
            json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(audio).decode("ascii")})
        )


async def _receive_events(websocket: Any, processor: RealtimeEventProcessor) -> None:
    async for raw_event in websocket:
        await processor.handle(json.loads(raw_event), websocket)


async def _wait_for_stop(input_fn: Callable[[str], str]) -> None:
    if input_fn is not input:
        await asyncio.to_thread(input_fn, "")
        return
    loop = asyncio.get_running_loop()
    completed = loop.create_future()

    def accept_enter() -> None:
        sys.stdin.readline()
        if not completed.done():
            completed.set_result(None)

    try:
        loop.add_reader(sys.stdin, accept_enter)
    except (NotImplementedError, AttributeError):
        await asyncio.to_thread(input_fn, "")
        return
    try:
        await completed
    finally:
        loop.remove_reader(sys.stdin)


def _default_websocket_connect(url: str, headers: dict[str, str]) -> Any:
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError("Install websockets to use CONVERSATION_MODE=realtime.") from exc
    return connect(url, additional_headers=headers, open_timeout=20)


def _load_memory(
    episode_name: str,
    settings: Settings,
    resume: bool,
    session_path: str | Path | None,
) -> ConversationMemory:
    if session_path:
        return ConversationMemory.from_session_file(session_path)
    if resume:
        return ConversationMemory.latest_for_episode(episode_name, settings.sessions_dir)
    return ConversationMemory(episode_name, sessions_dir=settings.sessions_dir, root_dir=settings.root_dir)


def _temporary_wav_path(path: Path) -> Path:
    return path.with_name(f".{path.stem}.{uuid4().hex}.part.wav")


def _publish_wav(temporary_path: Path, path: Path, memory: ConversationMemory, kind: str) -> None:
    if temporary_path.exists() and temporary_path.stat().st_size > 0:
        os.replace(temporary_path, path)
        memory.register_artifact(path, kind=kind)


def _audio_device(value: str) -> int | str | None:
    stripped = value.strip()
    if not stripped or stripped.lower() == "default":
        return None
    try:
        return int(stripped)
    except ValueError:
        return stripped


def _enqueue_audio(queue: asyncio.Queue[bytes], payload: bytes) -> None:
    if queue.full():
        return
    queue.put_nowait(payload)
