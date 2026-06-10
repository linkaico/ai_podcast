# P3 — Low (minor, cosmetic, nice-to-have, test depth)

16 issues. Safe to defer, but cheap to fold into the batch that already opens the file.

---

## LLM-07 — No Anthropic prompt caching; full prefix re-sent every turn
- **Batch:** C · **Location:** [pipeline/llm.py:104](../../pipeline/llm.py#L104) · **Source:** [03](../context/03-llm-pipeline/README.md)
- **Problem:** The stable system prompt + growing history is re-sent every turn with no `cache_control`.
  On Anthropic, caching the frozen prefix would cut input cost ~90% across a multi-turn episode; over a
  long recording it's pure wasted spend.
- **Fix:** Send `system` as a content block with `cache_control: {"type": "ephemeral"}` (the system prompt
  is already byte-stable, built once per episode).

## LLM-08 — LLM tests are happy-path only
- **Batch:** C · **Location:** [tests/test_llm.py:44-139](../../tests/test_llm.py) · **Source:** [03](../context/03-llm-pipeline/README.md)
- **Problem:** Tests cover dry-run, OpenAI responses+chat, Google via well-formed fakes. Not covered: the
  Anthropic path (zero tests), empty/blocked → RuntimeError, the `_extract_openai_responses_text`
  multi-block/`output_text` fallback, transient-error retry, missing-key validation — the branches most
  likely to break live.
- **Fix:** Add fakes for Anthropic success + multi-block join; empty → RuntimeError; a 429-like raise →
  retried then `ProviderCallError`; the responses `output[]` fallback; `validate_for_active_provider`
  raising on a missing key.

## RT-07 — Windows ENTER-stop uses a non-cancellable `to_thread(input)`
- **Batch:** B · **Location:** [pipeline/realtime.py:296-312](../../pipeline/realtime.py#L296-L312) · **Source:** [06](../context/06-realtime/README.md)
- **Problem:** `loop.add_reader(sys.stdin, …)` isn't implemented on the Windows ProactorEventLoop, so the
  stopper always takes the `asyncio.to_thread(input_fn, "")` fallback. That blocked thread can't be
  cancelled — `stopper.cancel()` returns but the `input()` thread keeps blocking until the next ENTER or
  process exit, lingering after the session ends. Ctrl-C during the blocked `input()` is awkward on win32.
- **Fix:** Use a daemon stdin thread that sets an `asyncio.Event` via `call_soon_threadsafe` (dies with the
  process); ensure the outer `asyncio.run` catches `KeyboardInterrupt` so the `finally` (WAV publish) runs.

## RT-08 — Default realtime model/transcription ids may age out
- **Batch:** B · G · **Location:** [config/settings.py:94-96](../../config/settings.py#L94-L96); `realtime.py:26,32` · **Source:** [06](../context/06-realtime/README.md)
- **Problem:** Defaults `realtime_model="gpt-realtime"`, `realtime_transcription_model="gpt-4o-transcribe"`
  are valid GA ids today, but newer models exist (e.g. `gpt-realtime-2`, streaming
  `gpt-realtime-whisper`). If `gpt-realtime` is ever retired or not enabled on the account, connect fails.
  An availability/maintenance risk, not a bug today.
- **Fix:** Keep overridable (already is). Add a doctor/preflight check that the configured realtime model
  is available on the key (lands in G); consider bumping defaults after verifying account access.

## AUD-08 — Multichannel capture not signaled to Deepgram
- **Batch:** D · **Location:** [pipeline/stt.py:72-78](../../pipeline/stt.py#L72-L78), `:224-229` · **Source:** [04](../context/04-audio-io/README.md)
- **Problem:** If `AUDIO_CHANNELS>1`, a multichannel WAV is written but the Deepgram call omits
  `multichannel=true`, so it's treated as a single downmixed channel. Harmless for the intended mono mic,
  but a stereo config silently loses per-channel transcription.
- **Fix:** Pass `multichannel=True` when `settings.audio_channels > 1`, or document mono-only as the
  supported configuration.

## AUD-09 — Audio tests mock everything; no real WAV/device/playback coverage
- **Batch:** D · **Location:** [tests/test_stt.py](../../tests/test_stt.py), [tests/test_tts.py](../../tests/test_tts.py) · **Source:** [04](../context/04-audio-io/README.md)
- **Problem:** Coverage is shape-of-call only against fakes. Untested: `record_until_keypress`,
  `_input_device` parsing, a real int16→PCM_16 WAV round-trip, `_coerce_audio_bytes` with a true
  generator, `_elevenlabs_extension` rejection, and every `_try_play_audio` mode including the Windows
  `_system_play` failure (the macOS/Windows divergence has no test).
- **Fix:** Unit tests for `_input_device` (`""`/`"default"`/`"5"`/`"Mic Name"`), a real soundfile WAV
  round-trip on a synthesized int16 array, `_coerce_audio_bytes` (generator + empty chunks),
  `_elevenlabs_extension` unsupported-format raise, and `_system_play`/`_try_play_audio` per platform
  (monkeypatch `sys.platform`).

## INF-13 — Turn ids leak on skipped/empty/quit iterations
- **Batch:** F · **Location:** [main.py:65](../../main.py#L65) (`reserve_turn_id` at loop top); `memory.py:142-146` · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** `reserve_turn_id` runs at the top of every iteration before the turn is known usable. Empty
  turns, mic skips, and quit all consume an id (and save it). Reproduced: two empty turns + quit advanced
  `next_turn_id` to 3 with zero real turns — gaps in turn numbering and `turn_<n>` filenames after resume
  (not data-loss; ids stay monotonic).
- **Fix:** Reserve the id only after deciding the turn is real (move below the None/exit/empty checks), or
  return a provisional id and commit only on use.

## INF-14 — Orphan `.tmp` files accumulate on crash
- **Batch:** E · **Location:** [pipeline/memory.py:210](../../pipeline/memory.py#L210); `stt.py:89`; `tts.py:209/215`; `realtime.py:337` · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Each atomic writer creates `.{name}.{uuid}.tmp` then `os.replace`. A crash/SIGKILL/Ctrl-C
  between write and replace leaves the temp behind. Nothing cleans them; in `sessions/` they're untracked
  (the `sessions/*.json` ignore doesn't match the dotted temp name) and clutter the dirs.
- **Fix:** Sweep stale `.<…>.tmp*` files on `ConversationMemory` init, or write temps to a gitignored
  `tmp/` subdir that's periodically purged.

## INF-15 — `KeyboardInterrupt` mid-turn dumps a traceback; partial turn on resume
- **Batch:** F · **Location:** [main.py:194-236](../../main.py#L194-L236) (handler catches only `FileNotFoundError`/`ValueError`/`RuntimeError`) · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Ctrl-C during recording raises `KeyboardInterrupt`, uncaught → traceback. Per-turn saves
  keep the JSON coherent, but an interrupt right after `reserve_turn_id` (before `add`) leaves a
  reserved-but-unused id, and one between the user `add` and the assistant `add` leaves a user turn with
  no reply — an inconsistent partial turn on resume.
- **Fix:** Catch `KeyboardInterrupt` in `main` (and the loop) → "Session saved — stopping." with a clean
  exit; ensure the in-flight turn is completed or rolled back so resume starts clean.

## INF-16 — Base prompt still says "[PODCAST NAME TBD]"
- **Batch:** A · **Location:** [config/prompts/base_system.txt:1](../../config/prompts/base_system.txt#L1) · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Line 1: `You are the AI co-host of a podcast called [PODCAST NAME TBD].` In a live
  realtime/TTS recording the model will speak/reference the literal placeholder — embarrassing on a real
  recording. Nothing flags it.
- **Fix:** Replace with the real podcast name (or remove the clause). Optionally add a doctor warning if
  the base prompt still contains `TBD`/`[`.

## INF-18 — `from_session_file` derives the audio dir from the filename stem
- **Batch:** E · **Location:** [pipeline/memory.py:92](../../pipeline/memory.py#L92) (`session_id = payload.get("session_id") or session_path.stem`); `:55-61` · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Resume falls back to the file `stem` as `session_id` when the payload lacks one (externally
  created / hand-edited / renamed file). Because session-scoped audio dirs derive from `session_id`, a
  renamed file points new recordings at `audio/<new-stem>/…` while the original media sits under
  `audio/<original-id>/…` — new turns split from the original tree, and `--session <renamed>` won't find
  the prior audio.
- **Fix:** Prefer the embedded `session_id`; if missing, persist one on first save rather than deriving
  from the mutable filename, and warn if `stem != stored session_id`.

## OPS-08 — `rich` unused; `pytest` mixed into runtime requirements
- **Batch:** J · **Location:** [requirements.txt:11](../../requirements.txt#L11) (`rich`), `:13` (`pytest`) · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** No `import rich` anywhere — dead weight. `pytest` is a test-only dependency listed in the
  main runtime requirements with no dev separation.
- **Fix:** Remove `rich` (or start using it); move `pytest` to `requirements-dev.txt` / a dev extra.

## OPS-09 — No LICENSE file / license undeclared
- **Batch:** I · **Location:** repo root (no `LICENSE*`); `README.md` (no license section) · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** No LICENSE and no license mentioned. By default this is "all rights reserved," which may be
  unintended for a project meant to be set up/shared.
- **Fix:** Add an explicit LICENSE (or a "Proprietary — all rights reserved" note) reflecting intent.

## OPS-11 — `load_system_prompt` doesn't sanitize `episode_name`
- **Batch:** H · **Location:** [pipeline/llm.py:24-27](../../pipeline/llm.py#L24-L27) (raw name); `openclaw_tools.py:49-52` (writes via `safe_episode_name`) · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** `write_episode_context` writes `config/prompts/episodes/<safe_name>.txt`, but
  `load_system_prompt` reads `<episode_name>.txt` using the raw name. Works today only because the live
  loop passes the already-sanitized `memory.episode_name`. Any caller passing a raw name directly looks
  for a different filename than was written (silent prompt-not-found, no error).
- **Fix:** Sanitize `episode_name` inside `load_system_prompt` with `safe_episode_name` so read matches
  write regardless of caller.

## OPS-12 — OpenClaw surface has thin test coverage
- **Batch:** H · **Location:** [tests/test_openclaw_tools.py](../../tests/test_openclaw_tools.py) (4 tests) · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** Tests cover write/list/load/latest/artifacts/export happy paths but omit `run_episode`
  entirely (the blocking function), all error paths (`ValueError` on empty content / non-markdown format,
  `FileNotFoundError`), and the absolute-path containment behavior — the riskiest areas.
- **Fix:** Add tests for `run_episode` with an injected `input_fn`/settings, the `ValueError`/
  `FileNotFoundError` paths, and a path-containment negative test (once OPS-02 lands).

## EXE-06 — Non-ASCII device names render as mojibake in `--list-devices`
- **Batch:** J · **Location:** [pipeline/stt.py](../../pipeline/stt.py) `list_input_devices`, printed via `main.py:196-200` · **Source:** [07](../context/07-execution-report/README.md)
- **Problem:** On Windows, device names with umlauts ("Kopfhörer", "Mikrofon") print as `Kopfh�rer`
  because the console code page isn't UTF-8. Cosmetic; doesn't crash, exit 0.
- **Fix:** `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at startup, or note it as a known
  Windows console display artifact.
