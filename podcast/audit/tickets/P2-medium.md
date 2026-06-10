# P2 — Medium (survives the happy path, breaks under stress / quality gaps)

23 issues. Long sessions, network blips, refusals, concurrency, and trustworthy tooling.

---

## LLM-03 — Google client has no request timeout
- **Batch:** C · F · **Location:** [pipeline/llm.py:158](../../pipeline/llm.py#L158); `reliability.py:51` · **Source:** [03](../context/03-llm-pipeline/README.md)
- **Problem:** `genai.Client(api_key=…)` is built with no timeout, and `retry_call` discards
  `timeout_seconds`. Anthropic/OpenAI get `timeout=` via their factories; Google does not. A stalled
  Google request hangs `call_llm` (and the whole loop) indefinitely, with no retry trigger.
- **Fix:** Pass a timeout via `http_options` (google-genai timeout is in ms), e.g.
  `config={"system_instruction": …, "http_options": {"timeout": settings.provider_timeout_seconds*1000}}`,
  or solve globally via **INF-09** (enforced deadline in `retry_call`).

## LLM-04 — Hard 1024-token cap with no truncation detection
- **Batch:** C · **Location:** [pipeline/llm.py:106](../../pipeline/llm.py#L106), `:133`, `:140` · **Source:** [03](../context/03-llm-pipeline/README.md)
- **Problem:** Every live path caps output at 1024 tokens and never checks the stop reason. A long answer
  is truncated mid-sentence; the truncated text still passes the non-empty guard (`llm.py:53`) and is
  spoken/stored as if complete. A sentence cutting off silently is a real podcast-quality failure.
- **Fix:** Make the cap a setting (`provider_max_output_tokens`), raise the default, and detect
  truncation — Anthropic `stop_reason=="max_tokens"`, OpenAI `finish_reason=="length"`/incomplete,
  Google `finish_reason==MAX_TOKENS` — at minimum log/flag it.

## LLM-05 — Refusals/safety blocks collapse into a generic "empty response"
- **Batch:** C · **Location:** [pipeline/llm.py:53](../../pipeline/llm.py#L53), `:110`, `:171` · **Source:** [03](../context/03-llm-pipeline/README.md)
- **Problem:** Anthropic `stop_reason="refusal"`, an OpenAI refusal, or Google `finish_reason=SAFETY`
  all yield empty text → the catch-all `RuntimeError: "{provider} returned an empty response."` The
  operator sees a misleading "empty response" instead of "refused / blocked," slowing live debugging.
  Also, some google-genai versions raise/warn on `.text` for a blocked response — the bare
  `response.text or ""` doesn't defend against that.
- **Fix:** Inspect the stop/finish reason per provider before the empty-text fallback; raise distinct
  "refused"/"safety-blocked"/"empty" errors. For Google read `candidates[0].finish_reason`/
  `prompt_feedback` and guard `.text`.

## LLM-06 — Retry budget doubled with the SDKs' built-in retries; original error obscured
- **Batch:** C · F · **Location:** [pipeline/reliability.py:33](../../pipeline/reliability.py#L33); `llm.py:45` · **Source:** [03](../context/03-llm-pipeline/README.md)
- **Problem:** `retry_call` retries transient errors `max_retries+1` times, but each SDK already
  auto-retries 429/5xx (Anthropic/OpenAI default 2). Net transient retries `(max_retries+1)*(SDK+1)` —
  a rate-limited turn stalls far longer than expected. Permanent 400s are correctly not retried, but
  `ProviderCallError.__str__` buries the cause behind "failed after N attempt(s)".
- **Fix:** Keep one retry layer — pass `max_retries=0` to the Anthropic/OpenAI factories (wrapper
  authoritative) or drop the wrapper and rely on the SDKs. Preserve the underlying exception
  (`repr(last_error)`, re-chain with `from`).

## RT-03 — Manual `input_audio_buffer.commit` conflicts with VAD
- **Batch:** B · **Location:** [pipeline/realtime.py:255](../../pipeline/realtime.py#L255) · **Source:** [06](../context/06-realtime/README.md)
- **Problem:** On ENTER-stop the code sends `input_audio_buffer.commit`. With `semantic_vad`/`server_vad`
  the server commits automatically; an explicit commit on an empty/already-committed buffer returns a
  server `error` event ("buffer too small"), which flows into the processor's error branch (`:153-157`)
  and raises `RuntimeError` during shutdown — spurious and noisy.
- **Fix:** Don't send `commit` in VAD modes (let VAD finalize). For graceful shutdown, optionally send
  `response.cancel` if a response is active, then close.

## RT-04 — No mid-session network-drop / reconnect handling
- **Batch:** B · **Location:** [pipeline/realtime.py:287-289](../../pipeline/realtime.py#L287-L289), `:243` · **Source:** [06](../context/06-realtime/README.md)
- **Problem:** A network drop raises `ConnectionClosed` out of `async for raw_event in websocket`. There's
  no try/except/retry — the receiver dies, `asyncio.wait` returns, the session ends. The `finally` still
  publishes WAV stems (good), but the rest of the episode is lost and the exception may propagate as an
  unhandled error. Long recordings make this likely.
- **Fix:** Catch `websockets.exceptions.ConnectionClosed` in `_receive_events` → clean stop. Optionally
  reconnect-with-backoff (re-send `session.update`; note server-side conversation state is lost — at
  minimum keep recording the host WAV and inform the user).

## RT-05 — Mic frames silently dropped when the queue is full
- **Batch:** B · **Location:** [pipeline/realtime.py:356-359](../../pipeline/realtime.py#L356-L359), `:210` (`maxsize=256`) · **Source:** [06](../context/06-realtime/README.md)
- **Problem:** `_enqueue_audio` does `if queue.full(): return`, discarding the mic frame with no log or
  counter. The 256-slot queue fills whenever the sender lags — exactly what happens when the loop stalls
  on RT-02's blocking write. Dropped frames mean the model never hears part of what the host said, and
  corrupt `live_host.wav`.
- **Fix:** At minimum count/log drops (throttled). Better: raise the bound or apply real back-pressure
  via a lock-free ring buffer; and fix RT-02 so the sender isn't starved.

## RT-06 — Realtime connection + concurrency code is untested
- **Batch:** B · **Location:** [tests/test_realtime.py:1-127](../../tests/test_realtime.py) · **Source:** [06](../context/06-realtime/README.md)
- **Problem:** Tests cover only `RealtimeEventProcessor.handle` and the static `build_session_update`
  shape. Untested: the connect URL/headers (so RT-01 is invisible to the suite), sender/receiver/stopper
  orchestration + cancellation, stop-commit, error-event → RuntimeError, network drop, queue-full drops,
  WAV temp→publish. The most failure-prone code has zero coverage.
- **Fix:** Fake connector capturing URL+headers (assert `?model=`, `Authorization`, no `OpenAI-Beta`);
  fake websocket yielding a scripted event sequence to drive `run_realtime_episode` with injected
  `input_fn`/`output_fn` and fake sounddevice/soundfile; cover stop→commit, error raise,
  `ConnectionClosed`, WAV rename/registration.

## AUD-04 — Unbounded in-memory recording buffer
- **Batch:** D · **Location:** [pipeline/stt.py:61-84](../../pipeline/stt.py#L61-L84) · **Source:** [04](../context/04-audio-io/README.md)
- **Problem:** The capture callback appends every block to `audio_chunks` for the whole time the main
  thread blocks on `input_fn("")`, with no cap (~1.9 MB/min int16/mono/16 kHz), briefly doubled at
  `np.concatenate`. No max-seconds, no stop but ENTER.
- **Fix:** Track frames in the callback and `raise sd.CallbackStop` past a configurable
  `max_record_seconds`; free `audio_chunks` after concat. Even a generous default prevents pathological
  growth.

## AUD-05 — Deepgram legacy fallback branches would send no model/options
- **Batch:** D · **Location:** [pipeline/stt.py:138-149](../../pipeline/stt.py#L138-L149), `:215-240` · **Source:** [04](../context/04-audio-io/README.md)
- **Problem:** The production path correctly uses `listen.v1.media.transcribe_file` with `options=None`
  left for the v7 media API, but the two fallback branches (`listen.rest.v("1")`,
  `listen.prerecorded.v("1")`) are invoked with `options=None` — they'd send no model/smart_format if
  ever hit. Dead in production (pre-v7 shapes), but fragile within the `<8.0.0` range.
- **Fix:** Since the pin is `>=7.1.0,<8.0.0`, call `listen.v1.media.transcribe_file` directly and drop or
  guard the legacy branches so they never run with `options=None`.

## AUD-06 — `OUTPUT_AUDIO_DEVICE` is configured & documented but never used
- **Batch:** D · **Location:** [config/settings.py:73](../../config/settings.py#L73); no consumer in `tts.py` · **Source:** [04](../context/04-audio-io/README.md)
- **Problem:** `output_audio_device` is read and appears in `.env.example` (implying the AI voice can be
  routed to a chosen device, e.g. an OBS virtual cable), but no playback path references it. Operators
  routing audio for streaming will set it and see no effect.
- **Fix:** Implement output-device targeting (e.g. `play(..., use_ffmpeg=False)` over `sounddevice` with
  an explicit device) or remove the setting from `Settings` + `.env.example` and note routing is manual.

## INF-05 — Session `json.dumps` lacks `default=str` / `ensure_ascii=False`
- **Batch:** E · **Location:** [pipeline/memory.py:211](../../pipeline/memory.py#L211) · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Metadata/details dicts come from callers. A non-serializable value (Path, datetime, numpy
  scalar) makes `json.dumps` raise mid-`_save`; the CLI's broad `except` then ends the episode. Also
  `ensure_ascii` defaults True, so non-ASCII transcripts are written as `\uXXXX` — bloated and unreadable.
- **Fix:** `json.dumps(payload, indent=2, ensure_ascii=False, default=str)` (file is already UTF-8).

## INF-06 — `update_turn_metadata` raises when the turn was trimmed
- **Batch:** E · **Location:** [pipeline/memory.py:148-157](../../pipeline/memory.py#L148-L157), `:194-197` · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** `_trim` keeps only the last `max_turns*2` messages; `update_turn_metadata` raises
  `ValueError` if the target turn was trimmed. Latent today (the loop only updates the just-added
  assistant turn), but any older-turn update or small `max_turns` throws and ends the episode. (Reproduced.)
- **Fix:** Make `update_turn_metadata` a no-op (log + return) when the turn is gone, or guarantee TTS
  metadata is attached before any trim could drop it.

## INF-07 — Whole session rewritten every save (O(n²) over a long episode)
- **Batch:** E · **Location:** [pipeline/memory.py:199-212](../../pipeline/memory.py#L199-L212) · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Every `add`/`reserve_turn_id`/`add_event`/`update_turn_metadata` serializes and rewrites
  the entire growing JSON. A 2-hour episode (many turns × per-stage events) does quadratic disk work,
  wears flash, and widens the tmp+replace window. Realtime emits many events, amplifying it.
- **Fix:** Debounce/batch saves to turn boundaries while appending events to a separate
  `<session>.events.jsonl` (single append, no full rewrite). Design with **INF-01**.

## INF-08 — `latest_for_episode` sorts by name, not time
- **Batch:** E · **Location:** [pipeline/memory.py:110](../../pipeline/memory.py#L110) · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** "Latest" is chosen by lexical sort of `episode_YYYYMMDD_HHMMSS_…`. Equals chronological
  only while the clock is monotonic. An NTP/DST/manual backward clock change makes a newer session sort
  before an older one, so `--resume` reattaches to the wrong session and splits a recording across files.
- **Fix:** Sort by `path.stat().st_mtime` (or a stored creation epoch); keep the name for readability only.

## INF-09 — `retry_call` accepts but discards `timeout_seconds`
- **Batch:** F · **Location:** [pipeline/reliability.py:51](../../pipeline/reliability.py#L51) · **Source:** [02](../context/02-core-infra/README.md), [04](../context/04-audio-io/README.md)
- **Problem:** The wrapper `del`-etes `timeout_seconds` and enforces no wall-clock bound. The only timeout
  is whatever each SDK client got at construction — and the Google LLM client gets none (LLM-03). So a
  hung Google/STT/TTS request blocks the loop indefinitely. Backoff is also linear with no jitter, and
  layered on top of SDK retries (LLM-06).
- **Fix:** Enforce a deadline (run the op in a `concurrent.futures` worker, `future.result(timeout)`,
  raising `TimeoutError` — already treated as retryable), or require every client to receive the timeout.
  Add exponential backoff + jitter. This one fix also covers LLM-03.

## INF-10 — Doctor does no key/disk/output-device liveness checks
- **Batch:** G · **Location:** [pipeline/preflight.py:11-28](../../pipeline/preflight.py#L11-L28) · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** Preflight validates settings shape, prompt presence, dir writability, SDK imports, and
  (mic) input-device existence — but never pings a provider (invalid key passes), never checks free disk
  (a recording dies mid-session on a full disk), never checks the realtime **output** device or mic
  sample-rate compatibility. "Result: OK" can still fail a real recording.
- **Fix:** Add (a) a cheap per-provider auth check (warn, not fail, if offline) with a short timeout, (b)
  `shutil.disk_usage` free-space threshold on the sessions/audio drive, (c) an output-device
  resolvability check for realtime/sdk/system playback.

## INF-11 — Any host/LLM/TTS exception ends the whole episode
- **Batch:** F · **Location:** [main.py:74-77](../../main.py#L74-L77), `:101-104`, `:121-125` · **Source:** [02](../context/02-core-infra/README.md)
- **Problem:** The loop `break`s on the first exception in capture/LLM/TTS. A single transient blip
  (network hiccup, one bad Deepgram response, momentary mic error) terminates a live recording and forces
  `--resume`. AI text is preserved before TTS (good) but the session is over either way — fragile when
  the human is mid-sentence.
- **Fix:** For host-capture and TTS errors, offer continue/retry/skip (log, prompt, `continue`) instead
  of unconditional `break`. Reserve hard `break` for unrecoverable conditions; make it configurable.

## INF-12 — Realtime path drops `--max-turns` / `--confirm-transcript`
- **Batch:** B · **Location:** [main.py:215-223](../../main.py#L215-L223), `:225-232`; also `openclaw_tools.py:23-26` · **Source:** [02](../context/02-core-infra/README.md), [05](../context/05-integrations-ops/README.md)
- **Problem:** Realtime dispatch calls `run_realtime_episode(episode, settings, resume=…, session_path=…)`
  and never forwards `args.max_turns` or the confirm override. So the documented `--max-turns 1` rehearsal
  safeguard silently does nothing and a realtime session runs unbounded. Operators following "always run a
  one-turn rehearsal" get a full open-ended session.
- **Fix:** Thread `max_turns` into `run_realtime_episode` (and honor it), or reject `--max-turns` in
  realtime with a clear message. Document that confirm-transcript is N/A in realtime.

## OPS-04 — Docs overstate OpenClaw: it's an in-process library, no network layer
- **Batch:** I · **Location:** `README.md:185-224` (esp. 187); `docs/implementation-updates/2026-04-19-openclaw-session-integration.md:64` · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** There is no service/RPC/HTTP/registration layer anywhere; the integration is purely local
  Python functions importable from the same interpreter on the same machine. Both docs list network
  registration as "deferred," but the headline "OpenClaw agents can call local Python helpers" can
  mislead readers into expecting a registered endpoint.
- **Fix:** Reword to "in-process Python library, same machine/interpreter; no remote/registered endpoint
  (deferred)." Document the intended invocation context.

## OPS-05 — Operator guide points at legacy flat audio paths
- **Batch:** I · **Location:** `docs/AI_PODCAST_OPERATOR_GUIDE.md:392`, `:427-429` vs correct `:282-302` · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** §7 correctly documents `audio/<session_id>/…`, but §10 ("AI Audio Does Not Play Live") and
  §11 direct the user to the legacy flat `audio/output/`,`audio/input/`. The runtime only writes
  session-scoped paths; a user troubleshooting missing audio looks in the wrong place.
- **Fix:** Update §10 and §11 to the session-scoped paths consistent with §7.

## OPS-06 — Undocumented system dependencies
- **Batch:** I · **Location:** `requirements.txt` (no system note); `docs/AI_PODCAST_OPERATOR_GUIDE.md §1`; `README.md` OBS checklist · **Source:** [05](../context/05-integrations-ops/README.md)
- **Problem:** `sounddevice` needs PortAudio (bundled in the Windows wheel, but unstated); ffmpeg/mpv is
  required only for `PLAYBACK_MODE=sdk` (ElevenLabs `play()` shells out) — the default file-only path
  needs none, but this is never clarified; VB-Audio Virtual Cable (Windows) for OBS routing is only an
  aside, not a gated step.
- **Fix:** Add a "System prerequisites" section: PortAudio (bundled), libsndfile (bundled), ffmpeg only
  if `PLAYBACK_MODE=sdk`, VB-Audio cable for live OBS routing. State explicitly the default file-only
  flow needs no codecs.

## DEP-01 — Dependencies mostly uncapped; majors drifted; no lockfile
- **Batch:** J · **Location:** [requirements.txt](../../requirements.txt) · **Source:** [05](../context/05-integrations-ops/README.md), [07](../context/07-execution-report/README.md)
- **Problem:** Only `deepgram-sdk` (`<8.0`) and `websockets` (`<16.0`) have upper bounds. `anthropic`,
  `openai`, `google-genai`, `elevenlabs`, `numpy`, `rich`, `pytest` are floor-only. The env resolved to
  new majors past the floors (openai 2.x vs `>=1.70`, numpy 2.x, elevenlabs 2.x) — fresh installs are
  non-reproducible and a provider major can silently break the code's pre-2.0 shapes. No lockfile committed.
- **Fix:** Cap volatile SDKs (e.g. `anthropic>=0.50,<1`, `openai>=1.70,<3`, `numpy<3`, `pydantic<3`) and
  commit a lockfile (`pip freeze > requirements.lock`, or pip-tools/uv). Document the `<16`/`<8` cap
  rationales inline.
