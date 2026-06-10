# 06 — OpenAI Realtime Speech-to-Speech Path

> ✅/⏸ **Status (Batch B):** **resolved** — RT-01 (`?model=` connect URL, the P0), RT-02 (non-blocking background playback consumer), RT-03 (no manual VAD commit), RT-06 (protocol/connect tests), EXE-02 (clean WebSocket/auth errors), INF-12 (reject `--max-turns` in realtime). **⏸ Deferred (pragmatic-scope decision, not bugs):** RT-04 full auto-reconnect (clean-stop on drop landed), RT-05 true mic back-pressure (drop-logging landed), RT-07 cancellable Windows stop thread (documented limitation landed). Per-ticket status: [`../../tickets/README.md`](../../tickets/README.md).

Audit context for the **primary live recording path**: full-duplex, speech-to-speech
conversation between the host (Florian) and an OpenAI Realtime model over a single
WebSocket. This is the most complex and error-prone subsystem in the pipeline.

**Primary source:** [`pipeline/realtime.py`](../../../pipeline/realtime.py)
**Tests:** [`tests/test_realtime.py`](../../../tests/test_realtime.py)
**Settings:** [`config/settings.py`](../../../config/settings.py)
**Entry point:** [`main.py`](../../../main.py) (lines ~215-223)
**Dependency:** `websockets>=14.0,<16.0` in [`requirements.txt`](../../../requirements.txt)

---

## Purpose

When `CONVERSATION_MODE=realtime`, [`main.py`](../../../main.py) calls
`asyncio.run(run_realtime_episode(...))` instead of the chained STT→LLM→TTS loop.
A single OpenAI Realtime WebSocket session carries microphone PCM up to the model
and streams synthesized AI audio back down, with server-side VAD detecting turns
and barge-in. Transcripts of both sides are persisted to `ConversationMemory`, and
the raw audio of each side is written to WAV stems.

The model used is `gpt-realtime` (GA, Aug 2025) with voice `marin` and input
transcription via `gpt-4o-transcribe`. VAD defaults to `semantic_vad`. All audio is
16-bit mono PCM at 24 000 Hz.

---

## WebSocket event flow

```
                      ┌────────────────────────────────────────────────────┐
  microphone (PortAudio thread)                                             │
        │ RawInputStream callback (int16 mono @24k)                         │
        │ loop.call_soon_threadsafe -> microphone_queue (maxsize 256)       │
        ▼                                                                    │
  _send_microphone_audio task ──► host_writer.buffer_write (live_host.wav)   │
        │                                                                    │
        │ {"type":"input_audio_buffer.append","audio": <base64 pcm>} ───────┼──► OpenAI
        │                                                                    │     Realtime
        ▼                                                                    │     session
  (server VAD auto-detects end of speech, auto-commits, auto-creates resp)   │
                                                                             │
  _receive_events task  ◄──────────────── server events ◄───────────────────┘
        │
        ├─ response.created                    -> response_active = True
        ├─ response.done                       -> response_active = False
        ├─ conversation.item.created           -> assign turn_id / item_sequence
        ├─ response.output_audio.delta         -> b64 decode -> ai_writer.buffer_write
        │                                          + output_stream.write()  (SPEAKER)
        ├─ conversation.item.input_audio_transcription.completed
        │                                       -> memory.add("user", transcript)
        ├─ response.output_audio_transcript.done
        │                                       -> memory.add("assistant", transcript)
        ├─ input_audio_buffer.speech_started   -> BARGE-IN:
        │                                          _flush_playback() (abort+restart speaker)
        │                                          + send {"type":"response.cancel"}
        └─ error                                -> memory.add_event + raise RuntimeError
```

Stop: a `_wait_for_stop` task waits for ENTER on stdin. When it completes, the main
coroutine sends `input_audio_buffer.commit` and cancels the sender/receiver/stopper.

### Barge-in / interruption

When the server emits `input_audio_buffer.speech_started` (host started talking over
the AI), the processor immediately:
1. `_flush_playback()` — `output_stream.abort()` then `output_stream.start()` to drop
   any queued AI audio already handed to PortAudio.
2. If a response is active, sends `{"type":"response.cancel"}` to stop server-side
   generation, and sets `response_active = False`.

