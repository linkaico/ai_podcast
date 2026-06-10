# P1 — High (works in the demo, fails in a real recording / security / onboarding)

16 issues. These don't crash the dry-run, but each one will bite during an actual live recording,
a fresh-machine setup, or exposes a security/data hole.

---

## LLM-01 — `ACTIVE_MODEL` placeholder `dry-run-v1` is sent to live providers; no model validation
- **Area:** llm · **Batch:** A · C · G · **Source:** [03-llm-pipeline](../context/03-llm-pipeline/README.md), [02-core-infra](../context/02-core-infra/README.md)
- **Location:** [pipeline/llm.py:105](../../pipeline/llm.py#L105), `:131`, `:167`; [config/settings.py:242](../../config/settings.py#L242) & `:156-172`; `.env.example:10`
- **Problem:** `ACTIVE_MODEL` defaults to `dry-run-v1` and that placeholder is the only example value
  in `.env.example`. It's passed verbatim as `model=` to every provider. `validate_for_active_provider`
  checks the API key but never the model id. So setting a real `ACTIVE_LLM` + key without also
  changing `ACTIVE_MODEL` sends `model="dry-run-v1"` → **404 NotFound** on the first real LLM call,
  mid-recording. This is the most likely "I set my key and it still doesn't work" failure.
- **Fix:** (A) Put real per-provider example ids in `.env.example` (e.g. `claude-opus-4-8` / a current
  OpenAI model / a current Gemini model). (A/settings) Reject `active_model` starting with `dry-run`
  for a live provider with a message naming a valid id. (G) Surface it as a doctor warning.

## LLM-02 — Chat mode uses `max_tokens`; reasoning models reject it
- **Area:** llm · **Batch:** C · **Source:** [03-llm-pipeline](../context/03-llm-pipeline/README.md)
- **Location:** [pipeline/llm.py:140](../../pipeline/llm.py#L140)
- **Problem:** `chat.completions.create(..., max_tokens=1024)` is legacy. o1/o3/o4/gpt-5-class models
  return **400 `unsupported_parameter`** and require `max_completion_tokens`. So `OPENAI_API_MODE=chat`
  silently works only for gpt-4o-era models and crashes on the reasoning models a user is likely to
  reach for. (The Responses path correctly uses `max_output_tokens`.)
- **Fix:** Send `max_completion_tokens=1024` for chat mode (accepted by current chat models, required
  by reasoning ones), or detect the param error and retry with the alternate key.

## AUD-01 — Default `AUDIO_DEVICE_INDEX=0` picks the wrong/failed Windows device
- **Area:** audio · **Batch:** A · D · **Source:** [04-audio-io](../context/04-audio-io/README.md)
- **Location:** [config/settings.py:72](../../config/settings.py#L72); `.env.example:30`; consumed at [pipeline/stt.py:205](../../pipeline/stt.py#L205) (used `:71-78`)
- **Problem:** `audio_device_index` defaults to `"0"`, parsed to integer index 0 and passed to
  `sd.InputStream(device=0)`. On Windows, PortAudio index 0 is frequently an MME/WDM **output**
  endpoint or a different host-API device than the user's mic, so the first real recording either
  raises `PortAudioError` (bad channel count / unsupported samplerate) or silently captures the wrong
  device. `_input_device` already maps `""`/`"default"` → `None` (true system default), but the
  default never reaches that branch.
- **Fix:** Default `audio_device_index` to `"default"` in both `Settings` and `.env.example`. Keep the
  integer/name override.

## AUD-02 — `PLAYBACK_MODE=system` never plays audio on Windows
- **Area:** audio · **Batch:** D · **Source:** [04-audio-io](../context/04-audio-io/README.md)
- **Location:** [pipeline/tts.py:264-268](../../pipeline/tts.py#L264-L268) (dispatched `:255-256`)
- **Problem:** `_system_play` only handles `sys.platform == "darwin"` via `afplay`; everywhere else
  (including the Windows target) it raises, and the exception is swallowed by the best-effort wrapper.
  An operator who sets `PLAYBACK_MODE=system` on Windows gets silent "playback skipped" every turn —
  no audio, no actionable message. `validate_audio_modes` accepts `system` unconditionally.
- **Fix:** Add a Windows branch — `os.startfile(str(output_path))` (default player, handles MP3) or
  `winsound`/PowerShell `SoundPlayer` for WAV — or reject `system` off-macOS in `validate_audio_modes`
  with "use PLAYBACK_MODE=sdk or file-only on Windows."

## AUD-03 — `PLAYBACK_MODE=sdk` silently requires ffmpeg
- **Area:** audio · **Batch:** D · **Source:** [04-audio-io](../context/04-audio-io/README.md)
- **Location:** [pipeline/tts.py:251-254](../../pipeline/tts.py#L251-L254)
- **Problem:** `_try_play_audio` calls `elevenlabs.play(audio_bytes)`, whose default `use_ffmpeg=True`
  shells out to `ffplay` and raises `ValueError("ffplay from ffmpeg not found …")` when ffmpeg isn't on
  PATH — which it usually isn't on Windows, and it's not a declared dependency. The error is swallowed,
  so the one playback mode that could work on Windows silently produces no sound.
- **Fix:** Call `play(audio_bytes, use_ffmpeg=False)` (falls back to the already-required
  `sounddevice`+`soundfile`). Verify `soundfile` MP3 decode support, or stream PCM. Otherwise document
  ffmpeg as required for `sdk` playback.

## INF-01 — Concurrent runs of the same episode clobber the session JSON (data loss)
- **Area:** infra · **Batch:** E · **Source:** [02-core-infra](../context/02-core-infra/README.md)
- **Location:** [pipeline/memory.py:199-212](../../pipeline/memory.py#L199-L212) (`_save`); `main.py:38-40`
- **Problem:** `_save()` rewrites the whole session via `tmp.write_text` + `os.replace` with **no lock**.
  Two processes that both `--resume` (or `--session <same file>`) each reload, mutate in memory, and
  atomically replace — last-writer-wins, silently destroying the other's recorded turns. For a
  recording tool, a double-launch during a live session is catastrophic data loss.
- **Fix:** Exclusive lock for the `ConversationMemory` lifetime (`msvcrt.locking` on Windows /
  `fcntl.flock` on POSIX, or `portalocker`). Refuse to start on lock failure with "session already
  open." Alternatively append-only event journaling. Design alongside **INF-07**.

## INF-02 — Blank `KEY=` env lines crash startup
- **Area:** infra · **Batch:** A · **Source:** [02-core-infra](../context/02-core-infra/README.md)
- **Location:** [config/settings.py:34-56](../../config/settings.py#L34-L56) (`_getenv_int`/`_float`/`_bool`)
- **Problem:** `os.getenv(name, default)` returns the default only when the var is **unset**. A bare
  `AUDIO_SAMPLE_RATE=` / `PROVIDER_MAX_RETRIES=` / `CONFIRM_TRANSCRIPT=` line reads as `""`, then
  `int("")`/`float("")`/bool-parse raises `SettingsError`. Operators routinely leave blank `KEY=` lines;
  this turns a benign blank into a hard startup crash. (Reproduced.)
- **Fix:** In `_getenv`, treat empty-after-strip as absent: `raw = os.getenv(name, "").strip(); return raw or default`. Keep raising only on a genuinely non-empty unparseable value.

## INF-03 — `.gitignore` misses `audio/<session_id>/`; recordings can be committed
- **Area:** infra · **Batch:** A · **Source:** [02-core-infra](../context/02-core-infra/README.md), [05-integrations-ops](../context/05-integrations-ops/README.md)
- **Location:** `.gitignore:9-17`; runtime paths [pipeline/memory.py:55-61](../../pipeline/memory.py#L55-L61)
- **Problem:** Ignore rules cover the legacy flat `audio/input/*`,`audio/output/*`, but recordings now
  write to `audio/<session_id>/input|output/…`. `git check-ignore` confirms
  `audio/<session_id>/input/live_host.wav` is **NOT ignored** — real, potentially private podcast audio
  is one `git add -A` from being committed.
- **Fix:** Replace with `audio/**` plus `!audio/.gitkeep` (or `audio/**/input/*`, `audio/**/output/*` +
  negations). Same `.gitignore` pass as **EXE-04**.

## INF-04 — `--doctor` write-tests the wrong (legacy) audio dirs
- **Area:** infra · **Batch:** G · **Source:** [02-core-infra](../context/02-core-infra/README.md)
- **Location:** [pipeline/preflight.py:16-17](../../pipeline/preflight.py#L16-L17); real dirs `memory.py:55-61`
- **Problem:** Preflight write-tests `settings.audio_input_dir`/`audio_output_dir` = `root/audio/input|output`,
  but a real recording writes to `root/audio/<session_id>/input|output`. So `--doctor` can report
  "audio_output: writable" while the directory the session actually uses is full/read-only/missing — a
  green doctor that doesn't prove recordability, defeating doctor's purpose.
- **Fix:** Build a throwaway `ConversationMemory` (or compute `root/audio/<sample_id>/…`) and write-test
  the real session-scoped dirs, or test `root/audio/` recursively. Unify the two as one source of truth.

## OPS-01 — `run_episode` blocks on stdin; not drivable by an OpenClaw agent
- **Area:** ops · **Batch:** H · **Source:** [05-integrations-ops](../context/05-integrations-ops/README.md)
- **Location:** [integrations/openclaw_tools.py:15-35](../../integrations/openclaw_tools.py#L15-L35) → `main.run_episode` (`main.py:22-31`)
- **Problem:** The wrapper calls `main.run_episode` without an `input_fn`, so it defaults to builtin
  `input()`. The loop then blocks on the interactive console (text or mic). An agent calling
  `run_episode(...)` in-process hangs with no injection path — yet the README lists it in the "Callable
  surface" alongside the six helpers that do work programmatically.
- **Fix:** Expose `input_fn`/`output_fn` (and a turn-injection callback) through the wrapper, or
  explicitly document `run_episode` as human/TTY-only and outside the automatable surface. Add a test
  driving it with a fake `input_fn`.

## OPS-02 — Path traversal: `load_session`/`export_transcript` read arbitrary files
- **Area:** ops (security) · **Batch:** H · **Source:** [05-integrations-ops](../context/05-integrations-ops/README.md)
- **Location:** [integrations/openclaw_tools.py:88-93](../../integrations/openclaw_tools.py#L88-L93), `:120-156`, `_resolve_session_path :165-170`
- **Problem:** `session_path` is uncontained. `_resolve_session_path` returns absolute paths unchanged
  and joins relative ones to root with no boundary check. Verified: `load_session("<abs path outside repo>.json")`
  reads and returns that file; `export_transcript` then writes a derived `.md` into `exports/`. For a
  surface framed as an external-agent API this is arbitrary local file read + info-disclosure.
  (The `episode_name` helpers are safe — `safe_episode_name` neutralizes traversal.)
- **Fix:** `resolve()` the candidate and verify `is_relative_to(sessions_dir.resolve())` before reading;
  reject otherwise. Apply to both functions. Add a negative test.

## OPS-03 — Docs use POSIX/macOS shell; commands fail in PowerShell
- **Area:** ops · **Batch:** I · **Source:** [05-integrations-ops](../context/05-integrations-ops/README.md)
- **Location:** `README.md:8,23,25,170,174`; `docs/AI_PODCAST_OPERATOR_GUIDE.md` (many: 18,20,179,207,213,219,327-333,348,360,412-420)
- **Problem:** The repo targets Windows/PowerShell, but docs use `source .venv/bin/activate`, `cp`,
  inline env-prefix (`CONVERSATION_MODE=realtime INPUT_MODE=mic python main.py pilot`), `touch`, a
  `python - <<'PY'` heredoc, and `env PYTHONPATH=/tmp/…`. None execute in PowerShell (inline `VAR=value`
  is a parse error; `cp`/`touch`/`source`/heredoc are undefined). A fresh Windows user cannot follow
  setup or any "live"/"rehearsal" one-liner as written.
- **Fix:** Add PowerShell equivalents: `.venv\Scripts\Activate.ps1`, `Copy-Item .env.example .env`,
  `$env:VAR="value"; python main.py …`, `New-Item` for prompt files, a real `.py` for the export
  snippet. Convert or add a Windows section/column.

## RT-02 — Blocking `output_stream.write()` runs on the event loop and stalls everything
- **Area:** realtime · **Batch:** B · **Source:** [06-realtime](../context/06-realtime/README.md)
- **Location:** [pipeline/realtime.py:94](../../pipeline/realtime.py#L94) (called from `_receive_events :287-289`)
- **Problem:** AI audio plays via the blocking PortAudio `RawOutputStream.write()` synchronously inside
  the receiver coroutine. When the output buffer is full (slow speaker, big delta burst), `write()`
  blocks the single event-loop thread — simultaneously freezing the mic sender, event reception,
  barge-in, and ENTER-stop. In a real conversation: stutter, delayed barge-in, mic back-pressure (feeds
  the queue-drop in RT-05).
- **Fix:** Decouple playback — push decoded PCM onto a playback queue consumed by a dedicated thread, or
  a PortAudio output **callback** pulling from a ring buffer; at minimum
  `await asyncio.to_thread(self.output_stream.write, audio)`. A callback-driven stream also makes
  barge-in flush cleaner.

## EXE-02 — Realtime auth/WS failure dumps a raw traceback
- **Area:** execution · **Batch:** B · **Source:** [07-execution-report](../context/07-execution-report/README.md)
- **Location:** [pipeline/realtime.py](../../pipeline/realtime.py) (try/finally ~240-275, no `except`); `main.py:233`
- **Problem:** A failed Realtime WebSocket (bad/expired key) raises
  `websockets.exceptions.ConnectionClosedError`, which `main()` doesn't catch (it handles only
  `FileNotFoundError`/`ValueError`/`RuntimeError`) and there's no `except` around the WS block. The user
  gets a full stack trace (`… invalid_api_key`) and exit 1 instead of a clean `Error: …`. (Reproduced
  with the placeholder key.)
- **Fix:** Catch connection/auth errors in `run_realtime_episode` (or broaden `main()` to include
  `websockets.exceptions.WebSocketException`/`OSError`) and surface
  `Error: realtime connection failed (check OPENAI_API_KEY): <reason>`.

## EXE-03 — CLI test reads the developer's real `.env` (non-hermetic)
- **Area:** execution · **Batch:** A · **Source:** [07-execution-report](../context/07-execution-report/README.md)
- **Location:** [tests/test_cli_dry_run.py:285-298](../../tests/test_cli_dry_run.py#L285-L298) (`test_main_passes_resume_and_session_flags`)
- **Problem:** The test monkeypatches `main.run_episode` but **not** `main.load_settings`, so
  `main(["pilot","--resume"])` loads the on-disk `.env`. The test's result depends on the machine's
  `.env` — red with the committed realtime one, green with dry-run. This coupling is exactly why a config
  file turns the suite red (see EXE-01).
- **Fix:** Make it deterministic — `monkeypatch.setattr("main.load_settings", lambda validate=True: Settings(root_dir=…, active_llm="dry-run", active_model="dry-run-v1"))` or set dry-run vars via
  `monkeypatch.setenv` — so it never reads the on-disk `.env`.

## EXE-04 — Committed `.venv` is stale, mixed-platform, and empty
- **Area:** execution · **Batch:** A · **Source:** [07-execution-report](../context/07-execution-report/README.md)
- **Location:** `.venv/` (`Scripts/python.exe` Windows + a 6-byte POSIX `bin/python` stub; only `pip` installed)
- **Problem:** The repo commits a `.venv` containing both a Windows `Scripts/` layout and a leftover
  POSIX `bin/` tree, with **no** project deps. Following the README to use the existing venv fails
  immediately (every project import `ImportError`s). Confusing and platform-broken.
- **Fix:** Add `.venv/` to `.gitignore` (same edit as INF-03) and remove it from the repo. Document
  `py -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt`.
