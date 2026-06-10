# AI Podcast Backend — Audit Tickets

Prioritized, batched register of every issue found in the 2026-06-09 full audit.
Derived from the subsystem docs in [`../context/`](../context/01-architecture-overview/README.md).

- **Issues found:** 57 unique (after de-duplication across 6 audit agents)
- **By priority:** 🔴 2 × P0 · 🟠 16 × P1 · 🟡 23 × P2 · ⚪ 16 × P3
- **✅ Resolution status (updated 2026-06-10):** all 57 implemented across Batches **A–J** (suite **114 green**), **except four consciously deferred** — RT-04 (realtime auto-reconnect), RT-05 (true mic back-pressure), RT-07 (cancellable Windows stop thread), and the event-journal alternative for INF-07 (reduce-frequency chosen instead). The lighter forms of the deferred realtime items did land (clean-stop, drop-logging, documented limitation). Each ticket's "Batch" column below is the batch that fixed it.
- **Original empirical state (2026-06-09, historical):** *as shipped* `python main.py pilot` crashed and 1 test failed (committed `.env`), and the realtime path couldn't connect (`?model=` missing) — both fixed in A/B.

## Files in this folder

| File | Purpose |
|------|---------|
| [00-IMPLEMENTATION-PLAN.md](00-IMPLEMENTATION-PLAN.md) | **Start here to fix.** Groups all 57 tickets into 10 batches (A–J) ordered so each source file is touched once and fixes don't collide. The "do a lot at once without back-and-forth" map. |
| [P0-critical.md](P0-critical.md) | 2 blockers — broken right now. |
| [P1-high.md](P1-high.md) | 16 — will fail during a real recording / important gaps & one security hole. |
| [P2-medium.md](P2-medium.md) | 23 — robustness, correctness-under-stress, quality. |
| [P3-low.md](P3-low.md) | 16 — minor, cosmetic, nice-to-have, test depth. |

## Severity definitions

- **P0** — Broken in normal use right now (crash / cannot connect / red CI).
- **P1** — Works in the happy demo but will fail in a real recording, or is a security/data-loss/onboarding blocker.
- **P2** — Survives the happy path but breaks under stress (long sessions, network blips, refusals, concurrency) or is a meaningful correctness/quality gap.
- **P3** — Minor, cosmetic, or test-depth; safe to defer.

## Master index

IDs are stable. Area prefixes: `RT` realtime · `LLM` llm · `AUD` audio-io · `INF` core-infra · `OPS` integrations/ops · `EXE` execution · `DEP` dependencies.

