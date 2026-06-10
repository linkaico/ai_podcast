# 04 — Audio I/O (chained mode: capture · transcribe · synthesize · play)

> ✅ **Status (Batch D):** every Known Issue **resolved** — AUD-01 (system-default mic), AUD-02 (Windows `system` playback), AUD-03 (`sdk` playback via sounddevice, no ffmpeg), AUD-04 (recording cap), AUD-05 (Deepgram `listen.v1.media` path), AUD-06 (`OUTPUT_AUDIO_DEVICE` routing), AUD-08 (multichannel signal), AUD-09 (real WAV/device/playback tests). Per-ticket status: [`../../tickets/README.md`](../../tickets/README.md).

Audit context for the chained-mode audio layer: microphone capture, host
speech-to-text, AI text-to-speech, and local playback. This is the path used
when `CONVERSATION_MODE=chained` (the realtime speech-to-speech path is a
separate module audited under [06-realtime](../06-realtime/README.md)).

**In scope (audited):**
- [pipeline/stt.py](../../../pipeline/stt.py) — mic recording (`sounddevice`/`soundfile`/`numpy`), Deepgram transcription, xAI transcription, text-input capture
- [pipeline/tts.py](../../../pipeline/tts.py) — ElevenLabs synthesis, xAI synthesis, dry-run text artifacts, playback
- [tests/test_stt.py](../../../tests/test_stt.py), [tests/test_tts.py](../../../tests/test_tts.py)
- Audio/STT/TTS knobs in [config/settings.py](../../../config/settings.py) (`audio_sample_rate`, `audio_channels`, `audio_device_index`, `deepgram_model`, `elevenlabs_*`, `xai_*`, `playback_mode`, `tts_mode`, `stt_mode`) — read for context; settings validation itself is another agent's scope
- [requirements.txt](../../../requirements.txt) lines for `deepgram-sdk` (`>=7.1.0,<8.0.0`), `elevenlabs` (`>=1.16.0`), `sounddevice`, `soundfile`, `numpy`, `requests`
- [pipeline/reliability.py](../../../pipeline/reliability.py) — only as the retry/timeout wrapper used by STT/TTS

**Callers (for context, not audited here):**
- [main.py](../../../main.py) `run_episode` → `_capture_host_turn` (stt) and `speak` (tts)

---

## Purpose of this layer

In chained mode each turn is: **record host mic → transcribe to text → (LLM) →
synthesize AI text to audio → save + optionally play**. This layer owns the two
ends (capture/transcribe and synthesize/play) and the on-disk audio artifacts.
It is provider-pluggable on both ends:

- **STT:** `STT_MODE = deepgram | xai` (only used when `INPUT_MODE=mic`)
- **TTS:** `TTS_MODE = dry-run | elevenlabs | xai`

Everything has a **dry-run** degenerate path so the pipeline runs with no audio
hardware and no paid APIs: `INPUT_MODE=text` reads typed lines and `TTS_MODE=dry-run`
writes the spoken text to a `.txt` file instead of audio.

All provider SDKs / `requests` are imported lazily inside their functions, so a
missing dependency only fails when that provider is actually selected.

---

## Recording flow (`INPUT_MODE=mic`)

`record_until_keypress` ([stt.py:39](../../../pipeline/stt.py)):

```
record_until_keypress(settings, turn_index, input_fn, output_fn, output_dir)
  ├─ guard: settings.uses_microphone_input            (else RuntimeError)
  ├─ import numpy / sounddevice / soundfile           (lazy; RuntimeError if missing)
  ├─ device = _input_device(settings.audio_device_index)   [stt.py:205]
  │      "" or "default" → None ; "12" → int 12 ; "Mic Name" → str passthrough
  ├─ with sd.InputStream(samplerate, channels, dtype="int16", device, callback):
  │        input_fn("")            # BLOCKS the main thread until ENTER
  │        callback appends indata.copy() to audio_chunks   (PortAudio thread)
  ├─ if not audio_chunks            → RuntimeError "No microphone audio…"
  ├─ audio_data = np.concatenate(audio_chunks, axis=0)
  ├─ if audio_data.size == 0        → RuntimeError "Captured … empty"
  └─ write audio/input/turn_NNNNNN.wav   (atomic: .tmp + os.replace)   [stt.py:88-91]
```

