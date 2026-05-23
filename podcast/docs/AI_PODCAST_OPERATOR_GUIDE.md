# AI Podcast Operator Guide

This guide is the practical checklist for a real recorded AI podcast session. The primary path is OpenAI Realtime native speech-to-speech with interruption support; the chained Deepgram/xAI -> LLM -> ElevenLabs/xAI path remains available as fallback.

Clipping and social distribution are intentionally out of scope here.

## 1. What Still Needs To Be Done

Before the first real recording, complete these one-time setup tasks.

### Local Python Setup

From this workspace:

```bash
cd podcast
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### API Keys

Open `podcast/.env` and fill in the services you plan to use:

```env
DEEPGRAM_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
XAI_API_KEY=...
```

Choose one real LLM provider:

```env
ACTIVE_LLM=anthropic
ACTIVE_MODEL=<your_anthropic_model>
ANTHROPIC_API_KEY=...
```

Or:

```env
ACTIVE_LLM=openai
ACTIVE_MODEL=<your_openai_model>
OPENAI_API_KEY=...
OPENAI_API_MODE=responses
```

Or:

```env
ACTIVE_LLM=google
ACTIVE_MODEL=<your_google_model>
GOOGLE_API_KEY=...
```

Keep `.env` private. It is gitignored and should never be shared.

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

```bash
python main.py --list-devices
```

Set the selected device in `.env`:

```env
AUDIO_DEVICE_INDEX=0
AUDIO_SAMPLE_RATE=16000
AUDIO_CHANNELS=1
```

Use the device index that corresponds to your real microphone.

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

### OBS Audio Setup

1. Install BlackHole 2ch on macOS or VB-Audio Cable on Windows/Linux.
2. In OBS, add Florian's microphone as one audio source.
3. Add the virtual cable as a second audio source if you want live AI playback recorded separately.
4. Keep saved stems in `audio/<session_id>/output/` as the reliable fallback even if live routing fails.
5. Run a rehearsal and confirm OBS meters move for the intended sources.

## 2. Preflight Before Every Recording

Run:

```bash
python main.py <episode_name> --doctor
```

Example:

```bash
python main.py pilot --doctor
```

Do not record until the result says:

```text
Result: OK
```

The doctor checks settings, prompt files, writable runtime folders, required SDKs, and audio-device availability when mic mode is enabled.

## 3. Prepare The Episode Prompt

The base AI co-host persona lives here:

```text
config/prompts/base_system.txt
```

Episode-specific context goes here:

```text
config/prompts/episodes/<episode_name>.txt
```

Example:

```bash
touch config/prompts/episodes/pilot.txt
```

Use the episode file for research notes, topic framing, guest/background context, and things the AI should know before the recording.

OpenClaw-style agents can also write this file through:

```python
from integrations.openclaw_tools import write_episode_context

write_episode_context(
    "pilot",
    "Episode research context goes here.",
    sources=[{"title": "Reference", "url": "https://example.com"}],
)
```

## 4. Rehearsal Flow

Always run a one-turn rehearsal before the real recording:

```bash
python main.py <episode_name> --confirm-transcript --max-turns 1
```

For the primary realtime rehearsal:

```bash
CONVERSATION_MODE=realtime INPUT_MODE=mic OPENAI_API_KEY=<key> python main.py pilot
```

For a chained fallback rehearsal using mic, Deepgram, LLM, and ElevenLabs:

```bash
CONVERSATION_MODE=chained INPUT_MODE=mic STT_MODE=deepgram TTS_MODE=elevenlabs python main.py pilot --confirm-transcript --max-turns 1
```

For a real rehearsal using xAI STT and xAI TTS:

```bash
CONVERSATION_MODE=chained INPUT_MODE=mic STT_MODE=xai TTS_MODE=xai XAI_API_KEY=<key> python main.py pilot --confirm-transcript --max-turns 1
```

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
audio/<session_id>/output/turn_000000.mp3
```

If the rehearsal is bad, tune `.env`, device settings, prompt context, or voice-provider settings before recording.

## 5. Real Recording Flow

Start OBS first. Confirm framing and audio meters.

For realtime recording, run:

```bash
CONVERSATION_MODE=realtime INPUT_MODE=mic python main.py <episode_name>
```

Example:

```bash
CONVERSATION_MODE=realtime INPUT_MODE=mic python main.py pilot
```

