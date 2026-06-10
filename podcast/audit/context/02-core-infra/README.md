# 02 — Core Infrastructure (config, session persistence, preflight, reliability, CLI)

> ✅ **Status (Batches A–J):** every Known Issue in this doc is **resolved** — INF-02/03/16 (Batch A), INF-01/05/06/07/08/14/18 (E), INF-09/11/13/15 (F), INF-04/10 (G), plus realtime INF-12 (B). New settings since the audit: `provider_max_output_tokens`, `audio_max_record_seconds`, `audio_device_index="default"`; sessions are now file-locked and saves are turn-boundary. Per-ticket status: [`../../tickets/README.md`](../../tickets/README.md).

Audit context for the backbone of the podcast pipeline: how the app is
**configured**, how a recording session is **persisted to disk** (the recording
source of truth), how `--doctor` **preflight** verifies readiness, how provider
calls are made **reliable**, and how the **CLI** orchestrates a recording loop.

**In scope (audited / owned):**
- [config/settings.py](../../../config/settings.py) — `Settings` dataclass, env loading, all validation
- [pipeline/memory.py](../../../pipeline/memory.py) — `ConversationMemory`: session JSON persistence, turn ids, resume, event log, audio dirs
- [pipeline/preflight.py](../../../pipeline/preflight.py) — the `--doctor` checks
- [pipeline/reliability.py](../../../pipeline/reliability.py) — `retry_call`, `ProviderCallError`, `structured_error`
- [main.py](../../../main.py) — argparse, `run_episode` loop, host-turn capture, exit commands, error handling
- [config/prompts/base_system.txt](../../../config/prompts/base_system.txt) — AI co-host persona prompt
- Tests: [test_settings.py](../../../tests/test_settings.py), [test_memory.py](../../../tests/test_memory.py), [test_preflight.py](../../../tests/test_preflight.py), [test_reliability.py](../../../tests/test_reliability.py), [test_cli_dry_run.py](../../../tests/test_cli_dry_run.py)

**Read for context (other agents own):** [pipeline/llm.py](../../../pipeline/llm.py), [pipeline/stt.py](../../../pipeline/stt.py), [pipeline/tts.py](../../../pipeline/tts.py), [pipeline/realtime.py](../../../pipeline/realtime.py), [integrations/openclaw_tools.py](../../../integrations/openclaw_tools.py)

---

## 1. The Settings model

[`Settings`](../../../config/settings.py) is a `@dataclass(frozen=True)`. It is
immutable; `with_overrides(**kwargs)` returns a re-validated copy via
`dataclasses.replace`. Every field is loaded from an environment variable (with
`.env` loaded first by `_load_dotenv`, only if `python-dotenv` is installed).

### Fields and defaults