Notes:
- **Stop mechanism is not a keypress listener; it is a blocking `input()`** on the
  main thread. The `InputStream` runs its `callback` on PortAudio's own thread for
  the entire time `input_fn("")` is blocked. Pressing ENTER returns from `input()`,
  the `with` block exits, the stream closes. This works cross-platform but means the
  recording length is exactly "however long until the user presses ENTER".
- **dtype is `int16`.** `soundfile.write(path, int16_ndarray, samplerate)` infers
  subtype `PCM_16` from the array dtype → a standard 16-bit PCM WAV. Sample rate is
  written into the WAV header from `settings.audio_sample_rate` (16000). Correct and
  lossless; no normalization/clipping is applied (raw int16 from PortAudio).
- **Buffer growth is unbounded.** `audio_chunks` holds every block in memory until
  ENTER. At int16/mono/16 kHz that is ~1.92 MB per minute — fine for normal turns,
  but there is no cap or max-duration guard.
- **Files are per-turn, atomic, collision-safe.** `turn_NNNNNN.wav` keyed on
  `turn_index`; written to a hidden `.{stem}.{uuid}.tmp.wav` then `os.replace`d, so a
  crash mid-write never leaves a truncated `turn_*.wav`. Re-recording the same turn
  overwrites deterministically.