| ID | Pri | Area | Title | Primary file | Batch |
|----|-----|------|-------|--------------|-------|
| EXE-01 | P0 | exec | Committed `.env` duplicate keys flip to realtime/mic; breaks dry-run + 1 test | `.env` | A |
| RT-01 | P0 | realtime | WebSocket URL omits mandatory `?model=`; live path can't connect | `pipeline/realtime.py` | B |
| LLM-01 | P1 | llm | `ACTIVE_MODEL` placeholder `dry-run-v1` sent to live providers; no model validation | `pipeline/llm.py`, `config/settings.py`, `.env.example` | A·C·G |
| LLM-02 | P1 | llm | Chat mode `max_tokens` → 400 on reasoning models; use `max_completion_tokens` | `pipeline/llm.py` | C |
| AUD-01 | P1 | audio | Default `AUDIO_DEVICE_INDEX=0` picks wrong/failed Windows device; use `default` | `config/settings.py`, `.env.example` | A·D |
| AUD-02 | P1 | audio | `PLAYBACK_MODE=system` never plays on Windows (macOS-only afplay) | `pipeline/tts.py` | D |
| AUD-03 | P1 | audio | `PLAYBACK_MODE=sdk` silently needs ffmpeg; pass `use_ffmpeg=False` | `pipeline/tts.py` | D |
| INF-01 | P1 | infra | Concurrent runs clobber the session JSON (no file lock) — data loss | `pipeline/memory.py` | E |
| INF-02 | P1 | infra | Blank `KEY=` env lines crash startup instead of using defaults | `config/settings.py` | A |
| INF-03 | P1 | infra | `.gitignore` misses `audio/<session_id>/`; real recordings can be committed | `.gitignore` | A |
| INF-04 | P1 | infra | `--doctor` write-tests the wrong (legacy) audio dirs; green ≠ recordable | `pipeline/preflight.py` | G |
| OPS-01 | P1 | ops | `run_episode` blocks on stdin; not drivable by an OpenClaw agent | `integrations/openclaw_tools.py` | H |
| OPS-02 | P1 | ops | Path traversal: `load_session`/`export_transcript` read arbitrary files | `integrations/openclaw_tools.py` | H |
| OPS-03 | P1 | ops | Docs use POSIX/macOS shell; commands fail in PowerShell (Windows target) | `README.md`, `docs/` | I |
| RT-02 | P1 | realtime | Blocking `output_stream.write()` on the event loop stalls mic/barge-in | `pipeline/realtime.py` | B |
| EXE-02 | P1 | exec | Realtime auth/WS failure dumps a raw traceback instead of a clean error | `pipeline/realtime.py`, `main.py` | B |
| EXE-03 | P1 | exec | CLI test reads the developer's real `.env` (non-hermetic) — why CI flips red | `tests/test_cli_dry_run.py` | A |
| EXE-04 | P1 | exec | Committed `.venv` is stale, mixed-platform, empty | `.venv/`, `.gitignore` | A |
| LLM-03 | P2 | llm | Google client has no request timeout; a hung call freezes the episode | `pipeline/llm.py` | C·F |
| LLM-04 | P2 | llm | Hard 1024-token cap + no truncation detection; replies cut off silently | `pipeline/llm.py` | C |
| LLM-05 | P2 | llm | Refusals/safety blocks reported as generic "empty response" | `pipeline/llm.py` | C |
| LLM-06 | P2 | llm | Retry budget doubled vs SDK retries; underlying error obscured | `pipeline/reliability.py`, `pipeline/llm.py` | C·F |
| RT-03 | P2 | realtime | Manual `input_audio_buffer.commit` conflicts with VAD; raises error event | `pipeline/realtime.py` | B |
| RT-04 | P2 | realtime | No mid-session network-drop / reconnect handling | `pipeline/realtime.py` | B |
| RT-05 | P2 | realtime | Mic frames silently dropped when the queue is full | `pipeline/realtime.py` | B |
| RT-06 | P2 | realtime | Connection + concurrency code is untested (hides RT-01) | `tests/test_realtime.py` | B |
| AUD-04 | P2 | audio | Unbounded in-memory recording buffer; no max-duration guard | `pipeline/stt.py` | D |
| AUD-05 | P2 | audio | Deepgram legacy fallback branches would send no model/options | `pipeline/stt.py` | D |
| AUD-06 | P2 | audio | `OUTPUT_AUDIO_DEVICE` is configured & documented but never used | `config/settings.py`, `pipeline/tts.py` | D |
| INF-05 | P2 | infra | Session `json.dumps` lacks `default=str`/`ensure_ascii=False` | `pipeline/memory.py` | E |
| INF-06 | P2 | infra | `update_turn_metadata` raises if the turn was trimmed | `pipeline/memory.py` | E |
| INF-07 | P2 | infra | Whole session rewritten every save (O(n²) over a long episode) | `pipeline/memory.py` | E |
| INF-08 | P2 | infra | `latest_for_episode` sorts by name, not time; clock rollback → wrong resume | `pipeline/memory.py` | E |
| INF-09 | P2 | infra | `retry_call` accepts but discards `timeout_seconds`; no wall-clock bound | `pipeline/reliability.py` | F |
| INF-10 | P2 | infra | Doctor does no key/disk/output-device liveness checks | `pipeline/preflight.py` | G |
| INF-11 | P2 | infra | Any host/LLM/TTS exception ends the whole episode (no retry/skip) | `main.py` | F |
| INF-12 | P2 | infra | Realtime path drops `--max-turns` / `--confirm-transcript` | `main.py`, `pipeline/realtime.py` | B |
| OPS-04 | P2 | ops | Docs overstate OpenClaw: it's an in-process library, no network layer | `README.md`, `docs/` | I |
| OPS-05 | P2 | ops | Operator guide points at legacy flat audio paths | `docs/AI_PODCAST_OPERATOR_GUIDE.md` | I |
| OPS-06 | P2 | ops | Undocumented system deps (PortAudio, ffmpeg-for-sdk, VB-Audio cable) | `README.md`, `docs/` | I |
| DEP-01 | P2 | deps | Deps mostly uncapped; majors drifted (openai 2.x, numpy 2.x); no lockfile | `requirements.txt` | J |
| LLM-07 | P3 | llm | No Anthropic prompt caching; full prefix re-sent every turn (cost) | `pipeline/llm.py` | C |
| LLM-08 | P3 | llm | LLM tests are happy-path only (no Anthropic/failure/retry coverage) | `tests/test_llm.py` | C |
| RT-07 | P3 | realtime | Windows ENTER-stop uses a non-cancellable `to_thread(input)` thread | `pipeline/realtime.py` | B |
| RT-08 | P3 | realtime | Default realtime model/transcription ids may age out of the account | `config/settings.py`, `pipeline/preflight.py` | B·G |
| AUD-08 | P3 | audio | Multichannel capture not signaled to Deepgram | `pipeline/stt.py` | D |
| AUD-09 | P3 | audio | Audio tests mock everything; no real WAV/device/playback coverage | `tests/` | D |
| INF-13 | P3 | infra | Turn ids leak on skipped/empty/quit iterations (filename gaps) | `main.py`, `pipeline/memory.py` | F |
| INF-14 | P3 | infra | Orphan `.tmp` files accumulate on crash across all atomic writers | `pipeline/memory.py` et al. | E |
| INF-15 | P3 | infra | `KeyboardInterrupt` mid-turn dumps a traceback; partial turn on resume | `main.py` | F |
| INF-16 | P3 | infra | Base prompt still says "[PODCAST NAME TBD]" — the AI will speak it | `config/prompts/base_system.txt` | A |
| INF-18 | P3 | infra | `from_session_file` derives audio dir from filename stem; rename splits media | `pipeline/memory.py` | E |
| OPS-08 | P3 | ops | `rich` is an unused dep; `pytest` mixed into runtime requirements | `requirements.txt` | J |
| OPS-09 | P3 | ops | No LICENSE file / license undeclared | repo root | I |
| OPS-11 | P3 | ops | `load_system_prompt` doesn't sanitize `episode_name` (read/write mismatch) | `pipeline/llm.py` | H |
| OPS-12 | P3 | ops | OpenClaw surface has thin test coverage (no `run_episode`, no error paths) | `tests/test_openclaw_tools.py` | H |
| EXE-06 | P3 | exec | Non-ASCII device names render as mojibake in `--list-devices` | `main.py`, `pipeline/stt.py` | J |

> Note on de-duplication: a handful of findings were reported by two agents from different
> angles and are merged here — `.gitignore` audio gap (INF-03), discarded `retry_call` timeout
> (INF-09), realtime `--max-turns` drop (INF-12), `PLAYBACK_MODE=system` on Windows (AUD-02),
> the `ACTIVE_MODEL` placeholder (LLM-01), and dependency pinning (DEP-01). Each appears once.
