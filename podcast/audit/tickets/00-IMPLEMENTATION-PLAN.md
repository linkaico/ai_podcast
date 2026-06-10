# Implementation Plan — Batched Fix Strategy

The 57 findings are grouped into **10 batches (A–J)**. Batches are organized so that **each
source file is edited in a single focused pass** — you open `memory.py` once, fix all seven of
its issues, run its tests, and never return. Batches are ordered by dependency and value.

> Goal: land the maximum number of fixes per editing pass with zero rework. Within a batch,
> the fixes share a file, a mental model, and a test harness.

## Dependency graph (what must precede what)

```
A (repo & config hygiene)  ── unblocks ──►  everything (green CI + runnable + real model ids)
        │
        ├─► C (llm)  ──┐
        │              ├─► G (doctor consumes model validation + audio-dir truth)
        ├─► D (audio) ─┤
        │              │
        ├─► E (memory) ┘  (E settles the audio-dir source of truth that G checks)
        │
        ├─► F (cli + reliability)  ──► coordinates timeout with C (LLM-03/06)
        │
        └─► B (realtime)   (independent of C/D/E; highest user value)

H (openclaw) — independent, after A.
I (docs)     — LAST: documents the final behavior from B/D/H.
J (deps)     — anytime after A; cheap, isolated.
```

**Recommended order:** **A → B → (C, D, E, F, G, H in parallel-ish) → J → I.**
Do **A** first (one short pass makes the repo green, installable, and safe to commit). Do **B**
next — it fixes the primary recording path. The middle batches touch disjoint files and can be
done in any order or by different people. Do **I** (docs) last so it reflects reality.

---

## BATCH A — Repo & config hygiene  🔴 gate to green
**Why first:** until this lands, the project crashes on its own documented command and CI is red.
All edits are config/manifest files with near-zero logic risk.

**Files:** `.env`, `.env.example`, `.gitignore`, `config/settings.py`, `tests/test_cli_dry_run.py`, `config/prompts/base_system.txt`

| Ticket | Pri | What |
|--------|-----|------|
| EXE-01 | P0 | De-duplicate `.env` to a single dry-run block (or stop committing it). |
| EXE-03 | P1 | Make the CLI test inject dry-run settings instead of reading the on-disk `.env`. Fixes the same red test EXE-01 causes — do them together. |
| EXE-04 | P1 | Remove `.venv/` from the repo and add it to `.gitignore`. |
| INF-03 | P1 | Replace `audio/input/*`,`audio/output/*` with `audio/**` (keep `.gitkeep`) so session-scoped recordings can't be committed. Same `.gitignore` edit as EXE-04. |
| INF-02 | P1 | In `_getenv`, treat empty-after-strip as absent so blank `KEY=` lines fall back to defaults. |
| LLM-01 | P1 | Put real per-provider example model ids in `.env.example`; add `validate_for_active_provider` rejection of `dry-run-v1` (and obviously-wrong ids) for live providers. (Doctor + llm sides land in G/C.) |
| INF-16 | P3 | Replace `[PODCAST NAME TBD]` in `base_system.txt`. Trivial, same hygiene pass. |

**Exit check:** `pytest -q` is 73/73 from a clean checkout; `python main.py pilot` runs the dry-run loop; `git status` shows no `.venv`/audio/secret churn.

---

## BATCH B — Realtime live path  🔴 primary recording
**Why second:** this is the headline feature and currently cannot connect. One deep pass through
`realtime.py` plus its `main.py` dispatch; build a fake-socket test harness once and cover it all.

**Files:** `pipeline/realtime.py`, `main.py` (realtime dispatch + error handling), `tests/test_realtime.py`

| Ticket | Pri | What |
|--------|-----|------|
| RT-01 | P0 | Append `?model={settings.realtime_model}` to the WebSocket URL. One line; unblocks the whole path. |
| EXE-02 | P1 | Catch `websockets` / auth errors and surface `Error: realtime connection failed (check OPENAI_API_KEY): …`; broaden `main()`'s handler. |
| RT-02 | P1 | Move blocking PortAudio playback off the event loop (`asyncio.to_thread` or a callback-fed ring buffer). |
| RT-03 | P2 | Stop sending `input_audio_buffer.commit` in VAD modes. |
| RT-04 | P2 | Catch `ConnectionClosed` in the receive loop; clean stop (+ optional reconnect). |
| RT-05 | P2 | Count/log dropped mic frames; raise the queue bound / apply back-pressure. |
| INF-12 | P2 | Forward & honor `--max-turns` in realtime, or reject it with a clear message; note `--confirm-transcript` is N/A. |
| RT-07 | P3 | Windows-friendly stop affordance (daemon stdin thread → `asyncio.Event`); handle Ctrl-C through the `finally`. |
| RT-08 | P3 | Make the realtime model id overridable + verified (the verify part lands in G). |
| RT-06 | P2 | Tests: fake connector asserting the URL has `?model=` and the auth header; scripted event sequence; stop/commit, error-event, `ConnectionClosed`, WAV temp→publish. (Would have caught RT-01.) |

