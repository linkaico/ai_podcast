# Backend Foundation Update - 2026-04-19

## Implemented

- Created the local `podcast/` backend project structure with pipeline modules, config, prompts, runtime directories, tests, and docs.
- Added `config/settings.py` for `.env` loading, provider selection, path helpers, and required-key validation for real LLM providers.
- Added `pipeline/memory.py` to persist timestamped conversation turns to `sessions/*.json` after every turn.
- Added `pipeline/llm.py` with prompt loading, a deterministic dry-run provider, and scaffolded Anthropic/OpenAI/Google provider branches.
- Added dry-run STT/TTS interfaces so the project can run without audio devices or paid APIs.
- Added `main.py`, a typed episode loop that saves session JSON and dry-run voice artifacts.
- Added pytest coverage for settings, prompt loading, memory persistence/trimming, dry-run LLM calls, and the CLI episode loop.

## How To Run

```bash
cd podcast
python -m pytest
python main.py pilot
```

The default provider is `ACTIVE_LLM=dry-run`, so no API keys are required for the first boot.

## Intentionally Deferred

- Real microphone capture and local audio-device selection.
- Deepgram transcription implementation.
- ElevenLabs audio streaming, MP3 stem generation, and OBS routing.
- Resuming an existing session file.
- OpenClaw-callable wrappers and research-agent episode context generation.

## Recommended Next Slice

Implement the real audio layer behind the existing `pipeline/stt.py` and `pipeline/tts.py` interfaces. Keep typed dry-run mode as the default test path, then add opt-in Deepgram and ElevenLabs integration with clear provider validation, small integration tests using mocks, and a manual OBS routing checklist.
