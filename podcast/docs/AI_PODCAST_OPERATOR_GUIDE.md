# AI Podcast Operator Guide

This guide is the practical checklist for a real recorded AI podcast session. The primary path is OpenAI Realtime native speech-to-speech with interruption support; the chained Deepgram/xAI -> LLM -> ElevenLabs/xAI path remains available as fallback.

Commands are written for **Windows PowerShell** (the project's target). Bash equivalents are noted where useful. Clipping and social distribution are intentionally out of scope here.

## 1. What Still Needs To Be Done

Before the first real recording, complete these one-time setup tasks.

### System Prerequisites

- **PortAudio** (microphone capture/playback): bundled inside the `sounddevice` wheel on Windows and macOS — nothing to install. On Linux, install PortAudio via your package manager.
- **libsndfile** (WAV/FLAC/MP3 decode): bundled inside the `soundfile` wheel.
- **ffmpeg**: **not required.** The default `PLAYBACK_MODE=file-only` writes stems and plays nothing; `PLAYBACK_MODE=sdk` plays via `sounddevice`/`soundfile` (no ffmpeg).
- **VB-Audio Virtual Cable** (Windows) or **BlackHole 2ch** (macOS): **optional** — only needed if you want the AI voice routed into OBS live (see OBS Audio Setup). The saved stems are always the reliable fallback.

### Local Python Setup

From this workspace, in PowerShell:

```powershell
cd podcast
py -m venv .venv
& .venv\Scripts\Activate.ps1
pip install -r requirements.txt          # runtime; add -r requirements-dev.txt for tests
Copy-Item .env.example .env
```

### API Keys

Open `podcast\.env` and fill in the services you plan to use:

```env
DEEPGRAM_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
XAI_API_KEY=...
```

Choose one real LLM provider and set a real model id (the loader rejects the placeholder `dry-run-v1` for a live provider):

```env
ACTIVE_LLM=anthropic
ACTIVE_MODEL=claude-opus-4-8        # or claude-sonnet-4-6 for lower cost
ANTHROPIC_API_KEY=...
```

Or:

```env
ACTIVE_LLM=openai
ACTIVE_MODEL=gpt-4o
OPENAI_API_KEY=...
OPENAI_API_MODE=responses
```

Or:

```env
ACTIVE_LLM=google
ACTIVE_MODEL=gemini-2.0-flash
GOOGLE_API_KEY=...
```

`PROVIDER_MAX_OUTPUT_TOKENS` (default 2048) caps each AI reply; raise it if replies get cut off. Keep `.env` private — it is gitignored and should never be shared.

### Primary Real Recording Mode

For the lowest-hesitation live podcast loop, set:

```env
CONVERSATION_MODE=realtime
OPENAI_API_KEY=...
INPUT_MODE=mic
REALTIME_MODEL=gpt-realtime
REALTIME_VOICE=marin
REALTIME_TRANSCRIPTION_MODEL=gpt-4o-transcribe
REALTIME_VAD_MODE=semantic_vad
REALTIME_SAMPLE_RATE=24000
PROVIDER_TIMEOUT_SECONDS=60
PROVIDER_MAX_RETRIES=1
```

Realtime mode streams AI speech as it is generated, flushes playback when Florian starts talking, and stores host/AI WAV stems in `audio/<session_id>/`.

For a chained external-voice fallback, set `CONVERSATION_MODE=chained`, choose `STT_MODE` and `TTS_MODE`, and retain the transcript-confirmation workflow below.

### Microphone Device

List available input devices:

```powershell
python main.py --list-devices
```

By default `AUDIO_DEVICE_INDEX=default` uses the system default microphone. To pin a specific device, set its index (or name) from the list:

```env
AUDIO_DEVICE_INDEX=default        # or an index like 1, or a device name
AUDIO_SAMPLE_RATE=16000
AUDIO_CHANNELS=1
AUDIO_MAX_RECORD_SECONDS=600      # safety cap per mic turn
```

### ElevenLabs Voice

Create or select the AI co-host voice in ElevenLabs, then copy its voice ID into:

```env
ELEVENLABS_VOICE_ID=...
ELEVENLABS_MODEL=eleven_flash_v2_5
ELEVENLABS_OUTPUT_FORMAT=mp3_22050_32
ELEVENLABS_STABILITY=0.45
ELEVENLABS_SIMILARITY_BOOST=0.80
ELEVENLABS_STYLE=0.35
ELEVENLABS_SPEED=1.0
```

Flash v2.5 is the low-latency fallback default. Tune voice settings after a few short rehearsal clips, or use a richer non-live model when regenerating post-production audio.

### xAI Voice Option

To use xAI for both transcription and AI voice output instead of Deepgram plus ElevenLabs, set:

```env
INPUT_MODE=mic
STT_MODE=xai
TTS_MODE=xai
XAI_API_KEY=...
XAI_STT_LANGUAGE=en
XAI_TTS_VOICE=eve
XAI_TTS_LANGUAGE=en
```

The chained turn flow stays the same. New recordings are saved under session-scoped paths: `audio/<session_id>/input/` and `audio/<session_id>/output/`.

### Playback And OBS Audio Routing

`PLAYBACK_MODE` controls what happens to the AI voice locally (the saved stem is written either way):

- `file-only` (default): write the stem, play nothing. No codecs needed.
- `sdk`: decode and play via `sounddevice`/`soundfile` (no ffmpeg). Routes to `OUTPUT_AUDIO_DEVICE` — set this to your VB-Audio cable to feed OBS live.
- `system`: hand the file to the OS default player (`os.startfile` on Windows, `afplay` on macOS). For quick local listening; no device routing.

```env
PLAYBACK_MODE=sdk
OUTPUT_AUDIO_DEVICE=CABLE Input (VB-Audio Virtual Cable)   # or "default", an index, or a name
```

OBS setup:

1. Install VB-Audio Cable (Windows) or BlackHole 2ch (macOS) — optional, only for live AI playback into OBS.
2. In OBS, add Florian's microphone as one audio source.
3. Add the virtual cable as a second audio source and set `PLAYBACK_MODE=sdk` + `OUTPUT_AUDIO_DEVICE=<cable>` to record the AI voice separately.
4. Keep saved stems in `audio/<session_id>/output/` as the reliable fallback even if live routing fails.
5. Run a rehearsal and confirm OBS meters move for the intended sources.

## 2. Preflight Before Every Recording

Run:

```powershell
python main.py <episode_name> --doctor
```

Example:

```powershell
python main.py pilot --doctor
```

Do not record until the result says:

```text
Result: OK
```

The doctor checks settings, the base prompt, writable runtime folders (incl. the real `audio/` tree), **free disk space**, required SDKs, the input device (mic mode), and the **output device** (when sdk/system playback or realtime is enabled). `WARN` lines are advisory and do not fail the check.

## 3. Prepare The Episode Prompt

The base AI co-host persona lives here:

```text
config\prompts\base_system.txt
```

Episode-specific context goes here:

```text
config\prompts\episodes\<episode_name>.txt
```

Example:

```powershell
New-Item -ItemType File -Force config\prompts\episodes\pilot.txt
```

Use the episode file for research notes, topic framing, guest/background context, and things the AI should know before the recording.

OpenClaw-style agents can also write this file in-process (this is a local Python library, not a network service):

```python
from integrations.openclaw_tools import write_episode_context

write_episode_context(
    "pilot",
    "Episode research context goes here.",
    sources=[{"title": "Reference", "url": "https://example.com"}],
)
```

The episode name is sanitized the same way on write and on read, so `"Pilot Episode"` and `"pilot"` resolve consistently.

## 4. Rehearsal Flow

Always run a one-turn rehearsal before the real recording (chained mode):

```powershell
python main.py <episode_name> --confirm-transcript --max-turns 1
```

For the primary realtime rehearsal (note: `--max-turns` does not apply to realtime — stop with ENTER):

```powershell
$env:CONVERSATION_MODE="realtime"; $env:INPUT_MODE="mic"; python main.py pilot
```

For a chained fallback rehearsal using mic, Deepgram, LLM, and ElevenLabs:

```powershell
$env:CONVERSATION_MODE="chained"; $env:INPUT_MODE="mic"; $env:STT_MODE="deepgram"; $env:TTS_MODE="elevenlabs"; python main.py pilot --confirm-transcript --max-turns 1
```

For a real rehearsal using xAI STT and xAI TTS:

```powershell
$env:CONVERSATION_MODE="chained"; $env:INPUT_MODE="mic"; $env:STT_MODE="xai"; $env:TTS_MODE="xai"; python main.py pilot --confirm-transcript --max-turns 1
```

(Setting values directly in `.env` is usually cleaner than per-command env vars.)

During mic mode:

1. Speak your turn.
2. Press Enter to stop recording.
3. Review the transcript.
4. At the confirmation prompt:
   - Press Enter to accept.
   - Type `r` to re-record.
   - Type `e` to edit the transcript.
   - Type `s` to skip the turn.
   - Type `q` to end the episode.
5. Confirm an AI MP3 appears in the session output directory:

```text
audio\<session_id>\output\turn_000000.mp3
```

If a provider call fails mid-turn, the loop does not quit — it prompts `[Enter=retry] s=skip / q=quit` so you can recover the turn. If the rehearsal is bad, tune `.env`, device settings, prompt context, or voice-provider settings before recording.

## 5. Real Recording Flow

Start OBS first. Confirm framing and audio meters. Do **not** launch a second process against the same session — sessions are file-locked and the second launch is refused.

For realtime recording, set `CONVERSATION_MODE=realtime` and `INPUT_MODE=mic` in `.env` (or via `$env:`), then run:

```powershell
python main.py <episode_name>
```

Recommended live rhythm:

1. Speak naturally while the realtime session is active.
2. Interrupt the AI naturally if you want to respond; playback is flushed on detected speech.
3. Press Enter once to end the session.
4. Inspect the saved session-local host and AI stems before editing.

The system saves progress after each turn, so if something fails, you should still have a usable partial session.

## 6. Resume If Something Breaks

Resume the latest session for an episode:

```powershell
python main.py <episode_name> --resume --confirm-transcript
```

Resume a specific session:

```powershell
python main.py <episode_name> --session sessions\<session_file>.json --confirm-transcript
```

"Latest" is chosen by file modification time, so it is correct even if the system clock changed. The resumed session continues writing to the same JSON file and continues turn numbering so new artifacts do not overwrite previous turns.

## 7. Where Outputs Land

Session JSON:

```text
sessions\<episode_name>_YYYYMMDD_HHMMSS_<id>.json
```

Host and AI realtime WAV stems:

```text
audio\<session_id>\input\live_host.wav
audio\<session_id>\output\live_ai.wav
```

Chained fallback stems:

```text
audio\<session_id>\input\turn_<n>.wav
audio\<session_id>\output\turn_<n>.mp3
```

Dry-run AI text outputs:

```text
audio\<session_id>\output\turn_<n>.txt
```

Markdown transcript exports:

```text
exports\*.md
```

Session JSON includes:

- `history`: conversation turns.
- `events`: stage events such as recording, transcription, LLM, TTS, failure, and completion.
- `artifacts`: discovered audio/text files (the authoritative list of this session's media).

## 8. Export A Transcript

From inside `podcast\`, save this as `export.py` and run `python export.py` (or use `python -c`):

```python
from integrations.openclaw_tools import latest_session, export_transcript

latest = latest_session("pilot")
print(export_transcript(latest["path"]))
```

The Markdown export appears in:

```text
exports\
```

Use this for editing notes and post-production review. (Session paths are contained to the `sessions\` directory — paths outside it are rejected.)

## 9. Dry-Run Mode For Testing

To test without APIs (this is the default `.env`):

```powershell
$env:INPUT_MODE="text"; $env:TTS_MODE="dry-run"; $env:ACTIVE_LLM="dry-run"; python main.py pilot
```

This is useful for checking session saving, prompt loading, resume behavior, and transcript exports.

## 10. Troubleshooting

### Running The Tests

```powershell
& .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
```

### Doctor Fails On Missing Key

Check `.env` and confirm the matching mode is enabled. For example, `INPUT_MODE=mic STT_MODE=deepgram` requires `DEEPGRAM_API_KEY`, while `STT_MODE=xai` or `TTS_MODE=xai` requires `XAI_API_KEY`. A live `ACTIVE_LLM` also requires a real `ACTIVE_MODEL` (not `dry-run-v1`).

### No Mic Device

Run:

```powershell
python main.py --list-devices
```

Then update:

```env
AUDIO_DEVICE_INDEX=<device_index_or_name>
```

### Bad Transcript

Keep `CONFIRM_TRANSCRIPT=true`. Use `r` to re-record or `e` to edit before the transcript reaches the LLM.

### AI Audio Does Not Play Live

Do not panic. The reliable artifact is the stem saved under the session output directory:

```text
audio\<session_id>\output\
```

Find the exact files in the session JSON `artifacts` block. Import the stems into Descript or your editor if live routing is not good enough. For live routing, set `PLAYBACK_MODE=sdk` and `OUTPUT_AUDIO_DEVICE=<your VB-Audio cable>`.

### Provider Call Fails Mid-Turn

The loop prompts `[Enter=retry] s=skip / q=quit` so you can retry the failed step without losing the session. The session JSON also records an `events` entry with the failed stage and error. If you quit, fix the key/network/config issue, then resume:

```powershell
python main.py <episode_name> --resume --confirm-transcript
```

## 11. Recommended First Real Session

Use a short pilot, not a full episode:

```powershell
python main.py pilot --doctor
python main.py --list-devices
$env:INPUT_MODE="mic"; $env:STT_MODE="deepgram"; $env:TTS_MODE="elevenlabs"; python main.py pilot --confirm-transcript --max-turns 1
$env:INPUT_MODE="mic"; $env:STT_MODE="deepgram"; $env:TTS_MODE="elevenlabs"; python main.py pilot --confirm-transcript
```

To pilot the xAI voice stack instead:

```powershell
$env:INPUT_MODE="mic"; $env:STT_MODE="xai"; $env:TTS_MODE="xai"; python main.py pilot --confirm-transcript --max-turns 1
$env:INPUT_MODE="mic"; $env:STT_MODE="xai"; $env:TTS_MODE="xai"; python main.py pilot --confirm-transcript
```

After the pilot, inspect:

```text
sessions\
audio\<session_id>\input\
audio\<session_id>\output\
exports\
```

Only move to longer recordings once this short loop feels boringly reliable.