**Exit check:** with a real key, a realtime episode connects, plays AI audio without stuttering, barge-in works, Ctrl-C/ENTER stops cleanly and publishes both WAV stems; new tests green.

---

## BATCH C — LLM provider correctness
**Files:** `pipeline/llm.py`, `tests/test_llm.py` (model-validation coordinates with A; timeout coordinates with F)

| Ticket | Pri | What |
|--------|-----|------|
| LLM-01 | P1 | Per-provider default model ids + the validation hook (settings side from A; ensure llm passes a real id). |
| LLM-02 | P1 | Chat path: `max_completion_tokens` instead of `max_tokens`. |
| LLM-03 | P2 | Give the Google client a request timeout (or rely on F's enforced `retry_call` deadline). |
| LLM-04 | P2 | Make the output cap a setting; detect truncation (`stop_reason`/`finish_reason`) and at least flag it. |
| LLM-05 | P2 | Inspect stop/finish reason; raise distinct "refused"/"safety-blocked" vs "empty" errors; guard Google `.text`. |
| LLM-06 | P2 | One retry layer (disable SDK retries or drop the wrapper); preserve the original exception in `ProviderCallError`. |
| LLM-07 | P3 | Anthropic `cache_control` on the stable system prefix. |
| LLM-08 | P3 | Tests: Anthropic path, empty/blocked→error, retry-then-`ProviderCallError`, responses `output[]` fallback, missing-key validation. |

**Exit check:** switching `ACTIVE_LLM` with a real key and model id produces a reply on all three providers; a refusal and a truncation are reported distinctly; tests cover the failure branches.

---

## BATCH D — Audio I/O & Windows playback
**Files:** `pipeline/stt.py`, `pipeline/tts.py`, `config/settings.py` (audio defaults coordinate with A), `tests/test_stt.py`, `tests/test_tts.py`

| Ticket | Pri | What |
|--------|-----|------|
| AUD-01 | P1 | Default `AUDIO_DEVICE_INDEX` → `default` (system default mic) in settings + `.env.example`. |
| AUD-02 | P1 | Implement Windows branch in `_system_play` (`os.startfile`) or reject `system` off-macOS with a clear message. |
| AUD-03 | P1 | `elevenlabs.play(audio, use_ffmpeg=False)` so `sdk` mode works without ffmpeg. |
| AUD-04 | P2 | Cap recording duration / free the chunk buffer after concat. |
| AUD-05 | P2 | Simplify the Deepgram default path to `listen.v1.media.transcribe_file`; drop/guard the optionless legacy branches. |
| AUD-06 | P2 | Wire `OUTPUT_AUDIO_DEVICE` into playback, or remove it from settings + `.env.example`. |
| AUD-08 | P3 | Pass `multichannel=True` when `AUDIO_CHANNELS>1`, or document mono-only. |
| AUD-09 | P3 | Tests: `_input_device` parsing, real int16 WAV round-trip, `_coerce_audio_bytes` generator, unsupported-format raise, per-platform `_system_play`. |

**Exit check:** on Windows, `PLAYBACK_MODE=sdk` and `=system` both produce sound or a clear message; default mic captures from the real default device; a long recording doesn't balloon memory.

---

## BATCH E — Session persistence robustness (data-loss prevention)
**Files:** `pipeline/memory.py`, `tests/test_memory.py`. Design INF-01 (lock) and INF-07 (save batching) together — they interact.

| Ticket | Pri | What |
|--------|-----|------|
| INF-01 | P1 | Exclusive lock per session (msvcrt/fcntl or `portalocker`); refuse a second opener. |
| INF-05 | P2 | `json.dumps(..., ensure_ascii=False, default=str)`. |
| INF-06 | P2 | `update_turn_metadata` becomes a no-op when the turn was trimmed. |
| INF-07 | P2 | Batch saves to turn boundaries and/or append events to a `.events.jsonl` instead of full rewrites. |
| INF-08 | P2 | Pick "latest" by `st_mtime` / stored epoch, not filename sort. |
| INF-14 | P3 | Sweep stale `.tmp` files on init (or write temps to a gitignored `tmp/`). |
| INF-18 | P3 | Prefer the embedded `session_id`; warn if it differs from the filename stem. |

**Exit check:** a double-launch is refused; a 2-hour session doesn't do quadratic I/O; unicode transcripts are human-readable; resume picks the truly newest session.

---

## BATCH F — CLI resilience & reliability wrapper
**Files:** `main.py` (episode loop), `pipeline/reliability.py`. INF-09 here also satisfies LLM-03 (a hung Google call gets a real deadline) — coordinate with C.

| Ticket | Pri | What |
|--------|-----|------|
| INF-11 | P2 | On host-capture/TTS errors, offer retry/skip instead of unconditionally `break`ing the episode. |
| INF-09 | P2 | Enforce a real wall-clock deadline in `retry_call` (worker thread + `future.result(timeout)`); add backoff jitter. |
| INF-13 | P3 | Reserve the turn id only after the turn is known real (move it below the skip/empty/exit checks). |
| INF-15 | P3 | Catch `KeyboardInterrupt` → "Session saved — stopping."; ensure the in-flight turn is complete or rolled back. |

**Exit check:** a single transient blip no longer ends a live recording; Ctrl-C exits cleanly with a coherent session; turn numbering has no gaps.

---

## BATCH G — Preflight / doctor hardening
**Files:** `pipeline/preflight.py`, `tests/test_preflight.py`. Consumes A's model validation, RT-08, and the audio-dir source-of-truth settled in E/INF-04.

| Ticket | Pri | What |
|--------|-----|------|
| INF-04 | P1 | Write-test the real session-scoped audio dirs (build a throwaway `ConversationMemory` or test `audio/` recursively). |
| INF-10 | P2 | Add cheap per-provider auth ping (warn, not fail, if offline), `shutil.disk_usage` free-space check, realtime output-device check. |
| LLM-01 | P1 | Doctor warning when `ACTIVE_MODEL` is the placeholder / implausible for the provider. |
| RT-08 | P3 | Doctor check that the configured realtime model is available on the key. |

**Exit check:** a green `--doctor` actually predicts a successful recording — it fails/warns on a bad key, full disk, missing output device, or placeholder model.

---

## BATCH H — OpenClaw surface & security
**Files:** `integrations/openclaw_tools.py`, `pipeline/llm.py` (`load_system_prompt` sanitize), `tests/test_openclaw_tools.py`

| Ticket | Pri | What |
|--------|-----|------|
| OPS-02 | P1 | Contain `session_path` to the sessions dir (`resolve().is_relative_to`) in `load_session` + `export_transcript`. Security. |
| OPS-01 | P1 | Expose `input_fn`/`output_fn` (and a turn-injection hook) through the `run_episode` wrapper, or document it as TTY-only and outside the automatable surface. |
| OPS-11 | P3 | Sanitize `episode_name` inside `load_system_prompt` so reads match `write_episode_context` writes. |
| OPS-12 | P3 | Tests: `run_episode` with injected `input_fn`, the `ValueError`/`FileNotFoundError` paths, a path-containment negative test. |

**Exit check:** an out-of-tree `load_session(...)` is refused; an agent can drive an episode non-interactively (or the docs say it can't); read/write prompt paths always agree.

---

## BATCH I — Docs & onboarding (Windows-first)  — do LAST
**Files:** `README.md`, `docs/AI_PODCAST_OPERATOR_GUIDE.md`, new `LICENSE`. Reflect the final behavior from B/D/H.

| Ticket | Pri | What |
|--------|-----|------|
| OPS-03 | P1 | PowerShell equivalents for every command (`.venv\Scripts\Activate.ps1`, `Copy-Item`, `$env:VAR="..."; python …`, `New-Item`, a real `.py` for the export snippet). |
| OPS-05 | P2 | Fix the operator guide's legacy flat audio paths → `audio/<session_id>/…`. |
| OPS-06 | P2 | Add a "System prerequisites" section (PortAudio bundled; ffmpeg only for `sdk`; VB-Audio cable for OBS). |
| OPS-04 | P2 | Reword OpenClaw as an in-process library, no remote endpoint. |
| OPS-09 | P3 | Add a LICENSE (or an explicit "proprietary" note). |

**Exit check:** a fresh Windows user can copy-paste their way from clone to a recorded dry-run with no POSIX-ism failing.

---

## BATCH J — Dependency hygiene
**Files:** `requirements.txt`, new `requirements-dev.txt` / lockfile. Independent; cheap.

| Ticket | Pri | What |
|--------|-----|------|
| DEP-01 | P2 | Cap volatile SDKs (`anthropic<1`, `openai<3`, `google-genai`, `numpy<3`, `pydantic<3`) and commit a lockfile (`pip freeze`/pip-tools/uv). |
| OPS-08 | P3 | Remove `rich` (unused); move `pytest` to `requirements-dev.txt`. |
| EXE-06 | P3 | `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at startup so device names print correctly. (Tiny `main.py` edit — fold in here or in F.) |

**Exit check:** a fresh install is reproducible; `pip install -r requirements.txt` pulls no surprise majors; `--list-devices` prints umlauts correctly.

---

## Effort & value snapshot

| Batch | Tickets | Blast radius | Value | Suggested owner-pass |
|-------|---------|--------------|-------|----------------------|
| A | 7 | tiny (config/manifests) | unblocks all | quick, do first |
| B | 10 | one module + dispatch | **highest** (primary path) | focused session |
| C | 8 | one module | high (correctness) | focused session |
| D | 8 | two modules | high (Windows audio) | focused session |
| E | 7 | one module | high (no data loss) | design lock+save together |
| F | 4 | two small files | medium (resilience) | quick |
| G | 4 | one file | medium (trustworthy doctor) | after A/C/E |
| H | 4 | one module + 1 fn | medium (security) | quick |
| I | 5 | docs only | onboarding | last |
| J | 3 | manifest | reproducibility | anytime |

**Fastest path to "safe to record a real episode":** A → B → (AUD-01/02/03 from D) → INF-01 from E → INF-04 from G. Everything else hardens the edges.
