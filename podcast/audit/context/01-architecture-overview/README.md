# AI Podcast Backend — Architecture Overview

> Entry point for the technical audit context. This folder (`audit/context/`) documents
> every subsystem of the podcast backend; the sibling `audit/tickets/` folder holds the
> prioritized, batched fix plan derived from these docs.
>
> Audit date: **2026-06-09** · Platform under audit: **Windows 10 / Python 3.13.3** · Verdict source: [07-execution-report](../07-execution-report/README.md)

---

## 1. What this project is

A local backend for Florian's AI video podcast. A human host speaks (or types); an AI
co-host answers in voice. The output is a set of editing artifacts — per-turn audio stems,
a session transcript JSON, and Markdown exports — used downstream in OBS / Descript.

It is **not** a web service. It is a CLI (`python main.py <episode>`) plus an importable
Python library surface (`integrations/openclaw_tools.py`).

## 2. The three operating modes

The whole system is organized around `CONVERSATION_MODE`:

| Mode | Path | Providers | Purpose |
|------|------|-----------|---------|
| `dry-run` (default) | `run_episode` loop, text in / text out | none (offline) | Safe rehearsal, CI, no API keys |
| `chained` | `run_episode` loop: STT → LLM → TTS | Deepgram/xAI · Anthropic/OpenAI/Google · ElevenLabs/xAI | Fallback live path, turn-by-turn |
| `realtime` | `run_realtime_episode` async loop | OpenAI Realtime (speech-to-speech) | **Primary** live recording, barge-in |

`main.py` selects the path: `settings.uses_realtime` → realtime; otherwise the chained/dry-run
`run_episode` loop. Mode validity is enforced in `Settings.validate_audio_modes()`.

## 3. Component map

```
                       ┌─────────────────────────────────────────────┐
                       │  main.py  (argparse CLI, episode loop)        │
                       │  flags: --doctor --list-devices --resume      │
                       │         --session --max-turns --confirm-*     │
                       └───────────────┬───────────────┬──────────────┘
                                       │               │
                   uses_realtime=False │               │ uses_realtime=True
                                       ▼               ▼
        ┌──────────────────────────────────────┐   ┌──────────────────────────────┐
        │ run_episode()  (chained / dry-run)    │   │ run_realtime_episode()        │
        │  _capture_host_turn → STT             │   │  WebSocket to OpenAI Realtime │
        │  call_llm → speak(TTS)                │   │  mic PCM ⇄ AI PCM, barge-in   │
        └───────┬─────────┬─────────┬───────────┘   └──────────────┬───────────────┘
                │         │         │                              │
                ▼         ▼         ▼                              ▼
        ┌───────────┐ ┌────────┐ ┌────────┐              ┌──────────────────┐
        │ stt.py    │ │ llm.py │ │ tts.py │              │ realtime.py       │
        │ Deepgram  │ │ Anthr. │ │ 11Labs │              │ websockets +      │
        │ xAI / mic │ │ OpenAI │ │ xAI    │              │ sounddevice PCM   │
        │ sounddev. │ │ Google │ │ dryrun │              │ live_host/ai.wav  │
        └───────────┘ └────────┘ └────────┘              └──────────────────┘
                │         │         │                              │
                └─────────┴────┬────┴──────────────────────────────┘
                               ▼
                ┌──────────────────────────────┐     ┌───────────────────────────┐
                │ memory.py  ConversationMemory │◄────│ reliability.py            │
                │  sessions/<id>.json (atomic)  │     │  retry_call, structured_  │
                │  audio/<id>/input|output/     │     │  error, ProviderCallError │
                │  event log, resume, turn ids  │     └───────────────────────────┘
                └──────────────┬───────────────┘
                               │
        ┌──────────────────────┼───────────────────────────┐
        ▼                      ▼                            ▼
┌───────────────┐   ┌────────────────────┐    ┌────────────────────────────┐
│ preflight.py  │   │ config/settings.py │    │ integrations/openclaw_tools│
│ --doctor      │   │ Settings dataclass │    │ run_episode, export_        │
│ checks        │   │ env load+validate  │    │ transcript, session helpers │
└───────────────┘   └────────────────────┘    └────────────────────────────┘
        ▲                      ▲
        │                      │
   config/prompts/        .env / .env.example
   base_system.txt        (python-dotenv)
   episodes/<name>.txt
```

## 4. Subsystem documents

| # | Subsystem | Files | Doc |
|---|-----------|-------|-----|
| 02 | Core infrastructure — settings, session persistence, preflight, reliability, CLI | `config/settings.py`, `pipeline/memory.py`, `pipeline/preflight.py`, `pipeline/reliability.py`, `main.py` | [02-core-infra](../02-core-infra/README.md) |
| 03 | LLM pipeline — Anthropic / OpenAI (responses+chat) / Google / dry-run | `pipeline/llm.py` | [03-llm-pipeline](../03-llm-pipeline/README.md) |
| 04 | Audio I/O — mic capture, Deepgram/xAI STT, ElevenLabs/xAI TTS, playback | `pipeline/stt.py`, `pipeline/tts.py` | [04-audio-io](../04-audio-io/README.md) |
| 05 | Integrations & ops — OpenClaw surface, docs, deps, secrets, fresh-setup | `integrations/openclaw_tools.py`, `README.md`, `docs/`, `requirements.txt`, `.gitignore` | [05-integrations-ops](../05-integrations-ops/README.md) |
| 06 | Realtime — OpenAI speech-to-speech WebSocket, barge-in, PCM streaming | `pipeline/realtime.py` | [06-realtime](../06-realtime/README.md) |
| 07 | Execution report — empirical install/test/run results on this machine | (live run) | [07-execution-report](../07-execution-report/README.md) |

