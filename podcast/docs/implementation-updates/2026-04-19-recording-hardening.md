# Recording Hardening Update - 2026-04-19

## Implemented

- Added preflight/doctor checks for settings, prompts, writable runtime directories, SDK imports, and optional audio-device lookup.
- Added `python main.py --list-devices`.
- Added provider retry handling through `pipeline/reliability.py`.
- Added additive session events so failed or completed stages are visible in session JSON.
- Added mic transcript confirmation with accept, re-record, edit, skip, and quit choices.
- Added OpenAI Responses API mode with Chat Completions fallback.
- Updated ElevenLabs generation to save from one generated audio response, with optional playback modes.
- Added recording-focused settings for provider retries/timeouts, transcript confirmation, OpenAI API mode, playback mode, ElevenLabs output format, and voice settings.

## How To Run

Preflight:

```bash
cd podcast
INPUT_MODE=text TTS_MODE=dry-run python main.py pilot --doctor
```

List devices:

```bash
python main.py --list-devices
```

One-turn rehearsal:

```bash
INPUT_MODE=mic TTS_MODE=elevenlabs ACTIVE_LLM=<provider> python main.py pilot --confirm-transcript --max-turns 1
```

## New Settings

- `CONFIRM_TRANSCRIPT=true`
- `PROVIDER_TIMEOUT_SECONDS=60`
- `PROVIDER_MAX_RETRIES=1`
- `OPENAI_API_MODE=responses`
- `PLAYBACK_MODE=file-only`
- `ELEVENLABS_OUTPUT_FORMAT=mp3_22050_32`
- `ELEVENLABS_STABILITY=0.45`
- `ELEVENLABS_SIMILARITY_BOOST=0.80`
- `ELEVENLABS_STYLE=0.35`
- `ELEVENLABS_SPEED=1.0`

## Notes For Future Work

- Playback device targeting remains best-effort; saved MP3 stems are still the reliable artifact.
- The retry helper records provider failures clearly, but deeper provider-specific retry classification can be added later.
- Clipping and publishing automation remain intentionally out of scope for this slice.
