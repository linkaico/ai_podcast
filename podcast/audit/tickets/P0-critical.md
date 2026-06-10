# P0 — Critical (broken right now)

Two blockers. Neither is hypothetical: one breaks the project's own documented command and CI;
the other makes the headline live feature unable to connect.

---

## EXE-01 — Committed `.env` duplicate keys flip the project into realtime/mic
- **Area:** execution · **Batch:** A · **Source:** [07-execution-report](../context/07-execution-report/README.md)
- **Location:** `.env` (duplicate block, ~lines 47-49: `CONVERSATION_MODE=realtime`, `OPENAI_API_KEY=your_key_here`, `INPUT_MODE=mic`)
- **Problem:** The committed `.env` defines keys twice — a dry-run block at the top, then a realtime
  block at the bottom. `python-dotenv` keeps the **last** value, so the project actually boots in
  `realtime` + `mic` with a placeholder key `your_key_here` (non-empty, so validation passes).
  Consequences, both reproduced empirically:
  1. `python main.py pilot` — the documented dry-run command — does **not** run the text loop; it
     dispatches to the realtime WebSocket and crashes with
     `websockets.exceptions.ConnectionClosedError: … invalid_api_key`.
  2. `pytest` fails `tests/test_cli_dry_run.py::test_main_passes_resume_and_session_flags`
     (`assert 1 == 0`) because `main()` takes the realtime branch.
  Forcing dry-run env vars makes the suite 73/73 and the episode succeed — proving the `.env` is the cause.
- **Fix:** Replace `.env` with the single dry-run block from `.env.example` (blank `OPENAI_API_KEY`,
  `CONVERSATION_MODE=dry-run`, `INPUT_MODE=text`, `TTS_MODE=dry-run`); delete the duplicate realtime
  override. Better: **stop committing `.env` at all** (it's normally git-ignored) and keep only
  `.env.example`. Pair with **EXE-03** so the test no longer depends on this file.
- **Verify:** `pytest -q` → 73 passed; `python main.py pilot` (pipe `hi\nq\n`) runs the dry-run loop.

---

## RT-01 — Realtime WebSocket URL omits the mandatory `?model=` query parameter
- **Area:** realtime · **Batch:** B · **Source:** [06-realtime](../context/06-realtime/README.md)
- **Location:** [pipeline/realtime.py:18](../../pipeline/realtime.py#L18) (`REALTIME_URL = "wss://api.openai.com/v1/realtime"`), used at ~line 243.
- **Problem:** The OpenAI GA Realtime endpoint **requires** the model on the URL
  (`wss://api.openai.com/v1/realtime?model=<id>`). Connecting without it fails the WebSocket
  handshake with **400 Bad Request** before any `session.update` is sent. Setting `model` inside
  `session.update` does **not** substitute — the query param selects the model at connect time.
  As written, the primary live recording path cannot establish a connection at all. (Verified
  against OpenAI Realtime docs and the Azure GA migration guide. The rest of the protocol modeling —
  `session.update` shape, event names, `pcm16`/24 kHz, `marin` voice, omitted `OpenAI-Beta` header —
  is correct, which is why this one omission is the whole blocker.)
- **Fix:** Build the URL with the model appended:
  ```python
  url = f"{REALTIME_URL}?model={settings.realtime_model}"
  ```
  and connect to `url`. Keep `model` in `session.update` too (harmless/consistent). Add the
  test from **RT-06** that asserts the connect URL contains `?model=` so this can't regress.
- **Verify:** with a valid `OPENAI_API_KEY`, `CONVERSATION_MODE=realtime INPUT_MODE=mic` connects and
  streams audio; the new fake-connector test asserts the `?model=` query param.
