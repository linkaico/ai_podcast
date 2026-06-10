# 05 — Integrations, Ops, Security & Documentation

> ✅ **Status (Batches A/D/H/I/J):** every Known Issue **resolved** — OPS-02 (path-traversal contained to `sessions/`), OPS-01 (`run_episode` drivable via `input_fn`/`output_fn`), OPS-11 (episode-name sanitized on read), OPS-12 (integration + security tests) in **H**; OPS-03/04/05/06/09 (PowerShell docs, OpenClaw rewording, session-scoped paths, system prerequisites, LICENSE) in **I**; OPS-08 + DEP-01 (caps + `requirements-dev.txt` + `requirements.lock`) in **J**; `.env` dedup / `.gitignore` audio / Windows playback earlier in A/D. Per-ticket status: [`../../tickets/README.md`](../../tickets/README.md).

Audit context for the OpenClaw integration surface, operations/setup, secrets/security,
dependency hygiene, and documentation accuracy of the real-time AI podcast pipeline.

Project root: `c:\Users\Flori\Documents\AI Podcast\podcast` (Windows / win32 / PowerShell).
This document lives at `podcast/audit/context/05-integrations-ops/`; relative links below
walk back up with `../../../` to reach project source.

Source files in scope:
- [`integrations/openclaw_tools.py`](../../../integrations/openclaw_tools.py)
- [`tests/test_openclaw_tools.py`](../../../tests/test_openclaw_tools.py)
- [`README.md`](../../../README.md)
- [`docs/AI_PODCAST_OPERATOR_GUIDE.md`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md)
- [`docs/implementation-updates/`](../../../docs/implementation-updates/)
- [`.env.example`](../../../.env.example) · [`.gitignore`](../../../.gitignore) · `.env` (untracked, local only)
- [`requirements.txt`](../../../requirements.txt)

Supporting source cross-referenced:
- [`main.py`](../../../main.py) · [`config/settings.py`](../../../config/settings.py)
- [`pipeline/memory.py`](../../../pipeline/memory.py) · [`pipeline/llm.py`](../../../pipeline/llm.py)
- [`pipeline/realtime.py`](../../../pipeline/realtime.py) · [`pipeline/stt.py`](../../../pipeline/stt.py)
- [`pipeline/tts.py`](../../../pipeline/tts.py) · [`pipeline/preflight.py`](../../../pipeline/preflight.py)

---

## 1. OpenClaw Callable Surface

All seven helpers live in [`integrations/openclaw_tools.py`](../../../integrations/openclaw_tools.py).
The module **imports cleanly with no side effects** (verified: no stdout/stderr on import, no
network calls, no file writes). It imports `main.run_episode` and `pipeline.realtime.run_realtime_episode`
at module load, so importing it pulls in the full pipeline import graph (argparse, asyncio, etc.) but
executes nothing.

`PROJECT_ROOT` is `config/settings.py`'s `Path(__file__).resolve().parents[1]`, i.e. the `podcast/`
directory. `root_dir=None` on every helper defaults to `PROJECT_ROOT`.

| Function | Signature (actual) | Reads | Writes | Notes |
|---|---|---|---|---|
| `run_episode` | `(name, resume=False, session_path=None, max_turns=None) -> dict` | session JSON (return metadata) | session JSON + audio via the live loop | **Blocks on stdin** (see §1.1). Branches on `settings.uses_realtime`. |
| `write_episode_context` | `(episode_name, content, sources=None, root_dir=None) -> dict` | — | `config/prompts/episodes/<safe_name>.txt` | `episode_name` sanitized via `safe_episode_name`. Empty content → `ValueError`. |
| `list_sessions` | `(episode_name=None, root_dir=None) -> list[dict]` | `sessions/<safe>_*.json` (or `*.json`) | — | Returns sorted metadata; filters falsy entries. |
| `load_session` | `(session_path, root_dir=None) -> dict` | the given path | — | **Absolute paths returned as-is → arbitrary file read** (see §3). |
| `latest_session` | `(episode_name, root_dir=None) -> dict` | newest `sessions/<safe>_*.json` | — | Raises `FileNotFoundError` if none. |
| `episode_artifacts` | `(episode_name, root_dir=None) -> dict` | newest session JSON | — | Returns `{episode, session_path, artifacts}`. |
| `export_transcript` | `(session_path, format="markdown", root_dir=None) -> dict` | the given session path | `exports/<session_stem>.md` | Only `markdown` supported (else `ValueError`). Same absolute-path read exposure as `load_session`. |