Recommended live rhythm:

1. Speak naturally while the realtime session is active.
2. Interrupt the AI naturally if you want to respond; playback is flushed on detected speech.
3. Press Enter once to end the session.
4. Inspect the saved session-local host and AI stems before editing.

The system saves progress after each turn, so if something fails, you should still have a usable partial session.

## 6. Resume If Something Breaks

Resume the latest session for an episode:

```bash
python main.py <episode_name> --resume --confirm-transcript
```

Resume a specific session:

```bash
python main.py <episode_name> --session sessions/<session_file>.json --confirm-transcript
```

The resumed session continues writing to the same JSON file and continues turn numbering so new artifacts do not overwrite previous turns.

## 7. Where Outputs Land

Session JSON:

```text
sessions/<episode_name>_YYYYMMDD_HHMMSS.json
```

Host and AI realtime WAV stems:

```text
audio/<session_id>/input/live_host.wav
audio/<session_id>/output/live_ai.wav
```

Chained fallback stems:

```text
audio/<session_id>/input/turn_<n>.wav
audio/<session_id>/output/turn_<n>.mp3
```

Dry-run AI text outputs:

```text
audio/<session_id>/output/turn_<n>.txt
```

Markdown transcript exports:

```text
exports/*.md
```

Session JSON includes:

- `history`: conversation turns.
- `events`: stage events such as recording, transcription, LLM, TTS, failure, and completion.
- `artifacts`: discovered audio/text files.

## 8. Export A Transcript

From inside `podcast/`:

```bash
python - <<'PY'
from integrations.openclaw_tools import latest_session, export_transcript

latest = latest_session("pilot")
print(export_transcript(latest["path"]))
PY
```

The Markdown export appears in:

```text
exports/
```

Use this for editing notes and post-production review.

## 9. Dry-Run Mode For Testing

If you want to test without APIs:

```bash
INPUT_MODE=text TTS_MODE=dry-run ACTIVE_LLM=dry-run python main.py pilot
```

This is useful for checking session saving, prompt loading, resume behavior, and transcript exports.

## 10. Troubleshooting

### `python -m pytest` segfaults

On the current local Anaconda Python, pytest can segfault before test collection due to a `readline` issue. The project tests have been verified with:

```bash
env PYTHONPATH=/tmp/pytest_no_readline PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -vv
```

This is an environment issue, not a podcast backend failure.

### Doctor Fails On Missing Key

Check `.env` and confirm the matching mode is enabled. For example, `INPUT_MODE=mic STT_MODE=deepgram` requires `DEEPGRAM_API_KEY`, while `STT_MODE=xai` or `TTS_MODE=xai` requires `XAI_API_KEY`.

### No Mic Device

Run:

```bash
python main.py --list-devices
```

Then update:

```env
AUDIO_DEVICE_INDEX=<device_index>
```

### Bad Transcript

Keep `CONFIRM_TRANSCRIPT=true`. Use `r` to re-record or `e` to edit before the transcript reaches the LLM.

### AI Audio Does Not Play Live

Do not panic. The reliable artifact is the MP3 saved to:

```text
audio/output/
```

Import the MP3 stems into Descript or your editor if live routing is not good enough.

### Provider Call Fails Mid-Turn

The session JSON should contain an `events` entry with the failed stage and error. Fix the key/network/config issue, then resume:

```bash
python main.py <episode_name> --resume --confirm-transcript
```

## 11. Recommended First Real Session

Use a short pilot, not a full episode:

```bash
python main.py pilot --doctor
python main.py --list-devices
INPUT_MODE=mic STT_MODE=deepgram TTS_MODE=elevenlabs python main.py pilot --confirm-transcript --max-turns 1
INPUT_MODE=mic STT_MODE=deepgram TTS_MODE=elevenlabs python main.py pilot --confirm-transcript
```

To pilot the xAI voice stack instead:

```bash
INPUT_MODE=mic STT_MODE=xai TTS_MODE=xai XAI_API_KEY=<key> python main.py pilot --confirm-transcript --max-turns 1
INPUT_MODE=mic STT_MODE=xai TTS_MODE=xai XAI_API_KEY=<key> python main.py pilot --confirm-transcript
```

After the pilot, inspect:

```text
sessions/
audio/input/
audio/output/
exports/
```

Only move to longer recordings once this short loop feels boringly reliable.