VAD `turn_detection` is configured with `interrupt_response: True` and
`create_response: True`, so the server itself also interrupts/creates responses;
the client `response.cancel` is belt-and-suspenders.

---

## Session config sent (`build_session_update`)

[`pipeline/realtime.py` lines 21-45](../../../pipeline/realtime.py). Shape (GA):

```jsonc
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "model": "gpt-realtime",
    "instructions": "<system prompt>",
    "output_modalities": ["audio"],
    "audio": {
      "input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"model": "gpt-4o-transcribe"},
        "turn_detection": {
          "type": "semantic_vad",
          "create_response": true,
          "interrupt_response": true
        }
      },
      "output": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "voice": "marin"
      }
    }
  }
}
```

This matches the **GA** event shape (the `type: "realtime"` discriminator,
`output_modalities`, nested `audio.input`/`audio.output`, `audio/pcm` format objects).
It is NOT the deprecated preview shape (which used top-level `modalities`,
`input_audio_format` strings, and `response.audio.delta` events). The `OpenAI-Beta`
header is correctly omitted (required for GA).

---

## Audio format / sample-rate handling

| Aspect            | Value                                              |
|-------------------|----------------------------------------------------|
| Codec             | 16-bit signed PCM, little-endian (`int16`)         |
| Channels          | mono (1)                                           |
| Sample rate       | 24 000 Hz, both directions                         |
| Mic capture       | `sd.RawInputStream(samplerate=24000, dtype=int16)` |
| Speaker playback  | `sd.RawOutputStream(samplerate=24000, dtype=int16)`|
| Wire encoding     | base64 of raw PCM bytes                            |

Mic is captured **directly at 24 kHz**, so there is no resampling — the realtime
rate and the capture rate are forced equal (and `validate_audio_modes` hard-requires
`realtime_sample_rate == 24000`). Note the chained path's `audio_sample_rate=16000`
is a *separate* setting and does not apply here.

Outbound: each mic callback buffer is appended raw to the WAV stem and base64-encoded
into `input_audio_buffer.append`. Inbound: each `response.output_audio.delta` is base64
decoded, written to the AI WAV stem, and pushed to the speaker via blocking
`output_stream.write()`.

---

## asyncio task structure

`run_realtime_episode` (async) sets up, then under a single `async with connector(...)`
spawns three tasks:

| Task                         | Function                  | Role                                                |
|------------------------------|---------------------------|-----------------------------------------------------|
| `sender`                     | `_send_microphone_audio`  | drains mic queue → WAV + `input_audio_buffer.append`|
| `receiver`                   | `_receive_events`         | `async for` over socket → `RealtimeEventProcessor`  |
| `stopper`                    | `_wait_for_stop`          | resolves when user presses ENTER                    |

`await asyncio.wait({receiver, stopper}, FIRST_COMPLETED)` blocks. On wake:
if the receiver finished (socket closed / error), it is awaited (re-raising any error);
otherwise the user stopped, and `input_audio_buffer.commit` is sent. All three tasks
are then cancelled and awaited under `suppress(CancelledError)`.

The PortAudio mic callback runs on a **non-asyncio thread** and marshals data back via
`loop.call_soon_threadsafe`. The output stream is written **synchronously from the
receiver task** (on the event-loop thread).

`_wait_for_stop` has two strategies: `loop.add_reader(sys.stdin, ...)` (POSIX), falling
back to `asyncio.to_thread(input_fn, "")` when `add_reader` raises
`NotImplementedError` — which is exactly what happens on **Windows** under the default
ProactorEventLoop. So on Windows the stop path is always the `to_thread(input)` fallback.

---

## Output stems

| Stem                                   | Written by                          | Published to                          |
|----------------------------------------|-------------------------------------|---------------------------------------|
| `audio/input/live_host.wav`            | `host_writer` (mic PCM)             | via `_publish_wav` on `finally`       |
| `audio/output/live_ai.wav`             | `ai_writer` (AI PCM deltas)         | via `_publish_wav` on `finally`       |

Both are written to a hidden temp file (`.live_host.<uuid>.part.wav`) while recording,
then atomically `os.replace`d to the final name on exit and registered as artifacts
(`kind="input_wav"` / `"output_wav"`). A temp file with size 0 is not published.

---

## Config knobs (settings)