`list_input_devices` ([stt.py:18](../../../pipeline/stt.py)) enumerates
`sd.query_devices()` and keeps only devices with `max_input_channels > 0`
(used by `python main.py --list-devices` and by preflight's device check).

---

## STT provider paths

Entry point: `transcribe` → `transcribe_with_client`
([stt.py:95](../../../pipeline/stt.py)). Dispatch order:

1. **text dry-run** — if `uses_text_input` and the path ends in `.txt`, just
   `read_text().strip()` ([stt.py:109](../../../pipeline/stt.py)). No provider.
2. **xAI** — if `uses_xai_stt` → `_transcribe_with_xai`.
3. **Deepgram** — otherwise → `_transcribe_with_deepgram_provider`.

### Deepgram ([stt.py:123](../../../pipeline/stt.py))
- Requires `DEEPGRAM_API_KEY`; checks the file exists and is non-empty before any call.
- Default client: `DeepgramClient(api_key=..., timeout=provider_timeout_seconds)`.
- The actual call lives in `_transcribe_with_deepgram` ([stt.py:215](../../../pipeline/stt.py)),
  which **probes the client capability in order**:
  1. `client.listen.v1.media.transcribe_file(request=bytes, model=…, language="en", smart_format=True, request_options={timeout_in_seconds})`
  2. `client.listen.rest.v("1").transcribe_file({"buffer": bytes}, options)`
  3. `client.listen.prerecorded.v("1").transcribe_file({"buffer": bytes}, options)`
- **This ordering matches the pinned SDK.** `deepgram-sdk` 7.0.0 (2026-04-27) was a
  full client regeneration; the current file-transcription call is
  `client.listen.v1.media.transcribe_file(request=…, model=…)` and the response is
  attribute-accessible as `response.results.channels[0].alternatives[0].transcript`.
  Branches 2–3 are the pre-v7 (v3/v4-era) shapes, retained only as fallbacks / for
  the injected test fakes. At `>=7.1.0,<8.0.0` the **media branch is the live path**.
- Transcript extraction `_extract_transcript` ([stt.py:275](../../../pipeline/stt.py))
  handles both a dict response and an attribute-object response; returns `""` on any
  shape mismatch, and the caller raises `RuntimeError("…empty transcript")` on empty.

### xAI ([stt.py:168](../../../pipeline/stt.py))
- Requires `XAI_API_KEY`; same exists/non-empty file guards.
- `_post_xai_stt` ([stt.py:243](../../../pipeline/stt.py)) — multipart POST:
  ```
  POST https://api.x.ai/v1/stt
  Authorization: Bearer <key>
  data  = {"format": "true", "language": settings.xai_stt_language}
  files = {"file": (path.name, bytes, content_type)}
  ```
- **Endpoint and shape are real and essentially correct** (xAI launched standalone
  Grok STT/TTS in April 2026). The transcript is in the JSON `text` key, which
  `_extract_xai_transcript` ([stt.py:290](../../../pipeline/stt.py)) reads. `requests`
  serializes `data` fields before `files`, satisfying xAI's "file must come last in
  the multipart form" requirement. Content-type is derived from the file extension
  (`.wav` → `audio/wav`) via `_audio_content_type` ([stt.py:262](../../../pipeline/stt.py)).
- Minor: `format` is xAI's text-normalization toggle, sent literally as the string
  `"true"`. xAI treats it as a boolean form field; the string form works but is the
  only place a literal `"true"`/`"false"` string is used.

Both providers run through `retry_call` (provider=`deepgram`/`xai`, stage=`transcribe`)
with `is_transient_provider_error` as the retry predicate.

---

## TTS provider paths

Entry point: `speak` → `speak_with_client` ([tts.py:25](../../../pipeline/tts.py)).
Dispatch order:

1. **dry-run** — `uses_dry_run_tts` → write `audio/output/turn_NNNNNN.txt` with the
   spoken text (atomic), return that path ([tts.py:36](../../../pipeline/tts.py)).
2. **xAI** — `uses_xai_tts` → `_speak_with_xai`.
3. **ElevenLabs** — otherwise → `_speak_with_elevenlabs`.

### ElevenLabs ([tts.py:67](../../../pipeline/tts.py))
- Requires `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`.
- Default client: `ElevenLabs(api_key=…, timeout=provider_timeout_seconds)` — the
  `timeout` kwarg is valid (SDK default is 240s).
- Call:
  ```python
  client.text_to_speech.convert(
      voice_id=settings.elevenlabs_voice_id,
      text=text,
      model_id=settings.elevenlabs_model,        # "eleven_flash_v2_5"
      output_format=settings.elevenlabs_output_format,   # "mp3_22050_32"
      voice_settings=_voice_settings(settings),  # VoiceSettings or dict fallback
  )
  ```
- **`convert()` returns `Iterator[bytes]`** (a streaming generator of chunks), not a
  single `bytes`. `_coerce_audio_bytes` ([tts.py:193](../../../pipeline/tts.py))
  correctly handles `bytes`/`bytearray`/iterable-of-bytes by joining non-empty
  chunks. `model_id`/`output_format`/`voice_settings` are all valid for the current
  SDK. Output file extension is derived from the format prefix by
  `_elevenlabs_extension` ([tts.py:201](../../../pipeline/tts.py)) →
  `mp3_22050_32` ⇒ `.mp3`.
- Writes `audio/output/turn_NNNNNN.<ext>` atomically, then calls `_try_play_audio`.

### xAI ([tts.py:121](../../../pipeline/tts.py))
- Requires `XAI_API_KEY`.
- `_post_xai_tts` ([tts.py:165](../../../pipeline/tts.py)) — JSON POST:
  ```
  POST https://api.x.ai/v1/tts
  Authorization: Bearer <key> ; Content-Type: application/json
  json = {"text": text, "voice_id": settings.xai_tts_voice, "language": settings.xai_tts_language}
  ```
- **Endpoint, fields, and default voice are real and correct.** xAI TTS returns raw
  audio bytes (default codec **MP3 @ 24 kHz / 128 kbps** when `output_format` is
  omitted), and the response body is read via `_response_content`
  ([tts.py:184](../../../pipeline/tts.py)). The code always writes `.mp3`, which
  matches the default codec — consistent. `voice_id` defaults to `eve` (a real xAI
  voice); `language` is required by xAI and is always sent.
- Note: xAI's `output_format` is a **nested object** (`{codec, sample_rate, bit_rate}`),
  not a top-level string; the code does not send it and relies on the MP3 default, so
  the `.mp3` extension is always correct. There is no knob to request WAV/PCM from xAI.

Both providers run through `retry_call` (stage=`tts`).

---

## Playback modes (`PLAYBACK_MODE`)

`_try_play_audio` ([tts.py:238](../../../pipeline/tts.py)) — best-effort, wrapped in
a broad `except Exception` so a playback failure never loses the saved file:

| Mode | Behavior | Status |
|---|---|---|
| `file-only` (default) | returns immediately; file saved, no sound | Works everywhere |
| `sdk` | `elevenlabs.play(audio_bytes)` | **Requires `ffmpeg`/`ffplay` on PATH** — `play()` defaults to `use_ffmpeg=True` and raises `ValueError` if `ffplay` is absent. Failure is swallowed → "playback skipped". |
| `system` | `_system_play(output_path)` | **macOS only** — calls `afplay`; on every non-Darwin platform (incl. Windows) it raises `RuntimeError`, swallowed → "playback skipped". |

So on Windows the only *audible* path is `sdk`, and only if the user has installed
ffmpeg separately (undocumented). `system` never plays on Windows. In both audible
modes the saved file is the reliable artifact; sound is opportunistic.

---

## Audio formats / sample rates — where they must match providers

| Stage | Format produced/consumed | Source of truth | Match status |
|---|---|---|---|
| Mic capture | 16-bit PCM WAV, mono, 16 kHz | `AUDIO_SAMPLE_RATE=16000`, `AUDIO_CHANNELS=1`, `dtype="int16"` | WAV header carries the rate; Deepgram/xAI read it from the container — no fixed expectation, so 16 kHz is fine. |
| Deepgram STT | WAV bytes in `request=` | `deepgram_model="nova-3"` | Deepgram decodes the WAV container; sample rate is read from the header. OK. |
| xAI STT | WAV multipart upload | content-type by extension | xAI supports WAV among 12 formats; `format`/`language` set normalization. OK. |
| ElevenLabs TTS | `mp3_22050_32` → `.mp3` | `ELEVENLABS_OUTPUT_FORMAT` | 22.05 kHz / 32 kbps MP3. Extension derived from the format string. OK. |
| xAI TTS | default MP3 24 kHz/128 → `.mp3` | (no knob; default) | Extension hardcoded `.mp3`; matches xAI's default codec. OK. |
| Realtime (out of scope) | 24 kHz PCM | `REALTIME_SAMPLE_RATE=24000` | Separate path; see 06-realtime. |

There is **no required cross-match between the capture rate (16 kHz) and any TTS
rate** — STT and TTS are independent legs. The one place a mismatch *could* bite is
`AUDIO_CHANNELS`: capturing >1 channel produces a multichannel WAV, but the Deepgram
call does not set `multichannel=true`, so a stereo recording is treated as a single
mixed/again channel by the provider (acceptable for a single host mic, where mono is
the intended config).

---

## Config knobs that affect this layer

| Setting | Default | Effect |
|---|---|---|
| `INPUT_MODE` | `text` | `mic` enables `record_until_keypress`; `text` reads typed lines. |
| `STT_MODE` | `deepgram` | `deepgram` or `xai` transcription (only when `INPUT_MODE=mic`). |
| `TTS_MODE` | `dry-run` | `dry-run` (text file) / `elevenlabs` / `xai`. |
| `AUDIO_DEVICE_INDEX` | `0` | Passed to PortAudio. `""`/`default` → system default; integer → device index; other string → device name. **Default `0` is a literal device index, not the default device** (see Known Issues). |
| `AUDIO_SAMPLE_RATE` | `16000` | Capture rate + WAV header. |
| `AUDIO_CHANNELS` | `1` | Capture channel count. |
| `DEEPGRAM_MODEL` | `nova-3` | Deepgram model. |
| `ELEVENLABS_MODEL` | `eleven_flash_v2_5` | ElevenLabs `model_id`. |
| `ELEVENLABS_VOICE_ID` | `""` | Required for ElevenLabs; no default. |
| `ELEVENLABS_OUTPUT_FORMAT` | `mp3_22050_32` | Codec/rate/bitrate + file extension. |
| `ELEVENLABS_STABILITY/SIMILARITY_BOOST/STYLE/SPEED` | 0.45/0.80/0.35/1.0 | Passed as `VoiceSettings`. |
| `XAI_STT_LANGUAGE` | `en` | xAI STT `language` form field. |
| `XAI_TTS_VOICE` | `eve` | xAI TTS `voice_id`. |
| `XAI_TTS_LANGUAGE` | `en` | xAI TTS `language` (required by xAI). |
| `PLAYBACK_MODE` | `file-only` | `file-only` / `sdk` (ffmpeg) / `system` (macOS only). |
| `OUTPUT_AUDIO_DEVICE` | `default` | **Read into settings but never used** by this layer (no output-device targeting exists). |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | Passed to Deepgram/ElevenLabs clients and to xAI `requests` `timeout=`. |
| `PROVIDER_MAX_RETRIES` | `1` | `retry_call` runs `max_retries+1` attempts. |

---

## System dependencies

- **PortAudio** — required by `sounddevice` for any mic capture / `--list-devices`.
  Bundled in the `sounddevice` wheels on Windows/macOS; on Linux it is a system
  package (`libportaudio2`). Not documented in the repo.
- **ffmpeg (`ffplay`)** — required *only* for `PLAYBACK_MODE=sdk` (ElevenLabs
  `play()`). Not a Python dependency, not in `requirements.txt`, not documented.
  Without it, `sdk` playback raises and is swallowed.
- **MP3 encoding** — done server-side by ElevenLabs/xAI; the pipeline only writes the
  returned bytes, so no local MP3 encoder is needed. `soundfile` writes WAV only
  (capture side), which needs no extra codec.
- **`afplay`** — macOS built-in, used by `PLAYBACK_MODE=system`. No Windows equivalent
  is wired up.

---

## Integration points

- **Caller:** [main.py](../../../main.py) `run_episode` → `_capture_host_turn`
  ([main.py:133](../../../main.py)) drives `record_until_keypress` + `transcribe`,
  with an interactive accept/re-record/edit/skip/quit confirmation loop
  (`CONFIRM_TRANSCRIPT`). `speak` ([main.py:107](../../../main.py)) is called with the
  AI text and `memory.audio_output_dir`. Both are wrapped in `try/except Exception`
  that records a structured event and breaks the loop on failure.
- **Output dirs:** default to `root/audio/input` and `root/audio/output`
  ([settings.py:108-114](../../../config/settings.py)); `run_episode` overrides them
  with the per-session `memory.audio_input_dir` / `audio_output_dir`.
- **Preflight:** [pipeline/preflight.py](../../../pipeline/preflight.py) checks the
  selected provider SDKs are importable and (for mic mode) that `AUDIO_DEVICE_INDEX`
  resolves to a real input device.
- **Reliability:** every live provider call goes through
  [reliability.py](../../../pipeline/reliability.py) `retry_call`. Note `retry_call`
  **discards `timeout_seconds`** (`del timeout_seconds`) — it does not enforce a
  wall-clock timeout; each client/`requests` call enforces its own.

---

## Known Issues

Severity in parentheses; full detail and fixes are in the audit issue list (Deliverable 2).

1. **(P1) `AUDIO_DEVICE_INDEX` defaults to `0`, a literal PortAudio index — not the
   system default.** On Windows, index 0 is frequently a host-API/output device or a
   different capture endpoint than the user's mic, causing a `PortAudioError`
   (invalid channels/samplerate) or a silent recording. A safer default is `default`
   (which `_input_device` already maps to `None`). ([settings.py:72](../../../config/settings.py),
   [.env.example:30](../../../.env.example), [stt.py:205](../../../pipeline/stt.py))

2. **(P1) `PLAYBACK_MODE=system` cannot play audio on Windows.** `_system_play`
   implements only macOS `afplay` and raises on every other platform. The error is
   swallowed by the best-effort wrapper, so the operator gets silent "playback
   skipped" with no audible output and no clear guidance. ([tts.py:264](../../../pipeline/tts.py))

3. **(P1) `PLAYBACK_MODE=sdk` needs ffmpeg, which is undocumented and unpinned.**
   `elevenlabs.play()` defaults to `use_ffmpeg=True` and raises `ValueError` if
   `ffplay` is missing. Most Windows machines lack ffmpeg on PATH, so `sdk` silently
   degrades to no sound. Either pass `use_ffmpeg=False` (uses the already-required
   `sounddevice`+`soundfile`) or document the ffmpeg install. ([tts.py:251-254](../../../pipeline/tts.py))

4. **(P2) Unbounded in-memory recording buffer / no max duration.** `audio_chunks`
   accumulates every block until ENTER; a forgotten/very long recording grows memory
   linearly (~1.9 MB/min mono) with no cap, and the whole file is held twice
   (chunks + concatenated array) at write time. ([stt.py:61-84](../../../pipeline/stt.py))

5. **(P2) No `model`/options on the legacy Deepgram fallback branches; default branch
   relies entirely on `listen.v1.media`.** If a future `deepgram-sdk` 7.x point release
   reorganizes `listen.v1.media`, the default path (`client_factory is None`, `options
   = None`) falls through to `rest`/`prerecorded` with `options=None`, sending no
   model/smart_format. Pin behavior is currently fine, but there is no explicit
   version assertion. ([stt.py:138-149](../../../pipeline/stt.py), [stt.py:232-238](../../../pipeline/stt.py))

6. **(P2) `OUTPUT_AUDIO_DEVICE` is configured but never used.** It is parsed into
   settings and surfaced in `.env.example`, implying output-device routing exists, but
   no playback path consumes it — misleading for operators trying to route AI voice to
   a virtual cable (OBS). ([settings.py:73](../../../config/settings.py),
   [.env.example:31](../../../config/settings.py))

7. **(P3) `retry_call` advertises a timeout it does not enforce.** `timeout_seconds`
   is passed in then `del`-eted; only the per-client/`requests` timeout applies. A
   provider that ignores its client timeout could hang a turn. ([reliability.py:51](../../../pipeline/reliability.py))

8. **(P3) Multichannel capture is not signaled to Deepgram.** `AUDIO_CHANNELS>1`
   produces a multichannel WAV but the Deepgram call omits `multichannel=true`, so
   extra channels are collapsed by the provider. Harmless for a single mono host mic
   (the intended config) but surprising if someone raises the channel count.
   ([stt.py:72-78](../../../pipeline/stt.py), [stt.py:224-229](../../../pipeline/stt.py))

9. **(P3) Test suite mocks every provider; no real audio/PortAudio path is exercised.**
   `record_until_keypress`, real WAV round-tripping, device resolution, the
   `_coerce_audio_bytes` generator branch with a true generator, and all playback
   modes are untested. Coverage is shape-of-call only. ([tests/test_stt.py](../../../tests/test_stt.py),
   [tests/test_tts.py](../../../tests/test_tts.py))