## 5. Data flow (chained turn, end to end)

1. `main.run_episode` reserves a `turn_id`, calls `_capture_host_turn`.
2. **Text mode:** `capture_text_turn(input_fn)` reads a line. **Mic mode:** `record_until_keypress`
   captures PCM via `sounddevice` until ENTER, writes `audio/<id>/input/turn_<n>.wav`, then
   `transcribe()` sends it to Deepgram (or xAI) and returns text; optional confirm/edit/re-record loop.
3. Host text is appended to `ConversationMemory` (`role=user`) and the session JSON is saved.
4. `call_llm(history, system_prompt, settings)` routes to the active provider, returns AI text.
5. `speak(text, turn_id, ...)` synthesizes via ElevenLabs/xAI → `audio/<id>/output/turn_<n>.mp3`
   (or a `.txt` stub in dry-run), best-effort playback per `PLAYBACK_MODE`.
6. Session JSON + event log saved after every step. `q`/`quit`/`end` stops the loop.

**Realtime turn:** mic PCM (24 kHz) is base64-streamed to OpenAI over WebSocket; server VAD
detects turn boundaries; AI audio deltas stream back and play through a PortAudio output stream;
host speech mid-AI triggers barge-in (playback flush). Host and AI PCM are written to
`live_host.wav` / `live_ai.wav` stems.

## 6. Configuration surface

All config is environment-driven (`.env` via `python-dotenv`, falling back to process env).
`Settings` is a frozen dataclass built in `load_settings()`; `validate_runtime()` enforces
provider-key presence and cross-field mode constraints. Key axes:

- **Provider selection:** `ACTIVE_LLM`, `ACTIVE_MODEL`, `STT_MODE`, `TTS_MODE`, `OPENAI_API_MODE`
- **Audio:** `INPUT_MODE`, `AUDIO_DEVICE_INDEX`, `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `PLAYBACK_MODE`, `OUTPUT_AUDIO_DEVICE`
- **Realtime:** `REALTIME_MODEL`, `REALTIME_VOICE`, `REALTIME_TRANSCRIPTION_MODEL`, `REALTIME_VAD_MODE`, `REALTIME_SAMPLE_RATE`
- **Reliability:** `PROVIDER_TIMEOUT_SECONDS`, `PROVIDER_MAX_RETRIES`, `CONFIRM_TRANSCRIPT`

See [02-core-infra](../02-core-infra/README.md#settings) for the full table and every validation rule.

## 7. External dependencies & system requirements

- **Python SDKs (runtime):** `anthropic`, `openai`, `google-genai`, `deepgram-sdk` (v7), `elevenlabs`, `websockets` (14–15), `requests`, `sounddevice`, `soundfile`, `numpy`, `python-dotenv`. `pytest` moved to `requirements-dev.txt`; `rich` removed (unused).
- **System:** PortAudio (bundled in the `sounddevice` wheel), libsndfile (bundled in `soundfile`); ffmpeg is **not** required (sdk playback uses sounddevice); a VB-Audio virtual cable is optional for live OBS routing.
- **Accounts/keys:** per active provider — set in `.env`.

Reproducibility: dependencies now carry upper bounds that allow the installed working majors, plus a committed `requirements.lock` (pip freeze) for exact pins (was `DEP-01`/`OPS-08`, fixed in Batch J).

## 8. State of the system (audit verdict)

> **Updated after Batches A–J (2026-06-10):** every P0/P1/P2/P3 finding is resolved or consciously
> deferred. Per-ticket resolution status: [`../../tickets/README.md`](../../tickets/README.md).

- ✅ **Install / imports / dry-run / resume / `--doctor` / `--list-devices` all work** on a clean Windows venv.
- ✅ **Test suite is 114/114 green.**
- ✅ **The committed `.env` is a clean dry-run**; `python main.py pilot` runs the dry-run loop (was `EXE-01` P0, fixed in Batch A).
- ✅ **The realtime live path connects** — the WebSocket URL now carries `?model=` (was `RT-01` P0, fixed in Batch B).
- ✅ **All P1 gaps closed:** model-id validation, Windows/sounddevice playback, system-default mic, session file-lock, `.gitignore` audio, doctor checks the real dirs + disk + output device, PowerShell-first docs, and the OpenClaw path-traversal is contained to `sessions/`.
- ⏸ **Consciously deferred** (pragmatic-scope decisions, not bugs): realtime auto-reconnect (RT-04 full), true mic back-pressure (RT-05), cancellable Windows stop thread (RT-07), event-journal for INF-07. The lighter forms (clean-stop, drop-logging, documented limitation) did land.

The prioritized, batched plan and per-ticket resolution are in [`audit/tickets/`](../../tickets/README.md).

## 9. How to use this audit

1. Read this overview, then the subsystem doc for the area you're touching.
2. Open [`audit/tickets/README.md`](../../tickets/README.md) for the issue index.
3. Follow [`audit/tickets/00-IMPLEMENTATION-PLAN.md`](../../tickets/00-IMPLEMENTATION-PLAN.md) —
   it groups the ~50 findings into ~10 batches ordered so each file is touched once and fixes
   don't collide.
