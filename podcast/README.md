# AI Podcast Backend

This is the local backend for Florian's AI video podcast. It supports a native low-latency OpenAI Realtime conversation for recording and preserves a chained STT -> LLM -> TTS fallback plus a safe offline dry-run mode.

Commands are written for **Windows PowerShell** (the project's target platform); bash equivalents are noted where helpful.

## What Works Now

- `python main.py pilot` starts a typed dry-run episode loop.
- `CONVERSATION_MODE=realtime INPUT_MODE=mic` (set in `.env`) starts native speech-to-speech recording with barge-in.
- Host turns are saved to `sessions\*.json` after every turn and media lives beneath a unique session directory. Sessions are file-locked, so a second process can't clobber an open recording.
- Dry-run AI responses are generated locally through the LLM adapter.
- Dry-run voice artifacts are saved beneath `audio\<session_id>\output\turn_*.txt`.
- `INPUT_MODE=mic` records host WAV files and transcribes them with Deepgram or xAI.
- `TTS_MODE=elevenlabs` or `TTS_MODE=xai` generates MP3 stems for the AI voice.
- A failed provider call mid-turn prompts `[Enter=retry] s=skip / q=quit` instead of ending the episode.
- Base and per-episode prompts are loaded from `config\prompts\`.

For the full step-by-step recording workflow, use [docs/AI_PODCAST_OPERATOR_GUIDE.md](docs/AI_PODCAST_OPERATOR_GUIDE.md).

## System Prerequisites

- **PortAudio** / **libsndfile**: bundled in the `sounddevice` / `soundfile` wheels (Windows, macOS) — nothing extra to install. On Linux, install PortAudio via your package manager.
- **ffmpeg**: not required. Default `PLAYBACK_MODE=file-only` plays nothing; `PLAYBACK_MODE=sdk` plays via `sounddevice` (no ffmpeg).
- **VB-Audio Virtual Cable** (Windows) / **BlackHole 2ch** (macOS): optional, only for routing the AI voice into OBS live.

## Setup

```powershell
cd podcast
py -m venv .venv
& .venv\Scripts\Activate.ps1
pip install -r requirements.txt          # runtime only
# pip install -r requirements-dev.txt    # adds pytest for running the tests
Copy-Item .env.example .env
```

The default `.env.example` uses `CONVERSATION_MODE=dry-run`, `ACTIVE_LLM=dry-run`, `INPUT_MODE=text`, and `TTS_MODE=dry-run`, which do not require API keys.

## Primary Live Recording: OpenAI Realtime

Set in `.env`:

```env
CONVERSATION_MODE=realtime
OPENAI_API_KEY=your_key_here
INPUT_MODE=mic
REALTIME_MODEL=gpt-realtime
REALTIME_VOICE=marin
REALTIME_TRANSCRIPTION_MODEL=gpt-4o-transcribe
REALTIME_VAD_MODE=semantic_vad
REALTIME_SAMPLE_RATE=24000
```

Then run:

```powershell
python main.py pilot --doctor
python main.py pilot
```

Realtime mode streams microphone PCM audio and AI audio over WebSocket, automatically interrupts AI playback when you begin speaking, and records session-local `live_host.wav` and `live_ai.wav` stems for editing. (`--max-turns` does not apply to realtime; stop with ENTER.)

## Run A Dry-Run Episode

```powershell
python main.py pilot
```

Type a host turn and press Enter. Type `q`, `quit`, or `end` to stop the episode.

Resume the latest saved session for an episode:

```powershell
python main.py pilot --resume
```

Resume a specific session file:

```powershell
python main.py pilot --session sessions\pilot_YYYYMMDD_HHMMSS_<id>.json
```

Run preflight checks before recording:

```powershell
python main.py pilot --doctor
```

List available microphone input devices:

```powershell
python main.py --list-devices
```

## Chained Fallback With Microphone Input

Set these values in `.env`:

```env
INPUT_MODE=mic
CONVERSATION_MODE=chained
STT_MODE=deepgram
DEEPGRAM_API_KEY=your_key_here
DEEPGRAM_MODEL=nova-3
AUDIO_DEVICE_INDEX=default
AUDIO_SAMPLE_RATE=16000
AUDIO_CHANNELS=1
```

`AUDIO_DEVICE_INDEX=default` uses the system default microphone; set an index or device name (from `--list-devices`) to pin a specific one. Then run:

```powershell
python main.py pilot
```

Each host turn records until you press Enter, saves a WAV file under `audio\<session_id>\input\turn_<n>.wav`, then sends it to Deepgram for transcription.

To use xAI instead of Deepgram for transcription, set in `.env`:

```env
INPUT_MODE=mic
STT_MODE=xai
XAI_API_KEY=your_key_here
XAI_STT_LANGUAGE=en
```

The recording flow is unchanged: each host turn is still saved as a WAV file before transcription.

By default, mic mode asks you to confirm each transcript before it is sent to the LLM. At the confirmation prompt:

- Press Enter to accept
- Type `r` to re-record
- Type `e` to edit the transcript
- Type `s` to skip the turn
- Type `q` to end the episode

To inspect local input devices:

```powershell
python -m sounddevice
```

## Run With ElevenLabs Output

Set these values in `.env`:

```env
TTS_MODE=elevenlabs
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
ELEVENLABS_MODEL=eleven_flash_v2_5
ELEVENLABS_OUTPUT_FORMAT=mp3_22050_32
PLAYBACK_MODE=file-only
```

Each AI turn saves a stem inside `audio\<session_id>\output\turn_<n>.mp3`. Local playback is controlled by `PLAYBACK_MODE`: `file-only` (default, no playback), `sdk` (play via sounddevice — no ffmpeg — and route to `OUTPUT_AUDIO_DEVICE`), or `system` (OS default player; `os.startfile` on Windows). The saved stem is always the reliable artifact.

## Run With xAI Voice Output

Set these values in `.env`:

```env
TTS_MODE=xai
XAI_API_KEY=your_key_here
XAI_TTS_VOICE=eve
XAI_TTS_LANGUAGE=en
PLAYBACK_MODE=file-only
```

Each AI turn saves an MP3 stem inside `audio\<session_id>\output\turn_<n>.mp3`, matching the ElevenLabs artifact layout.

## Real Recording Checklist

1. Run `python main.py pilot --doctor`.
2. Run `python main.py --list-devices` and set `AUDIO_DEVICE_INDEX` (or leave `default`).
3. Run a one-turn chained rehearsal (set modes in `.env`, then):

```powershell
python main.py pilot --confirm-transcript --max-turns 1
```

4. Start OBS and confirm Florian's mic and the AI voice track are both visible.
5. Prefer the realtime episode: set `CONVERSATION_MODE=realtime INPUT_MODE=mic` in `.env`, then `python main.py <episode_name>`.
6. After recording, use `sessions\*.json`, `audio\<session_id>\`, and `exports\*.md` as the editing source of truth.

## OBS Routing Checklist

1. Install VB-Audio Cable on Windows or BlackHole 2ch on macOS (optional — only for live AI routing).
2. In OBS, add one Audio Input Capture source for Florian's microphone.
3. Add a second Audio Input Capture source for the virtual cable.
4. Set `PLAYBACK_MODE=sdk` and `OUTPUT_AUDIO_DEVICE=<the cable>` so the AI voice is routed to OBS while recording.
5. Import the AI stems from `audio\<session_id>\output\` into Descript or your editor if live routing is not clean enough.

## OpenClaw Integration

`integrations/openclaw_tools.py` is an **in-process Python library** (same machine, same interpreter — there is no remote endpoint or service registration; network registration is deferred). Import the helpers directly from your local Python:

```python
from integrations.openclaw_tools import (
    episode_artifacts,
    export_transcript,
    latest_session,
    list_sessions,
    load_session,
    run_episode,
    write_episode_context,
)

write_episode_context(
    "pilot",
    "Episode research context goes here.",
    sources=[{"title": "Reference transcript", "url": "https://example.com"}],
)

latest = latest_session("pilot")
payload = load_session(latest["path"])
artifacts = episode_artifacts("pilot")
export = export_transcript(latest["path"])
```

Callable surface:

- `run_episode(name, resume=False, session_path=None, max_turns=None, input_fn=None, output_fn=None)` — pass `input_fn`/`output_fn` to drive the chained loop non-interactively; omit them for the console.
- `write_episode_context(episode_name, content, sources=None)`
- `list_sessions(episode_name=None)`
- `load_session(session_path)` — `session_path` is contained to `sessions\`; out-of-tree paths are rejected.
- `latest_session(episode_name)`
- `episode_artifacts(episode_name)`
- `export_transcript(session_path, format="markdown")`

Episode context files are written to `config\prompts\episodes\<episode_name>.txt` and are automatically appended by `load_system_prompt()` the next time that episode runs (the episode name is sanitized identically on write and read).

Markdown transcript exports are written to `exports\*.md` for post-production review and clip selection.

## Episode Prompts

- Base persona: `config\prompts\base_system.txt`
- Episode-specific context: `config\prompts\episodes\<episode_name>.txt`

If `python main.py pilot` is running, the backend automatically appends `config\prompts\episodes\pilot.txt` when that file exists.

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

## License

Proprietary — all rights reserved. See [LICENSE](LICENSE).

## Deferred To The Next Slice

- Realtime auto-reconnect on mid-session network drop, true mic back-pressure, and a cancellable Windows stop thread (the lighter forms — clean-stop, drop-logging, documented limitation — are in place).
- Exact cross-platform OBS device targeting from code.
- Network/service-level OpenClaw registration.