### Return shapes

`_session_metadata(path)` (used by `run_episode`, `list_sessions`, `latest_session`) returns:
```python
{"episode": str|None, "path": str, "saved_at": str|None, "turns": int, "artifacts": dict}
```
`run_episode`, `latest_session` return this directly. `list_sessions` returns a list of these.
`episode_artifacts` returns `{"episode": str, "session_path": str, "artifacts": dict}` (different
key: `session_path`, not `path`). `write_episode_context` returns `{"episode", "path", "sources_count"}`.
`export_transcript` returns `{"episode", "format", "path", "turns"}`. `load_session` returns the raw
parsed session JSON. The shapes are **internally consistent but not uniform** — three different "path"
key names across the surface (`path`, `session_path`).

### 1.1 `run_episode` blocks on stdin — not agent-callable as documented

[`integrations/openclaw_tools.py:15-35`](../../../integrations/openclaw_tools.py) calls
`main.run_episode(name, settings=..., resume=..., session_path=..., max_turns=...)` **without an
`input_fn`**. [`main.run_episode`](../../../main.py) defaults `input_fn: Callable = input` (builtin).
The loop calls `capture_text_turn(input_fn=input)` (text mode → `input("FLORIAN> ")`) or
`record_until_keypress(... input_fn=input)` (mic mode → `input("")`). Both **block on the interactive
console**. An OpenClaw agent invoking `run_episode(...)` in-process would hang waiting for keyboard
input there is no way to inject through the wrapper.

The realtime branch (`run_realtime_episode`) is worse for automation: it opens a live microphone
(`sounddevice.RawInputStream`), a speaker (`RawOutputStream`), and a WebSocket to OpenAI, and ends only
when a human presses ENTER (`_wait_for_stop` reads `sys.stdin`). `max_turns` is **ignored** on the
realtime path (the wrapper only forwards `max_turns` to the non-realtime branch; `run_realtime_episode`
has no such parameter).

Net: of the seven helpers, six are genuine programmatic file/metadata operations; `run_episode` is a
thin wrapper over an **interactive, human-driven** loop and cannot be driven by an agent without a TTY.
The README lists it in the "Callable surface" without this caveat.

### 1.2 `write_episode_context` → `load_system_prompt` path round-trips correctly

`write_episode_context` writes to `root/config/prompts/episodes/<safe_name>.txt`, where
`<safe_name> = safe_episode_name(episode_name)` ([`openclaw_tools.py:49-52`](../../../integrations/openclaw_tools.py)).
[`pipeline/llm.load_system_prompt`](../../../pipeline/llm.py) reads
`root/config/prompts/episodes/<episode_name>.txt` using the **raw** name it is given.
In the live loop, `main.run_episode` calls `load_system_prompt(episode_name=memory.episode_name, ...)`,
and `memory.episode_name` is already sanitized by `ConversationMemory.__post_init__`
([`pipeline/memory.py:42`](../../../pipeline/memory.py)). So both sides converge on the same
sanitized filename and the handoff works **as long as both paths apply the same sanitizer** — which they
currently do. Caveat: `load_system_prompt` does not itself sanitize, so a future caller passing a raw
unsanitized `episode_name` to it directly would look for a different file than `write_episode_context`
wrote. Not a current bug; a latent coupling.

### 1.3 `export_transcript` vs the real session schema

The session schema written by [`ConversationMemory._save`](../../../pipeline/memory.py) is:
```json
{"episode","session_id","next_turn_id","saved_at","history":[{role,content,created_at,metadata?}],
 "events":[...],"artifacts":{...}}
```
`export_transcript` reads `payload["episode"]`, `payload["saved_at"]`, and iterates
`payload["history"]` using `turn["role"]` / `turn["content"]` — all of which match the real schema.
It hardcodes the host name as **"Florian"** for `role=="user"` and "AI" otherwise. The `.get(...)`
defaults make it tolerant of missing fields. This function is schema-correct.