| Field | Env var | Default | Type |
|---|---|---|---|
| `root_dir` | — | `PROJECT_ROOT` | `Path` |
| `active_llm` | `ACTIVE_LLM` | `dry-run` | str (lowercased) |
| `active_model` | `ACTIVE_MODEL` | `dry-run-v1` | str (**not** lowercased) |
| `conversation_mode` | `CONVERSATION_MODE` | `dry-run` | str (lowercased) |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `""` | str |
| `openai_api_key` | `OPENAI_API_KEY` | `""` | str |
| `google_api_key` | `GOOGLE_API_KEY` | `""` | str |
| `deepgram_api_key` | `DEEPGRAM_API_KEY` | `""` | str |
| `elevenlabs_api_key` | `ELEVENLABS_API_KEY` | `""` | str |
| `elevenlabs_voice_id` | `ELEVENLABS_VOICE_ID` | `""` | str |
| `xai_api_key` | `XAI_API_KEY` | `""` | str |
| `audio_device_index` | `AUDIO_DEVICE_INDEX` | `"0"` | **str** (not int) |
| `output_audio_device` | `OUTPUT_AUDIO_DEVICE` | `default` | str |
| `input_mode` | `INPUT_MODE` | `text` | str (lowercased) |
| `stt_mode` | `STT_MODE` | `deepgram` | str (lowercased) |
| `tts_mode` | `TTS_MODE` | `dry-run` | str (lowercased) |
| `deepgram_model` | `DEEPGRAM_MODEL` | `nova-3` | str |
| `elevenlabs_model` | `ELEVENLABS_MODEL` | `eleven_flash_v2_5` | str |
| `xai_stt_language` | `XAI_STT_LANGUAGE` | `en` | str (`or "en"`) |
| `xai_tts_voice` | `XAI_TTS_VOICE` | `eve` | str (`or "eve"`) |
| `xai_tts_language` | `XAI_TTS_LANGUAGE` | `en` | str (`or "en"`) |
| `audio_sample_rate` | `AUDIO_SAMPLE_RATE` | `16000` | int |
| `audio_channels` | `AUDIO_CHANNELS` | `1` | int |
| `confirm_transcript` | `CONFIRM_TRANSCRIPT` | `True` | bool |
| `provider_timeout_seconds` | `PROVIDER_TIMEOUT_SECONDS` | `60` | int |
| `provider_max_retries` | `PROVIDER_MAX_RETRIES` | `1` | int |
| `openai_api_mode` | `OPENAI_API_MODE` | `responses` | str (lowercased) |
| `playback_mode` | `PLAYBACK_MODE` | `file-only` | str (lowercased) |
| `elevenlabs_output_format` | `ELEVENLABS_OUTPUT_FORMAT` | `mp3_22050_32` | str |
| `elevenlabs_stability` | `ELEVENLABS_STABILITY` | `0.45` | float |
| `elevenlabs_similarity_boost` | `ELEVENLABS_SIMILARITY_BOOST` | `0.80` | float |
| `elevenlabs_style` | `ELEVENLABS_STYLE` | `0.35` | float |
| `elevenlabs_speed` | `ELEVENLABS_SPEED` | `1.0` | float |
| `realtime_model` | `REALTIME_MODEL` | `gpt-realtime` | str |
| `realtime_voice` | `REALTIME_VOICE` | `marin` | str |
| `realtime_transcription_model` | `REALTIME_TRANSCRIPTION_MODEL` | `gpt-4o-transcribe` | str |
| `realtime_vad_mode` | `REALTIME_VAD_MODE` | `semantic_vad` | str (lowercased) |
| `realtime_sample_rate` | `REALTIME_SAMPLE_RATE` | `24000` | int |

Derived properties (no state): `prompts_dir`, `sessions_dir`, `audio_input_dir`,
`audio_output_dir` (these last two are the **global** `root/audio/input` and
`root/audio/output` — note these differ from the **session-scoped** dirs that
`ConversationMemory` actually uses; see §2), plus a set of `uses_*` / `is_*`
booleans driving validation.

### Env parsing helpers ([settings.py:30-56](../../../config/settings.py))

- `_getenv(name, default)` → `os.getenv(name, default).strip()`.
- `_getenv_int` / `_getenv_float` → parse, raise `SettingsError` on `ValueError`.
- `_getenv_bool` → accepts `1/true/yes/y/on` and `0/false/no/n/off`; raises otherwise.

> **Gotcha:** `os.getenv(name, default)` only uses `default` when the variable is
> **unset**. A variable set to the **empty string** (e.g. a bare `AUDIO_SAMPLE_RATE=`
> line in `.env`) is returned as `""` and then fails `int("")` →
> `SettingsError`, instead of falling back to the default. See Known Issues #2.

### Validation rules

`validate_runtime()` ([settings.py:224](../../../config/settings.py)) runs:
1. `validate_for_active_provider()` — **only if not realtime**.
2. `validate_audio_modes()` — always.

**`validate_for_active_provider`** ([settings.py:156](../../../config/settings.py)):
- If `is_dry_run` (`active_llm ∈ {dry-run, dry_run, local}`) → return (no key needed).
- Else `active_llm` must be one of `anthropic|openai|google`, and the matching
  `*_API_KEY` must be non-empty. It validates the **key**, never the model ID.