[`config/settings.py`](../../../config/settings.py):

| Setting (env)                       | Default            | Notes                                       |
|-------------------------------------|--------------------|---------------------------------------------|
| `REALTIME_MODEL`                    | `gpt-realtime`     | GA model id                                 |
| `REALTIME_VOICE`                    | `marin`            | realtime-exclusive voice (also `cedar`)     |
| `REALTIME_TRANSCRIPTION_MODEL`      | `gpt-4o-transcribe`| input STT model                             |
| `REALTIME_VAD_MODE`                 | `semantic_vad`     | or `server_vad`                             |
| `REALTIME_SAMPLE_RATE`              | `24000`            | hard-validated to exactly 24000             |
| `AUDIO_DEVICE_INDEX`                | `0`                | mic device (index / name / "default")       |
| `OUTPUT_AUDIO_DEVICE`               | `default`          | speaker device                              |
| `OPENAI_API_KEY`                    | —                  | required for realtime                       |

`uses_realtime` requires `INPUT_MODE=mic`, a non-empty `OPENAI_API_KEY`, a valid VAD
mode, and `REALTIME_SAMPLE_RATE == 24000`. Note `validate_runtime` **skips**
`validate_for_active_provider()` for realtime, so `ACTIVE_LLM` is irrelevant here.

---

## Dependencies

- `websockets>=14.0,<16.0` — async client (`websockets.asyncio.client.connect`,
  uses the `additional_headers=` kwarg, which is the v11+ name).
- `sounddevice>=0.4.7` (PortAudio) — `RawInputStream` / `RawOutputStream`.
- `soundfile>=0.12.1` — `SoundFile.buffer_write(data, dtype)` for raw-bytes WAV writes.
- `openai` — **not** used on this path (raw WebSocket, not the SDK).

---

## Known Issues

Ordered roughly by severity. Full detail is in the audit issue list.

1. **P0 — Missing `?model=` query parameter on the WebSocket URL.**
   `REALTIME_URL` is the bare `wss://api.openai.com/v1/realtime`. The OpenAI GA
   endpoint **requires** `?model=<model>` in the URL; without it the handshake returns
   **400 Bad Request** and the session never opens. The model in `session.update` does
   not substitute for the query param. This path cannot connect as written.

2. **P1 — Blocking `output_stream.write()` stalls the event loop.**
   The AI-audio speaker write happens synchronously inside the receiver coroutine. A
   blocking PortAudio write (full output buffer) freezes the *entire* asyncio loop —
   sender, receiver, barge-in handling, and stop detection all stall together. Should
   run on a thread / dedicated playback queue.

3. **P2 — Manual `input_audio_buffer.commit` on stop conflicts with VAD.**
   With `semantic_vad`/`server_vad`, the server auto-commits. Sending an explicit
   commit at shutdown triggers a server `error` ("buffer too small"/"already
   committed"), which the processor turns into a `RuntimeError`.

4. **P2 — No network-drop / reconnect handling.**
   A mid-recording disconnect raises out of `async for raw_event in websocket`. The
   `finally` still publishes WAV stems, but there is no reconnect and the session
   simply dies; an in-flight `ConnectionClosed` may surface as an unhandled error.

5. **P2 — Mic queue silently drops audio when full.**
   `_enqueue_audio` discards frames if the 256-slot queue is full (back-pressure from
   a stalled sender, e.g. during issue #2). Dropped mic audio = lost words, no warning.

6. **P3 — Windows ENTER-to-stop quirks.**
   The `add_reader(stdin)` primary path is dead on Windows; the `to_thread(input)`
   fallback works but cannot be cancelled cleanly, so the blocked input thread can
   linger until the next keystroke after the session ends.

7. **P3 — Default model/transcription ids may lag.**
   `gpt-realtime` and `gpt-4o-transcribe` are valid but newer ids exist
   (`gpt-realtime-2`, `gpt-realtime-1.5`, `gpt-realtime-whisper`). Verify against the
   account's available models.

8. **Test gap.** Tests exercise the `RealtimeEventProcessor` event handlers and
   `build_session_update` shape only. The connection URL, headers, the
   sender/receiver/stopper orchestration, the commit-on-stop logic, audio device
   setup, and WAV publishing are entirely unmocked/untested — including the P0 URL bug.