### 1.4 No network/registration layer (by design, but a gap vs the framing)

There is **no service, RPC, HTTP, or registration layer** for OpenClaw anywhere in the repo. "OpenClaw
integration" is purely a set of local Python functions importable from the same process. Both
[`README.md:243`](../../../README.md) ("Network/service-level OpenClaw registration" — Deferred) and
[`docs/implementation-updates/2026-04-19-openclaw-session-integration.md:64`](../../../docs/implementation-updates/2026-04-19-openclaw-session-integration.md)
("Network/service registration with OpenClaw" — Intentionally Deferred) acknowledge this. The gap to
flag: the README's headline claim "OpenClaw agents **can call** local Python helpers" is only true for an
agent that runs **in the same Python interpreter / same machine, with this repo importable**. There is no
remote invocation path. Anyone reading "OpenClaw agents can call these" may expect a registered tool
endpoint; what exists is an in-process library.

### 1.5 Test coverage

[`tests/test_openclaw_tools.py`](../../../tests/test_openclaw_tools.py) (4 tests, all passing) covers
`write_episode_context`, `list_sessions`/`load_session`/`latest_session`, `episode_artifacts`, and
`export_transcript`, all through `root_dir=tmp_path`. **Not covered:** `run_episode` (the blocking one),
the absolute-path read exposure, and any error paths (`ValueError` on empty content / non-markdown
format, `FileNotFoundError`).

---

## 2. Documentation Map