**`validate_audio_modes`** ([settings.py:174](../../../config/settings.py)) — the cross-field rules:
- `conversation_mode ∈ {dry-run, chained, realtime}`.
- `input_mode ∈ {text, mic}`; `stt_mode ∈ {deepgram, xai}`.
- `tts_mode` must be a dry-run / elevenlabs / xai variant.
- `openai_api_mode ∈ {responses, chat}`; `playback_mode ∈ {file-only, sdk, system}`.
- `audio_sample_rate > 0`, `audio_channels > 0`, `provider_timeout_seconds > 0`, `provider_max_retries >= 0`.
- **dry-run constraint:** `conversation_mode == "dry-run"` requires `is_dry_run AND uses_text_input AND uses_dry_run_tts`. (So dry-run mode forbids mic/live TTS.)
- **realtime branch** (`uses_realtime`): requires `openai_api_key`, requires `input_mode == mic`, `realtime_vad_mode ∈ {semantic_vad, server_vad}`, `realtime_sample_rate == 24000`, then **`return`s early** — skipping all STT/TTS-key checks below.
- **mic STT keys:** mic + deepgram → `DEEPGRAM_API_KEY`; mic + xai → `XAI_API_KEY`.
- **TTS keys:** elevenlabs → `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`; xai → `XAI_API_KEY`.

`load_settings(root_dir=None, validate=True)`
([settings.py:235](../../../config/settings.py)) builds the dataclass from env
and calls `validate_runtime()` unless `validate=False` (the `--doctor` path
loads with `validate=False` so the doctor can *report* rather than *raise*).

---

## 2. Session persistence — the recording source of truth

[`ConversationMemory`](../../../pipeline/memory.py) is a mutable `@dataclass`
that owns the session JSON and the per-session media directories.

### Identity & paths ([`__post_init__`, memory.py:41](../../../pipeline/memory.py))

- `episode_name` is sanitized by `_safe_episode_name` (regex `[^A-Za-z0-9_.-]+`→`_`, stripped of leading/trailing `._`, falls back to `default`).
- `sessions_dir` defaults to `PROJECT_ROOT/sessions`; created with `mkdir(parents=True, exist_ok=True)`.
- `root_dir` inferred via `_infer_root_dir` (parent of `sessions_dir` if it is named `sessions`, else the dir itself).
- **`session_id`** (when not resuming): `f"{episode_name}_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:8]}"` — UTC timestamp **with microseconds** plus an 8-char uuid suffix.
- **`session_file`**: `sessions_dir/{session_id}.json`.
- **Media dirs are session-scoped:** `audio_input_dir` = `root/audio/{session_id}/input`, `audio_output_dir` = `root/audio/{session_id}/output`. (Differs from `Settings.audio_input_dir`/`audio_output_dir`, which are the non-scoped legacy `root/audio/input`.)

### Session JSON schema (written by [`_save`, memory.py:199](../../../pipeline/memory.py))

```jsonc
{
  "episode":      "pilot",                 // sanitized episode name
  "session_id":   "pilot_YYYYMMDD_HHMMSS_ffffff_xxxxxxxx",
  "next_turn_id": 3,                        // monotonic id reservation counter
  "saved_at":     "2026-...T...+00:00",     // ISO-8601 UTC, rewritten each save
  "history": [
    { "role": "user|assistant",
      "content": "trimmed text",
      "created_at": "ISO-8601",
      "metadata": { "turn_id": 0, "status": "...", "audio_path": "...", ... } }
  ],
  "events": [
    { "stage": "...", "status": "...", "created_at": "ISO", "turn_index": 0, "details": {...} }
  ],
  "artifacts": { "input_wav": [...], "output_wav": [...], "output_mp3": [...], "dryrun_text": [...] }
}
```

### Lifecycle

**Create:** `ConversationMemory(episode_name, sessions_dir=..., root_dir=...)`.
No file is written until the first `_save()` (triggered by `add`,
`reserve_turn_id`, `add_event`, `update_turn_metadata`, or
`order_realtime_transcripts`).

**Append** (`add`, [memory.py:115](../../../pipeline/memory.py)): validates
`role ∈ {user, assistant}` and non-empty `content`; appends
`{role, content (stripped), created_at}` (+ optional `metadata`); registers any
path-like metadata as an artifact; calls `_trim()` then `_save()`.

