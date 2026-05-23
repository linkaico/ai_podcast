# AI Podcast Backend

This is the local foundation slice for Florian's AI video podcast backend. It creates a safe offline version of the STT -> LLM -> TTS loop so the project can be tested without microphones, OBS routing, Deepgram, ElevenLabs, or frontier-model API keys.

## What Works Now

- `python main.py pilot` starts a typed dry-run episode loop.
- Host turns are saved to `sessions/*.json` after every turn.
- Dry-run AI responses are generated locally through the LLM adapter.
- Dry-run voice artifacts are saved as `audio/output/dryrun_ai_turn_*.txt`.
- `INPUT_MODE=mic` records host WAV files and transcribes them with Deepgram or xAI.
- `TTS_MODE=elevenlabs` or `TTS_MODE=xai` generates MP3 stems for the AI voice.
- Base and per-episode prompts are loaded from `config/prompts/`.

For the full step-by-step recording workflow, use [docs/AI_PODCAST_OPERATOR_GUIDE.md](docs/AI_PODCAST_OPERATOR_GUIDE.md).

## Setup

```bash
cd podcast
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The default `.env.example` uses `ACTIVE_LLM=dry-run`, `INPUT_MODE=text`, and `TTS_MODE=dry-run`, which do not require API keys.

## Run A Dry-Run Episode

```bash
python main.py pilot
```

Type a host turn and press Enter. Type `q`, `quit`, or `end` to stop the episode.

Resume the latest saved session for an episode:

```bash
python main.py pilot --resume
```

Resume a specific session file:

```bash
python main.py pilot --session sessions/pilot_YYYYMMDD_HHMMSS.json
```

Run preflight checks before recording:

```bash
python main.py pilot --doctor
```

List available microphone input devices:

```bash
python main.py --list-devices
```

## Run With Microphone Input

Set these values in `.env`:

```env
INPUT_MODE=mic
STT_MODE=deepgram
DEEPGRAM_API_KEY=your_key_here
DEEPGRAM_MODEL=nova-3
AUDIO_DEVICE_INDEX=0
AUDIO_SAMPLE_RATE=16000
AUDIO_CHANNELS=1
```

Then run:

```bash
python main.py pilot
```

Each host turn records until you press Enter, saves a WAV file to `audio/input/host_turn_<n>.wav`, then sends it to Deepgram for transcription.

To use xAI instead of Deepgram for transcription:

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

```bash
python -m sounddevice
```

## Run With ElevenLabs Output

Set these values in `.env`:

```env
TTS_MODE=elevenlabs
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
ELEVENLABS_MODEL=eleven_multilingual_v3
ELEVENLABS_OUTPUT_FORMAT=mp3_22050_32
PLAYBACK_MODE=file-only
```

Each AI turn saves an MP3 stem to `audio/output/ai_turn_<n>.mp3`. Playback through the ElevenLabs SDK is best-effort; the saved MP3 is the guaranteed artifact.

## Run With xAI Voice Output

Set these values in `.env`:

```env
TTS_MODE=xai
XAI_API_KEY=your_key_here
XAI_TTS_VOICE=eve
XAI_TTS_LANGUAGE=en
PLAYBACK_MODE=file-only
```

Each AI turn saves an MP3 stem to `audio/output/ai_turn_<n>.mp3`, matching the ElevenLabs artifact path.

## Real Recording Checklist

1. Run `python main.py pilot --doctor`.
2. Run `python main.py --list-devices` and set `AUDIO_DEVICE_INDEX`.
3. Run a one-turn rehearsal:

```bash
INPUT_MODE=mic STT_MODE=xai TTS_MODE=xai XAI_API_KEY=<key> ACTIVE_LLM=<provider> python main.py pilot --confirm-transcript --max-turns 1
```

4. Start OBS and confirm Florian's mic and the AI voice track are both visible.
5. Run the real episode with `python main.py <episode_name> --confirm-transcript`.
6. After recording, use `sessions/*.json`, `audio/input/*.wav`, `audio/output/*.mp3`, and `exports/*.md` as the editing source of truth.

## OBS Routing Checklist

1. Install BlackHole 2ch on macOS or VB-Audio Cable on Windows/Linux.
2. In OBS, add one Audio Input Capture source for Florian's microphone.
3. Add a second Audio Input Capture source for BlackHole/VB-Audio.
4. Route system or SDK playback to the virtual cable when recording live.
5. Import `audio/output/ai_turn_<n>.mp3` stems into Descript or your editor if live routing is not clean enough.

## OpenClaw Integration

OpenClaw agents can call local Python helpers from `integrations/openclaw_tools.py`.

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

- `run_episode(name, resume=False, session_path=None, max_turns=None)`
- `write_episode_context(episode_name, content, sources=None)`
- `list_sessions(episode_name=None)`
- `load_session(session_path)`
- `latest_session(episode_name)`
- `episode_artifacts(episode_name)`
- `export_transcript(session_path, format="markdown")`

Episode context files are written to `config/prompts/episodes/<episode_name>.txt` and are automatically appended by `load_system_prompt()` the next time that episode runs.

Markdown transcript exports are written to `exports/*.md` for post-production review and clip selection.

## Episode Prompts

- Base persona: `config/prompts/base_system.txt`
- Episode-specific context: `config/prompts/episodes/<episode_name>.txt`

If `python main.py pilot` is running, the backend automatically appends `config/prompts/episodes/pilot.txt` when that file exists.

## Tests

```bash
python -m pytest
```

## Deferred To The Next Slice

- Production-grade provider retries, timeouts, and structured logging
- Exact cross-platform OBS device targeting from code
- Network/service-level OpenClaw registration