| Doc | Purpose | Accuracy summary |
|---|---|---|
| [`README.md`](../../../README.md) | Overview, setup, modes, OpenClaw surface | Command syntax is **POSIX/macOS-Linux**, not PowerShell (§6). OpenClaw signatures match code exactly. |
| [`docs/AI_PODCAST_OPERATOR_GUIDE.md`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | Step-by-step recording checklist | Same POSIX-isms; internal path inconsistency (`audio/output/` vs `audio/<session_id>/output/`); macOS-only `touch`, heredoc, `/tmp`. |
| [`docs/implementation-updates/2026-04-19-backend-foundation.md`](../../../docs/implementation-updates/2026-04-19-backend-foundation.md) | Slice 1 changelog | Historical; accurate. |
| [`docs/implementation-updates/2026-04-19-audio-integration.md`](../../../docs/implementation-updates/2026-04-19-audio-integration.md) | Slice 2 changelog | Historical; refers to legacy flat `audio/input` `audio/output` paths (pre session-scoping). |
| [`docs/implementation-updates/2026-04-19-recording-hardening.md`](../../../docs/implementation-updates/2026-04-19-recording-hardening.md) | Slice 3 changelog | Historical; accurate. |
| [`docs/implementation-updates/2026-04-19-openclaw-session-integration.md`](../../../docs/implementation-updates/2026-04-19-openclaw-session-integration.md) | Slice 4 changelog (OpenClaw) | Signatures match. POSIX heredoc-style examples. |

### Documentation accuracy flags (verified against code)

- **Env-var-prefix command syntax does not run in PowerShell.** Throughout both docs, e.g.
  [`README.md:8`](../../../README.md) `CONVERSATION_MODE=realtime INPUT_MODE=mic python main.py pilot`,
  [`README.md:170`](../../../README.md), [`README.md:174`](../../../README.md),
  [`operator guide:207,213,219,348,412-413,419-420`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md).
  PowerShell treats `NAME=value cmd` as a parse error / literal, not an inline env assignment. Requires
  `$env:NAME="value"; python main.py ...` per variable, or setting them in `.env`.
- **`source .venv/bin/activate`** ([`README.md:23`](../../../README.md),
  [`operator guide:18`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md)) — wrong on Windows. Should be
  `.venv\Scripts\Activate.ps1` (PowerShell) or `.venv\Scripts\activate.bat` (cmd).
- **`cp .env.example .env`** ([`README.md:25`](../../../README.md),
  [`operator guide:20`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md)) — `cp` is not a PowerShell command;
  should be `Copy-Item .env.example .env`.
- **`touch config/prompts/episodes/pilot.txt`** ([`operator guide:179`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md))
  — `touch` does not exist in PowerShell.
- **Heredoc `python - <<'PY' ... PY`** ([`operator guide:327-333`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md))
  — POSIX shell heredoc; does not work in PowerShell.
- **`env PYTHONPATH=/tmp/pytest_no_readline ... python -m pytest`** ([`operator guide:360`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md))
  — `env` prefix and `/tmp` are POSIX-only; the whole readline-segfault workaround is Anaconda/macOS-specific
  and irrelevant on Windows.
- **Operator guide internal path inconsistency.** §7 correctly documents outputs under
  `audio/<session_id>/...`, but §10 "AI Audio Does Not Play Live"
  ([line 392](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md)) and §11 "inspect"
  ([lines 427-429](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md)) tell the user to look in flat
  `audio/output/` and `audio/input/`. The runtime writes only to session-scoped dirs
  ([`pipeline/memory.py:56-61`](../../../pipeline/memory.py)). The flat dirs only hold a stale
  `dryrun_ai_turn_*.txt` and a `.gitkeep`.
- **OBS: "VB-Audio Cable on Windows/Linux"** ([`README.md:179`](../../../README.md),
  [`operator guide:134`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md)) — VB-Audio Virtual Cable is
  **Windows-only**; Linux would use PulseAudio/PipeWire loopback. Minor.
- **"appended by `load_system_prompt()` the next time that episode runs"** ([`README.md:222`](../../../README.md))
  — accurate; verified in §1.2.

---

## 3. Secrets & Security Model

### Env / secrets

- Config is loaded by [`config/settings.py`](../../../config/settings.py): `_load_dotenv` reads `.env`
  **only if `python-dotenv` is installed** (silent no-op otherwise — env-var-only fallback). All keys
  come from environment variables; `Settings` is a frozen dataclass.
- **`.env` is gitignored** ([`.gitignore:1`](../../../.gitignore)) and **was never committed** (verified:
  `git log --all -- .env` is empty; `git check-ignore .env` confirms ignore). Good.
- A **real local `.env` exists** on disk (1.4 KB, untracked). It contains exactly one non-empty
  secret-bearing value: `OPENAI_API_KEY` (a short ~13-char value — likely a placeholder/test stub, not a
  full-length production key; **not printed here**). All other key slots (`ANTHROPIC`, `GOOGLE`,
  `DEEPGRAM`, `ELEVENLABS`, `XAI`, `ELEVENLABS_VOICE_ID`) are empty. Risk is **local-disk only**; nothing
  is exposed via git.
- **`.env` has duplicate keys.** `OPENAI_API_KEY`, `CONVERSATION_MODE`, `INPUT_MODE`, and all five
  `REALTIME_*` keys appear **twice** in the local `.env`. With `python-dotenv`, the **last** occurrence
  wins, so an appended realtime block silently overrides the earlier dry-run defaults. This is confusing
  and error-prone (a user editing the first occurrence sees no effect).

### Secrets in logs / errors

- API keys are passed to SDK client constructors and to `Authorization: Bearer ...` headers
  ([`pipeline/realtime.py:209`](../../../pipeline/realtime.py),
  [`pipeline/stt.py:252`](../../../pipeline/stt.py), [`pipeline/tts.py:168`](../../../pipeline/tts.py)).
  No code path prints a key value. `structured_error` and `add_event` record stage/error strings, not
  keys. **No key leakage found in logs.** (A provider SDK could theoretically echo a key in an exception
  string, but the app does not.)

### Path handling / traversal

- **`episode_name` is safe.** `safe_episode_name` ([`pipeline/memory.py:22-24`](../../../pipeline/memory.py))
  replaces `[^A-Za-z0-9_.-]+` with `_` and strips leading/trailing `._`, collapsing traversal:
  verified `'../../etc/passwd' -> 'etc_passwd'`, `'....' -> 'default'`, `'/abs/path' -> 'abs_path'`.
  So `write_episode_context`, `list_sessions`, `latest_session`, `episode_artifacts` cannot be coerced
  to write/read outside `config/prompts/episodes` or `sessions`.
- **`session_path` is NOT contained.** `load_session` and `export_transcript` take a raw `session_path`,
  and `_resolve_session_path` ([`openclaw_tools.py:165-170`](../../../integrations/openclaw_tools.py))
  returns absolute paths unchanged and joins relative ones to root **without a containment check**.
  Verified empirically: `load_session("<abs path outside repo>.json")` reads and returns that file.
  `export_transcript` then re-`load_session`s and writes a derived `.md` into `exports/`. For a surface
  framed as an external-agent API, this is **arbitrary local file read** of any JSON-parseable file (and
  an information-disclosure vector into `exports/`). Lower risk while the surface is in-process and
  single-user, but it is the only real security defect in the integration layer.

### Git hygiene

- Tracked-but-shouldn't-be: **none**. Only `.gitkeep` placeholders are tracked under `sessions/`,
  `audio/`, `exports/`, `config/prompts/episodes/`. No `__pycache__`, `.venv`, `.pytest_cache`,
  `*.pyc`, session JSON, or audio is tracked (verified `git ls-files`).
- On disk but correctly ignored/untracked: `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`,
  four `sessions/pilot_*.json` (contain real transcript content), `audio/output/dryrun_ai_turn_*.txt`.
- **`.gitignore` gap (latent leak):** it ignores `audio/input/*` and `audio/output/*` (the **legacy flat**
  dirs) but **not** the session-scoped `audio/<session_id>/` tree the app actually writes. Real recorded
  host/AI WAVs (`audio/<session_id>/input|output/...`) are **not** matched by any ignore rule and would be
  staged by `git add -A`. No such files exist yet, so nothing is leaked today, but the protection the
  author intended is not in place. (Session JSON itself is covered by `sessions/*.json`.)

### Licensing

- **No `LICENSE` file** exists. No license declared in README or any doc. The repo is effectively
  "all rights reserved" by default, which may or may not be intended.

---

## 4. Dependency Inventory

Declared in [`requirements.txt`](../../../requirements.txt):

| Package | Pin (declared) | Installed in this env | Capped? | Notes |
|---|---|---|---|---|
| anthropic | `>=0.50.0` | 0.104.1 | no upper | LLM provider (optional, lazy import). |
| openai | `>=1.70.0` | **2.38.0** | no upper | **Major drift to 2.x.** Used for chained OpenAI + Realtime. `client.responses.create` / `chat.completions` assumed. |
| google-genai | `>=1.0.0` | 2.6.0 | no upper | LLM provider (optional). |
| deepgram-sdk | `>=7.1.0,<8.0.0` | 7.2.0 | **yes** | STT. Code probes `listen.v1.media` then `rest`/`prerecorded` fallbacks. |
| elevenlabs | `>=1.16.0` | 2.49.0 | no upper | TTS (optional). Major drift to 2.x. |
| requests | `>=2.31.0` | 2.32.5 | no upper | Used only by xAI STT/TTS REST paths. |
| sounddevice | `>=0.4.7` | 0.5.5 | no upper | Mic/realtime I/O. **Needs PortAudio system lib.** |
| soundfile | `>=0.12.1` | 0.13.1 | no upper | WAV read/write. **Needs libsndfile** (bundled in wheels). |
| python-dotenv | `>=1.0.0` | 1.2.1 | no upper | `.env` loading (optional — code degrades gracefully if absent). |
| numpy | `>=1.26.0` | **2.4.0** | no upper | Audio buffers. Major drift to 2.x (no `<3`). |
| rich | `>=13.0.0` | 15.0.0 | no upper | Declared but **see "unused" below**. |
| websockets | `>=14.0,<16.0` | 15.0.1 | **yes** | Realtime transport. Cap rationale below. |
| pytest | `>=8.0.0` | 9.0.2 | no upper | Test-only dep (not separated into dev-requirements). |

### Cap rationales / risks

- **`websockets>=14.0,<16.0`:** [`pipeline/realtime.py:317`](../../../pipeline/realtime.py) imports
  `from websockets.asyncio.client import connect` and passes `additional_headers=` — the new asyncio
  client API introduced in websockets **14.0** (replacing the legacy `extra_headers`). The `<16.0` ceiling
  guards against the 16.x line where further API churn/removals could break this call. Verified: installed
  15.0.1 has `additional_headers`. The cap is **sensible but undocumented** in the README/requirements.
- **`deepgram-sdk<8.0.0`:** the 8.x major reorganized the client; the code's multi-path probe
  (`media.transcribe_file` → `rest.v("1")` → `prerecorded.v("1")`) targets 7.x shapes. Cap is sensible,
  also undocumented.
- **Uncapped majors (openai, elevenlabs, google-genai, anthropic, numpy, rich, pytest):** floors only.
  Fresh installs pull whatever is latest, so the build is **not reproducible** and a future provider major
  can silently break runtime (e.g., the installed `openai==2.x` vs a `>=1.70` floor that predates the 2.0
  API). No lockfile (`requirements.lock` / `pip freeze`) is committed.
- **`rich` appears unused.** No `import rich` (or `from rich`) anywhere in `pipeline/`, `config/`,
  `integrations/`, or `main.py`. Likely dead dependency.
- **`pytest` is a test-only dep mixed into runtime requirements** — minor hygiene (no
  `requirements-dev.txt`).

### System (non-pip) dependencies — undocumented

- **PortAudio** — required by `sounddevice` for any mic capture or audio output (`InputStream`,
  `RawInputStream`, `RawOutputStream` in [`stt.py`](../../../pipeline/stt.py) /
  [`realtime.py`](../../../pipeline/realtime.py)). On Windows the `sounddevice` wheel bundles PortAudio,
  so it usually "just works," but a virtual audio device/driver is still needed for OBS routing. **Not
  documented as a prerequisite.**
- **libsndfile** — needed by `soundfile`; bundled in the wheel on Windows. Usually fine, undocumented.
- **ffmpeg / mpv** — **only** needed if `PLAYBACK_MODE=sdk` (ElevenLabs `play()` shells out to a media
  player under the hood). The default `PLAYBACK_MODE=file-only` writes the MP3 and **does not decode/play**
  anything, so ffmpeg is **not** required for the documented default flow. Note the docs never actually
  invoke ffmpeg; "mp3 playback" is delegated to OBS/Descript on the saved stems.
- **Virtual audio cable** (VB-Audio Virtual Cable on Windows) — required to route AI playback into OBS as
  a separate track. Mentioned in the OBS checklist but as an aside, not a gated setup step.

---

## 5. Fresh-Machine Setup Gap (Windows focus)

What a brand-new Windows user needs that the docs do **not** correctly wire:

1. **Venv activation** — use `.venv\Scripts\Activate.ps1` (PowerShell), not `source .venv/bin/activate`.
   May also require `Set-ExecutionPolicy -Scope Process RemoteSigned` to allow the activation script.
2. **Copy env file** — `Copy-Item .env.example .env`, not `cp`.
3. **Per-command env vars** — every `VAR=value python main.py ...` example must become
   `$env:VAR="value"; python main.py ...`, or the values must be set in `.env`. As written, none of the
   "live"/"rehearsal" one-liners run in PowerShell.
4. **A populated `.env`** with at least one real LLM provider key + `ACTIVE_MODEL`, and provider keys for
   the chosen STT/TTS path (Deepgram/ElevenLabs/xAI) or `OPENAI_API_KEY` for realtime. `.env.example`
   ships every key empty and `ACTIVE_MODEL=dry-run-v1`; real model IDs are left as `<your_*_model>`
   placeholders in the operator guide.
5. **Microphone + PortAudio** — `sounddevice` wheel bundles PortAudio on Windows, but the user must pick a
   real device via `python main.py --list-devices` and set `AUDIO_DEVICE_INDEX`.
6. **VB-Audio Virtual Cable** (Windows) installed and selected if AI audio is to be captured live in OBS;
   otherwise import saved stems post-hoc.
7. **OBS** installed and configured with two audio sources (mic + virtual cable).
8. **Realtime sample rate constraint** — `REALTIME_SAMPLE_RATE` must be exactly `24000`
   ([`config/settings.py:209`](../../../config/settings.py)) or validation fails; realtime also forces
   `INPUT_MODE=mic`.
9. **`touch`/heredoc substitutes** — create episode prompt files with
   `New-Item config/prompts/episodes/pilot.txt` (or via `write_episode_context`); run the transcript-export
   snippet as a real `.py` file, not a heredoc.
10. **Playback** — leave `PLAYBACK_MODE=file-only` (default). `system` playback is **macOS-only** and
    raises on Windows ([`pipeline/tts.py:264-268`](../../../pipeline/tts.py)); `sdk` playback needs ffmpeg.

---

## 6. Cross-Platform (Windows vs macOS/Linux docs)

The project targets Windows but the docs are macOS/Linux-flavored. Every place this bites:

| Location | Problem on Windows/PowerShell |
|---|---|
| [`README.md:23`](../../../README.md), [`operator guide:18`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | `source .venv/bin/activate` — wrong activation path. |
| [`README.md:25`](../../../README.md), [`operator guide:20`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | `cp .env.example .env` — `cp` undefined. |
| [`README.md:8,170,174`](../../../README.md), [`operator guide:207,213,219,348,412-413,419-420`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | `VAR=value python ...` inline env prefix — parse error in PowerShell. |
| [`operator guide:179`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | `touch ...` — undefined. |
| [`operator guide:327-333`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | `python - <<'PY'` heredoc — POSIX-only. |
| [`operator guide:360`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | `env PYTHONPATH=/tmp/...` — POSIX `env` + `/tmp`; Anaconda/macOS-specific readline note. |
| [`pipeline/tts.py:264-268`](../../../pipeline/tts.py) | `_system_play` only implements macOS `afplay`; `PLAYBACK_MODE=system` **raises `RuntimeError` on Windows** (default is `file-only`, so not hit by default). |
| [`README.md:179`](../../../README.md), [`operator guide:134`](../../../docs/AI_PODCAST_OPERATOR_GUIDE.md) | "VB-Audio Cable on Windows/Linux" — VB-Audio is Windows-only. |

---

## 7. Known Issues (summary — full list with severities in the audit issue report)

1. **`run_episode` blocks on stdin / not agent-invocable; realtime ignores `max_turns`.** (§1.1)
2. **Arbitrary local file read** via `load_session` / `export_transcript` absolute `session_path` —
   no containment check. (§3)
3. **All shell command examples are POSIX/macOS-Linux**, broken in PowerShell (env-prefix, `source`,
   `cp`, `touch`, heredoc). The project targets Windows. (§2, §6)
4. **`.gitignore` does not cover session-scoped `audio/<session_id>/`** — real recorded audio would be
   committed. (§3)
5. **No network/registration layer for OpenClaw** — "agents can call these" overstates an in-process
   library; remote invocation is deferred. (§1.4)
6. **Dependency pins are mostly uncapped majors; installed versions drifted to new majors
   (openai 2.x, numpy 2.x, elevenlabs 2.x); no lockfile.** `rich` appears unused; `pytest` mixed into
   runtime deps. (§4)
7. **Undocumented system deps** (PortAudio via sounddevice; ffmpeg only for `PLAYBACK_MODE=sdk`; VB-Audio
   cable for OBS). (§4, §5)
8. **`.env` has duplicate keys** (last-wins via dotenv) — confusing and error-prone. (§3)
9. **Operator guide internal path inconsistency** — flat `audio/output/` (§10/§11) vs session-scoped
   (§7). (§2)
10. **No `LICENSE` file.** (§3)
11. **`PLAYBACK_MODE=system` is macOS-only** and raises on Windows. (§6)
12. **Latent coupling:** `load_system_prompt` does not sanitize `episode_name`; correctness depends on all
    callers pre-sanitizing. (§1.2)
13. **Thin test coverage** of the integration surface (no `run_episode`, no error paths, no traversal). (§1.5)
