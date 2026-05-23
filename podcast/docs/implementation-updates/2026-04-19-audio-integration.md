# Audio Integration Update - 2026-04-19

## Implemented

- Added audio pipeline settings for input mode, TTS mode, Deepgram model, ElevenLabs model, sample rate, and channel count.
- Split audio modes from LLM provider selection, so `ACTIVE_LLM` only controls the model provider.
- Added microphone recording in `pipeline/stt.py` using `sounddevice`, `soundfile`, and `numpy`.
- Added Deepgram prerecorded transcription behind `INPUT_MODE=mic`.
- Added ElevenLabs MP3 generation behind `TTS_MODE=elevenlabs`.
- Updated `main.py` to run typed text mode or mic transcription mode per settings.
- Updated `.env.example` and `README.md` with dry-run, microphone, ElevenLabs, and OBS setup notes.

## How To Run

Dry-run:

```bash
cd podcast
INPUT_MODE=text TTS_MODE=dry-run python main.py pilot
```

Microphone plus Deepgram:

```bash
INPUT_MODE=mic DEEPGRAM_API_KEY=... python main.py pilot
```

ElevenLabs voice output:

```bash
TTS_MODE=elevenlabs ELEVENLABS_API_KEY=... ELEVENLABS_VOICE_ID=... python main.py pilot
```

## Intentionally Deferred

- Production retries, backoff, and provider timeout policy.
- Cross-platform audio output device control from Python.
- Session resume and episode replay tools.
- OpenClaw callable wrappers and agent-written episode context.

## Notes For Future Work

- Keep `INPUT_MODE=text` and `TTS_MODE=dry-run` as the default automated test path.
- Treat saved `audio/input/*.wav` and `audio/output/*.mp3` files as the reliable post-production artifacts.
- OBS live routing should stay manual until the target machine and virtual audio driver are known.