**Turn-id reservation** (`reserve_turn_id`,
[memory.py:142](../../../pipeline/memory.py)): returns the current
`next_turn_id_value`, increments it, then `_save()`s. The CLI reserves **one id
per loop iteration** (before knowing whether the turn will be used), so skipped /
empty / quit iterations still consume an id (gaps in turn numbering — see Known
Issues #9).

**Metadata update** (`update_turn_metadata`,
[memory.py:148](../../../pipeline/memory.py)): scans `history` **in reverse** for
the first turn matching `role` **and** `metadata.turn_id == turn_id`; merges new
metadata; `_save()`s. Raises `ValueError` if no match (e.g. the turn was trimmed
away — see Known Issues #6).

**Event log** (`add_event`, [memory.py:174](../../../pipeline/memory.py)):
appends `{stage, status, created_at, [turn_index], [details]}` and `_save()`s.
Events are append-only and ordered by insertion (not re-sorted).

**Atomic write** (`_save`, [memory.py:210-212](../../../pipeline/memory.py)):
```python
temporary_path = self.session_file.with_name(f".{self.session_file.name}.{uuid4().hex}.tmp")
temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
os.replace(temporary_path, self.session_file)
```
Temp file is in the **same directory** (same filesystem), so `os.replace` is an
atomic rename on both POSIX and Windows. **The whole payload is rewritten on
every save** (O(n) per save, O(n²) over a session). `json.dumps` has no
`default=` / `ensure_ascii` override (unicode is escaped to `\uXXXX`; a
non-serializable value in metadata/details would raise — see Known Issues #5).

### Resume paths

- **`from_session_file(path, ...)`** ([memory.py:63](../../../pipeline/memory.py)):
  reads the JSON, requires a non-empty `episode`, validates `history` is a list
  (raises otherwise), tolerates a non-list `events` (resets to `[]`),
  reconstructs `session_id` (`payload.session_id` or the file `stem`), and
  recovers `next_turn_id` via `_next_turn_id_from_payload`. Reuses the same
  `session_file` and (derived) session-scoped audio dirs, so resumed turns get
  **higher** indices and do not overwrite prior media.
- **`latest_for_episode(name, sessions_dir, ...)`** ([memory.py:100](../../../pipeline/memory.py)):
  `sorted(glob(f"{safe_name}_*.json"))[-1]` — picks the **lexicographically
  last** matching file as "latest". Because the timestamp format is fixed-width
  and zero-padded, lexical order == chronological **as long as the wall clock is
  monotonic** (see Known Issues #8).
- **`_next_turn_id_from_payload`** ([memory.py:275](../../../pipeline/memory.py)):
  prefer stored `next_turn_id` (if int ≥ 0); else `max(event.turn_index)+1`; else
  count assistant turns in history.

### Artifacts

`register_artifact(path, kind=None)` ([memory.py:217](../../../pipeline/memory.py))
relativizes the path to `root_dir` (`_relative_path`, falls back to the absolute
string if not under root) and classifies it by suffix (`_artifact_kind`: `.wav`
→ `input_wav`/`output_wav` by whether `/input/` is in the path, `.mp3` →
`output_mp3`, `.txt` → `dryrun_text`; anything else is dropped). `artifacts()`
returns sorted-unique lists. `_register_paths` auto-registers any metadata/detail
value whose **key contains "path"** and whose value is a `str`.

---

## 3. Preflight (`--doctor`) — [pipeline/preflight.py](../../../pipeline/preflight.py)

`run_preflight(settings)` returns `{"ok": bool, "checks": [...]}` where `ok` is
true iff no check has `status == "error"`. Checks performed:

| Check | What it verifies |
|---|---|
| `settings` | `settings.validate_runtime()` does not raise. |
| `base_prompt` | `config/prompts/base_system.txt` exists and is non-empty. |
| `sessions`, `audio_input`, `audio_output`, `exports` | Each dir is creatable + a `.write_test` file can be written and unlinked. **Note:** uses `settings.audio_input_dir`/`audio_output_dir` — the **non-session-scoped** dirs, not the ones a real recording uses. |
| `sdk:<pkg>` | Provider SDK import availability via `importlib.util.find_spec`, gated on `active_llm` + modes (anthropic/openai/google-genai; sounddevice/soundfile/numpy for mic; websockets for realtime; deepgram-sdk or requests for STT; elevenlabs/requests for TTS). |
| `audio_device` (mic only) | `list_input_devices()` succeeds and the configured `AUDIO_DEVICE_INDEX` matches a device **index or name** (or is `default`). |

**Does NOT check:** network / API-key validity (no live ping), disk free space,
the **output** audio device (realtime playback), microphone sample-rate
compatibility, write access to the **session-scoped** audio dirs that recording
actually uses, or model-ID validity. See Known Issues #11.

`format_preflight_report` renders a human report ending in `Result: OK|FAILED`.

---

## 4. Reliability — [pipeline/reliability.py](../../../pipeline/reliability.py)

- **`ProviderCallError`** — a `@dataclass`-decorated `RuntimeError` carrying
  `provider/stage/attempts/original_error`, with a custom `__str__` and
  `to_event()`. The custom `__str__` *is* preserved through the dataclass
  decorator (verified), so `str(err)` is informative. (Side effects of the
  decorator: `__eq__` is generated so instances are **unhashable**, and
  `RuntimeError.__init__` is **not** called so `err.args == ()` — cosmetic.)
- **`retry_call(operation, *, provider, stage, max_retries, timeout_seconds, retry_exceptions=(Exception,), retry_predicate=None, sleep_fn=time.sleep, backoff_seconds=0.5)`**
  ([reliability.py:33](../../../pipeline/reliability.py)): runs `operation()` up
  to `max_retries + 1` times; on a caught exception, stops early if
  `attempt >= attempts` **or** `retry_predicate(exc)` is false; sleeps
  `backoff_seconds * attempt` (linear, not exponential) between tries; on
  exhaustion raises `ProviderCallError`. **It discards `timeout_seconds`**
  (`del timeout_seconds`, [line 51](../../../pipeline/reliability.py)) — it
  enforces no wall-clock timeout; timeout is delegated to whichever SDK client
  received it (Google's client never does — see 03-llm-pipeline). See Known
  Issues #10.
- **`is_transient_provider_error(exc)`** ([reliability.py:73](../../../pipeline/reliability.py)):
  `TimeoutError`/`ConnectionError` → transient; else inspects `exc.status_code`
  or `exc.response.status_code` for `{408,409,429}` or `>= 500`. The mixed
  `in {…} or isinstance() and …` expression parses correctly (verified).
- **`structured_error(exc, stage)`** ([reliability.py:83](../../../pipeline/reliability.py)):
  for a `ProviderCallError` returns its `to_event()` with `stage` overlaid; else
  `{stage, error, type}`. Used by the CLI to write failure events.

---

## 5. CLI orchestration — [main.py](../../../main.py)

### Flags ([main.py:182](../../../main.py))

| Flag | Effect |
|---|---|
| `episode_name` (positional, default `default`) | Episode / prompt / session name. |
| `--resume` | Resume the latest session for this episode. |
| `--session <file>` | Resume a specific session JSON. |
| `--doctor` | Run preflight and exit (`0` if OK else `1`). Loads settings with `validate=False`. |
| `--list-devices` | Print input devices and exit `0`. |
| `--confirm-transcript` / `--no-confirm-transcript` | Override `CONFIRM_TRANSCRIPT` (mic mode). Both set → "no" wins (the `if args.no_confirm_transcript` check runs last). |
| `--max-turns N` | Stop after N **completed** new turns. |

`main()` dispatch order: `--list-devices` → load settings → `--doctor` →
compute confirm override → if `uses_realtime` run `run_realtime_episode`
(async) else `run_episode`. A top-level
`except (FileNotFoundError, ValueError, RuntimeError)` prints `Error: …` to
stderr and returns `1`. `KeyboardInterrupt` is **not** caught (Ctrl-C →
traceback + non-zero exit; the per-turn save means partial state is on disk).

> **Realtime ignores `--max-turns` and `--confirm-transcript`:** main only
> forwards `resume`/`session_path` to `run_realtime_episode`. See Known Issues #13.

### `run_episode` loop ([main.py:22-130](../../../main.py))

```
load/override settings → mkdir sessions → build/resume ConversationMemory
mkdir session audio dirs → load_system_prompt → completed_turns = 0
loop:
  if max_turns is not None and completed_turns >= max_turns: print "Max turns reached"; break
  turn_id = memory.reserve_turn_id()                       # consumes an id every iteration
  try: host_text, user_metadata = _capture_host_turn(...)  # text or mic+confirm
  except Exception: add_event("host_turn","failed"); print; break   # ← any capture error ENDS the episode
  if host_text is None:           add_event("turn","skipped");        continue
  if host_text.lower() in {q,quit,end}: add_event("episode","ended"); break
  if not host_text:               add_event("turn","skipped_empty");  continue
  memory.add("user", host_text, metadata={..., turn_id})
  try: ai = call_llm(...); add_event("llm_completed","ok"); memory.add("assistant", ai, {status:tts_pending,turn_id})
  except Exception: add_event("llm_completed","failed"); print; break
  try: path = speak(ai, turn_id, ...); update_turn_metadata("assistant", turn_id, status=tts_saved[, audio_path]); add_event("tts_saved","ok")
  except Exception: update_turn_metadata("assistant", turn_id, status="tts_failed"); add_event("tts_saved","failed"); print; break
  add_event("turn","complete"); completed_turns += 1
```

Key behaviors:
- **Save-after-every-step:** each `add`/`reserve`/`update`/`add_event` persists, so
  a crash leaves a coherent on-disk session up to the last completed step.
- **Any LLM or TTS exception `break`s the loop** (ends the episode) rather than
  skipping the turn — by design the AI text is preserved before TTS, and TTS
  failure keeps the assistant turn with `status=tts_failed`. But it means a
  single transient failure ends the whole recording (resume required). See Known
  Issues #12.
- **Host-turn capture exceptions also `break`** — a mic glitch / STT error ends
  the episode instead of letting the operator retry. See Known Issues #12.

### `_capture_host_turn` ([main.py:133](../../../main.py))

- **Text mode:** returns `capture_text_turn(input_fn).strip()` (a string;
  never `None`). So text mode can only *exit* (q/quit/end) or *skip-empty*.
- **Mic mode:** loop: record WAV → `add_event(recording_saved)` → `transcribe`
  → `add_event(transcribed)`. If `confirm_transcript` is off → return transcript.
  Else prompt `[Enter=accept, r, e, s, q]`: Enter→accept, `r`→re-record (continue),
  `e`→edit (empty edit → re-record), `s`→return `None` (skip), `q`→return `"q"`
  (→ episode ends via the exit-command check), unknown→re-prompt.

---

## 6. Config knobs cheat-sheet (operational)

| Knob | Where it matters |
|---|---|
| `CONVERSATION_MODE` | `dry-run` (offline) / `chained` (STT→LLM→TTS) / `realtime` (OpenAI speech-to-speech, bypasses `run_episode`). |
| `ACTIVE_LLM` + `ACTIVE_MODEL` | Provider + model id. Model id is **never validated** (default `dry-run-v1` is a placeholder). |
| `INPUT_MODE` | `text` vs `mic`. |
| `STT_MODE` / `TTS_MODE` | deepgram/xai · dry-run/elevenlabs/xai. |
| `CONFIRM_TRANSCRIPT` | mic transcript review gate. |
| `PROVIDER_TIMEOUT_SECONDS` / `PROVIDER_MAX_RETRIES` | Passed to SDK clients + `retry_call` budget (timeout discarded by `retry_call`). |
| `AUDIO_DEVICE_INDEX` / `AUDIO_SAMPLE_RATE` / `AUDIO_CHANNELS` | mic capture (chained). |
| `REALTIME_*` | realtime model/voice/VAD/transcription/sample-rate (must be 24000). |
| `PLAYBACK_MODE` | file-only / sdk / system (system playback is macOS-only). |

---

## 7. Known Issues

Severity in parentheses; concrete fixes are in the audit issue list.

1. **(P1) Concurrent runs of the same episode silently clobber a session.**
   Two processes that `--resume` (or `--session`) the same file each rewrite the
   whole JSON via tmp+`os.replace` with no file lock → last-writer-wins, lost
   turns. Even fresh runs share the `audio/<session_id>/…` tree only if
   session_ids collide (they don't), but resumed concurrent runs do overwrite.
   ([memory.py:199](../../../pipeline/memory.py))

2. **(P1) Empty-string env vars crash startup instead of using defaults.**
   A bare `AUDIO_SAMPLE_RATE=` / `PROVIDER_MAX_RETRIES=` / `CONFIRM_TRANSCRIPT=`
   line in `.env` is read as `""` and fails int/float/bool parsing with a
   `SettingsError`, because `os.getenv` only applies the default when the var is
   *unset*. ([settings.py:34-56](../../../config/settings.py))

3. **(P1) `.gitignore` no longer covers session-scoped audio.** `.gitignore`
   ignores `audio/input/*` and `audio/output/*`, but recordings now live under
   `audio/<session_id>/input/…` and `audio/<session_id>/output/…`, which those
   patterns do **not** match. Real recordings can be accidentally committed.
   ([.gitignore](../../../.gitignore), [memory.py:55-61](../../../pipeline/memory.py))

4. **(P1) Doctor checks the wrong audio dirs.** Preflight writes test files to
   `settings.audio_input_dir`/`audio_output_dir` (the non-session-scoped legacy
   dirs), not the `audio/<session_id>/…` dirs an actual recording writes to. A
   passing doctor does not prove the real recording target is writable.
   ([preflight.py:16-17](../../../pipeline/preflight.py), [memory.py:55-61](../../../pipeline/memory.py))

5. **(P2) Non-serializable / unicode-escaped session JSON.** `_save` calls
   `json.dumps(payload, indent=2)` with no `default=` and no
   `ensure_ascii=False`. A non-serializable value placed in `metadata`/`details`
   raises mid-save (and is caught by the CLI's broad except → ends the episode);
   unicode is written as `\uXXXX` (bloats the transcript file).
   ([memory.py:211](../../../pipeline/memory.py))

6. **(P2) `update_turn_metadata` raises if the target turn was trimmed.**
   When `len(history) > max_turns*2`, `_trim` drops old turns; a later
   `update_turn_metadata(role, turn_id)` for a trimmed turn raises `ValueError`,
   which the CLI's TTS/host except blocks would turn into a loop-ending error.
   Not hit by the current loop (it always updates the just-added assistant turn,
   which fits the cap), but the API is unsafe for any non-trivial `max_turns`.
   ([memory.py:148-157](../../../pipeline/memory.py), [memory.py:194-197](../../../pipeline/memory.py))

7. **(P2) Full-payload rewrite every save (O(n²) I/O).** Every turn and every
   event rewrites the entire growing session JSON. A long (2-hour, many-event)
   recording does quadratic disk work and disk-flash wear; also widens the
   tmp+replace window. ([memory.py:199-212](../../../pipeline/memory.py))

8. **(P2) `latest_for_episode` trusts lexical filename order == chronological.**
   It depends on a monotonic wall clock. An NTP correction / DST / manual clock
   change that moves time backward can make a newer session sort *before* an
   older one, so `--resume` reattaches to the wrong session. Sorting by
   `st_mtime` (or embedded timestamp) would be safer. ([memory.py:110](../../../pipeline/memory.py))

9. **(P3) Turn ids leak on skipped/empty/quit iterations.** `reserve_turn_id`
   runs at the top of every loop iteration, so empty/skipped/exit turns still
   bump `next_turn_id`. After resume, audio filenames (`turn_<n>.wav/.mp3`) and
   `turn_id`s have gaps — confusing but not data-loss. ([main.py:65](../../../main.py), [memory.py:142](../../../pipeline/memory.py))

10. **(P2) `retry_call` advertises a timeout it discards; backoff is linear.**
    `timeout_seconds` is `del`-eted, so no wall-clock bound is enforced by the
    wrapper; combined with SDKs that don't receive a timeout (Google) a call can
    hang the whole episode. Backoff is `backoff_seconds*attempt` (linear), not
    exponential/jittered. ([reliability.py:51,63](../../../pipeline/reliability.py))

11. **(P2) Doctor has no liveness / capacity checks.** No API-key validity ping,
    no disk-space check, no output-device check (realtime playback), no
    sample-rate-vs-device compatibility check. A doctor "OK" can still fail a
    real recording on a bad key, full disk, or unusable output device.
    ([preflight.py:11-28](../../../pipeline/preflight.py))

12. **(P2) Any host-capture / LLM / TTS exception ends the whole episode.**
    The loop `break`s on the first exception in capture, `call_llm`, or `speak`
    rather than offering a retry/skip. A single transient provider blip forces a
    `--resume`. Mid-turn the assistant text is preserved (good), but the recording
    session is over. ([main.py:74-77,101-104,121-125](../../../main.py))

13. **(P2) Realtime path ignores `--max-turns` and transcript confirmation, and
    bypasses `validate_for_active_provider`.** `main` forwards only
    `resume`/`session_path` to `run_realtime_episode`, so `--max-turns` /
    `--confirm-transcript` are silently dropped in realtime; and
    `validate_runtime` skips provider validation for realtime (benign, since
    realtime ignores `ACTIVE_LLM`, but worth noting). ([main.py:215-223](../../../main.py), [settings.py:224-227](../../../config/settings.py))

14. **(P3) Orphan temp files accumulate on crash.** A crash between the temp
    write and `os.replace` (in `_save`, `record_until_keypress`, `tts`,
    `realtime`) leaves `.{name}.{uuid}.tmp(.wav)` files in `sessions/` and the
    audio dirs. They don't break the glob (it matches `*_*.json`) but are never
    cleaned up. ([memory.py:210](../../../pipeline/memory.py))

15. **(P3) `KeyboardInterrupt` mid-turn is uncaught.** Ctrl-C produces a
    traceback rather than a graceful "session saved" message. Per-turn saves mean
    the JSON is coherent, but an in-progress `reserve_turn_id`/`add` may leave a
    reserved-but-unused id or a user turn without its assistant reply.
    ([main.py:182-236](../../../main.py))

16. **(P3) Base prompt references an unfinished name.** `base_system.txt` still
    says "a podcast called [PODCAST NAME TBD]"; the model will read the literal
    placeholder aloud. ([config/prompts/base_system.txt:1](../../../config/prompts/base_system.txt))

17. **(P3) Settings/doc default drift.** `ACTIVE_MODEL=dry-run-v1` is a
    placeholder (not a real id for any live provider); `OUTPUT_AUDIO_DEVICE` and
    several realtime knobs are documented in some flows but not others. Doctor
    never flags an unset/placeholder model. ([settings.py:63](../../../config/settings.py), [.env.example:10](../../../.env.example))

---

## 8. Test coverage notes

- **Settings:** good matrix for provider-key, mic STT (deepgram/xai), TTS
  (elevenlabs/xai), realtime requirements, doctor-skip. **Gaps:** no test for
  empty-string env vars (Known Issues #2), invalid int/float parse messages, the
  dry-run constraint rejection, or `with_overrides` re-validation failure.
- **Memory:** covers add/trim/resume/`latest_for_episode`/artifacts/distinct
  media dirs/monotonic ids. **Gaps:** no crash-mid-write / atomicity test, no
  concurrent-writer test, no trimmed-turn `update_turn_metadata` failure, no
  non-serializable/unicode payload, no clock-rollback ordering, no orphan-temp
  assertion.
- **Preflight:** covers prompt/key/SDK/device/unwritable-dir/realtime-vs-deepgram.
  **Gaps:** does not assert it checks the *session-scoped* audio dirs (it
  doesn't), no liveness/disk checks tested.
- **Reliability:** covers retry/no-retry/structured error. **Gaps:** the
  discarded-timeout behavior is untested (no assertion that timeout is *not*
  enforced), backoff schedule untested, `is_transient_provider_error`
  status-code matrix untested.
- **CLI dry-run:** strong (dry-run, mic record/confirm/edit/skip/quit/re-record,
  resume, exact-session, llm-fail, tts-fail-keeps-text, realtime dispatch,
  list-devices, bad-session). **Gaps:** no `--max-turns 0`, no host-capture
  exception path, no `KeyboardInterrupt`, no concurrent-resume.
