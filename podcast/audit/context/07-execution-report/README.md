# 07 — Execution Audit Report

> ✅ **Post-fix status (2026-06-10):** this report is the **historical** 2026-06-09 run that
> established the shipped-state failures. They are now resolved: the committed `.env` is a clean
> dry-run (`EXE-01`, Batch A), the realtime path connects (`RT-01`, Batch B), the CLI test is
> hermetic (`EXE-03`), the stale `.venv` is gitignored + rebuilt (`EXE-04`), deps are capped +
> locked (`EXE-05`/Batch J), and console mojibake is fixed (`EXE-06`/Batch J). The suite is now
> **114 green** (was 72/1-fail as shipped). Keep the run below as the empirical record.

Empirical run of the real-time AI podcast pipeline on a real Windows machine.

- **Machine:** Windows 10 Pro (10.0.19045), win32, PowerShell + Git Bash
- **Date:** 2026-06-09
- **Project root:** `C:\Users\Flori\Documents\AI Podcast\podcast`
- **System Python:** 3.13.3 (`C:\Python313\python.exe`), pip 25.3
- **Audit venv built for this report:** `.venv_audit` (Python 3.13.3) — created fresh, then removed during cleanup (see end).

Everything below is actual command output, trimmed of noise. No paid API was called. No real API keys were used or written. No project source files were modified.

---

## 1. Environment probe

```
$ python --version           ->  Python 3.13.3
$ py --version               ->  Python 3.13.3
$ where python               ->  C:\Python313\python.exe
                                 C:\Users\Flori\AppData\Local\Microsoft\WindowsApps\python.exe
$ python -m pip --version    ->  pip 25.3 (python 3.13)
```

### Committed `.venv` state — STALE / MIXED-PLATFORM, and EMPTY

The committed `.venv` contains **both** a Windows layout and a POSIX layout:

```
.venv/Scripts/python.exe   254696 bytes  (real Windows launcher, runs: Python 3.13.3)
.venv/bin/python                6 bytes  (POSIX stub — a macOS/Linux venv leftover)
.venv/bin/Activate.ps1     dated Jun 12 2025
.venv/Scripts/Activate.ps1 dated Apr  8 2025
```

`pyvenv.cfg` points at `C:\Python313`. The Windows `python.exe` does launch, BUT the venv is **empty of project dependencies**:

```
$ .venv/Scripts/python.exe -m pip list
Package Version
------- -------
pip     25.0.1
```

Conclusion: the committed `.venv` is unusable as-is. It is a mix of a Windows venv and a stray POSIX `bin/` tree (the repo history has both a "mac" commit and the initial commit), and it has **no** dependencies installed — `import config.settings` etc. would all fail. It should not be relied on; it should arguably not be committed at all. A fresh venv is required.

---

## 2. Fresh venv + install

```
$ py -m venv .venv_audit                              -> exit 0
$ .venv_audit/Scripts/python.exe -m pip install --upgrade pip   -> pip 26.1.2
$ .venv_audit/Scripts/python.exe -m pip install -r requirements.txt
```

**Result: SUCCESS — all 44 packages installed from pre-built wheels. No compilation, no build errors, no version conflicts.**

Key packages and the Windows wheels that resolved (Python 3.13 / cp313 / win_amd64):

| requirement | resolved |
|---|---|
| anthropic>=0.50.0 | anthropic 0.107.1 |
| openai>=1.70.0 | openai 2.41.0 |
| google-genai>=1.0.0 | google-genai 2.8.0 |
| deepgram-sdk>=7.1.0,<8.0.0 | deepgram-sdk 7.3.1 |
| elevenlabs>=1.16.0 | elevenlabs 2.52.0 |
| requests>=2.31.0 | requests 2.34.2 |
| sounddevice>=0.4.7 | sounddevice 0.5.5 (win_amd64 wheel, bundles PortAudio) |
| soundfile>=0.12.1 | soundfile 0.14.0 (win_amd64 wheel, bundles libsndfile) |
| python-dotenv>=1.0.0 | python-dotenv 1.2.2 |
| numpy>=1.26.0 | numpy 2.4.6 (cp313 win_amd64 wheel) |
| rich>=13.0.0 | rich 15.0.0 |
| websockets>=14.0,<16.0 | websockets 15.0.1 |
| pytest>=8.0.0 | pytest 9.0.3 |

**No PortAudio / libsndfile / numpy build issues on this Windows machine** — every native dependency had a binary wheel. `cffi 2.0.0` (sounddevice dep) and `cryptography 48.0.0` (google-auth dep) also resolved as wheels. Per-package failures: **none**.

> Note: `requirements.txt` is fully unpinned (all `>=`). Today it resolved cleanly, but it is **not reproducible** — a future resolution could pull a breaking major (e.g. a hypothetical anthropic/openai 3.x) since there are no upper bounds except on `deepgram-sdk` and `websockets`.

---

## 3. Import smoke test

```
$ PYTHONPATH=<root> .venv_audit/Scripts/python.exe -c "import main; import config.settings; \
  import pipeline.llm, pipeline.stt, pipeline.tts, pipeline.realtime, pipeline.memory, \
  pipeline.preflight, pipeline.reliability, integrations.openclaw_tools; print('ALL IMPORTS OK')"

ALL IMPORTS OK
```

No ImportErrors. All listed modules import cleanly on Windows / Python 3.13.

---

## 4. Test suite — THE KEY DELIVERABLE

### 4a. As shipped (using the committed `.env`): **72 passed, 1 FAILED**

```
$ PYTHONPATH=<root> .venv_audit/Scripts/python.exe -m pytest -q
...
FAILED tests/test_cli_dry_run.py::test_main_passes_resume_and_session_flags
1 failed, 72 passed in 8.72s
=== PYTEST EXIT CODE: 1 ===
```

Every other test passes. The full verbose pass list (73 tests across test_cli_dry_run, test_llm, test_memory, test_openclaw_tools, test_preflight, test_realtime, test_reliability, test_settings, test_stt, test_tts) is all PASSED except the one below.

### 4b. The single failure — root cause = the committed `.env`, not the test logic

Failing test: `tests/test_cli_dry_run.py::test_main_passes_resume_and_session_flags`

Assertion that fails:
```
>       assert main(["pilot", "--resume"]) == 0
E       AssertionError: assert 1 == 0
E        +  where 1 = main(['pilot', '--resume'])
```

What actually happens (captured during the failure): the test calls real `main()` and the run prints
```
Episode: pilot
Realtime microphone conversation active. Press ENTER to end the episode.
```
then dies in the OpenAI Realtime WebSocket:
```
websockets.exceptions.ConnectionClosedError:
  received 3000 (registered) invalid_request_error.invalid_api_key;
  then sent 3000 (registered) invalid_request_error.invalid_api_key
```
plus a secondary `OSError: pytest: reading from stdin while output is captured!` from the realtime `_wait_for_stop()` and a `NotImplementedError` from `loop.add_reader(sys.stdin, ...)` (ProactorEventLoop on Windows does not support `add_reader` on stdin).

**Why:** The test monkeypatches `main.run_episode` but does **not** monkeypatch `main.load_settings`. So `main()` calls the real `load_settings()`, which reads the committed `.env`. That `.env` has **duplicate keys**: the dry-run block at the top is overridden by a second block at the bottom:

```
.env line 47:  CONVERSATION_MODE=realtime
.env line 48:  OPENAI_API_KEY=your_key_here
.env line 49:  INPUT_MODE=mic
```

`python-dotenv` keeps the **last** value, so `conversation_mode=realtime`, `input_mode=mic`, and `openai_api_key="your_key_here"` (a non-empty placeholder). Validation passes (key is non-empty), so `main()` dispatches to `run_realtime_episode` — **the wrong branch** (not the monkeypatched `run_episode`) — which opens a live WebSocket with the bogus key, the server rejects it, and `main()` returns 1.

### 4c. Proof: with a correct dry-run config the suite is GREEN (73/73)

`python-dotenv` does **not** override variables already present in `os.environ`, so forcing the dry-run values inline neutralizes the bad `.env`:

```
$ CONVERSATION_MODE=dry-run INPUT_MODE=text TTS_MODE=dry-run ACTIVE_LLM=dry-run OPENAI_API_KEY= \
    PYTHONPATH=<root> .venv_audit/Scripts/python.exe -m pytest -q
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 2.51s
=== EXIT: 0 ===
```

And the single test in isolation under the same override:
```
$ CONVERSATION_MODE=dry-run INPUT_MODE=text TTS_MODE=dry-run ACTIVE_LLM=dry-run \
    ... pytest tests/test_cli_dry_run.py::test_main_passes_resume_and_session_flags -q
1 passed in 0.07s
```

**Verdict on the suite:** the code/tests are sound (73/73). The committed `.env` is what breaks the suite. Two contributing problems: (1) the `.env` has duplicate keys flipping the project into realtime/mic mode; (2) the test is environment-coupled — it reads the real `.env` because it forgets to patch `load_settings`, so it is not hermetic.

---

## 5. Doctor / preflight + list-devices

### `python main.py pilot --doctor` (committed `.env`) — exit 0, Result: OK

```
Preflight checks:
- OK settings: runtime settings are valid
- OK base_prompt: C:\Users\Flori\Documents\AI Podcast\podcast\config\prompts\base_system.txt
- OK sessions: writable: ...\sessions
- OK audio_input: writable: ...\audio\input
- OK audio_output: writable: ...\audio\output
- OK exports: writable: ...\exports
- OK sdk:sounddevice: installed
- OK sdk:soundfile: installed
- OK sdk:numpy: installed
- OK sdk:websockets: installed
- OK audio_device: found input device: 0
Result: OK
===== DOCTOR EXIT: 0 =====
```

Note: doctor reports OK **because** the committed `.env` is in realtime mode with the placeholder key `your_key_here` (non-empty passes validation). So `--doctor` gives a green light that does **not** reflect a runnable dry-run, and does not reflect a runnable realtime session either (the key is fake). Doctor validates config shape + SDK presence + writable dirs + device index; it does not verify the key actually authenticates.

### `python main.py --list-devices` — exit 0, PortAudio works

```
0: Microsoft Sound Mapper - Input (inputs=2, rate=44100.0)
1: Mikrofon (Razer Kiyo) (inputs=2, rate=44100.0)
2: MOTIV Mix Virtual Output (Shure (inputs=2, rate=44100.0)
3: Mikrofon (2- Shure MV6) (inputs=1, rate=44100.0)
11: Primary Sound Capture Driver (inputs=2, rate=44100.0)
...
46: Mikrofon (Shure MV6) (inputs=1, rate=48000.0)
50: Kopfh�rer (...AirPods Pro...) (inputs=1, rate=8000.0)
...
===== LIST-DEVICES EXIT: 0 =====
```

sounddevice/PortAudio enumerates real Windows input devices correctly. Cosmetic only: non-ASCII device names ("Kopfhörer", "Mikrofon" with umlaut) render as mojibake because the Windows console code page is not UTF-8. It does not crash — purely a display nit.

---

## 6. Dry-run episode (non-interactive)

### 6a. As shipped (`python main.py pilot` with committed `.env`) — BROKEN: raw traceback, exit 1

```
$ printf 'hello world\nq\n' | .venv_audit/Scripts/python.exe -u main.py pilot
Episode: pilot
Session: ...\sessions\pilot_20260609_151416_..._062dcc97.json
Realtime microphone conversation active. Press ENTER to end the episode.
Traceback (most recent call last):
  File ".../main.py", line 216, in main
    asyncio.run( run_realtime_episode( ... ) )
  ...
  File ".../pipeline/realtime.py", line 244, in run_realtime_episode
    await websocket.send(json.dumps(build_session_update(settings, prompt)))
  ...
websockets.exceptions.ConnectionClosedError:
  received 3000 (registered) invalid_request_error.invalid_api_key; ...
===== EXIT: 1 =====
```

With the committed `.env`, the documented `python main.py pilot` does **not** run the text dry-run episode at all. It goes straight to the realtime WebSocket path (because the `.env` forces realtime/mic), connects with the placeholder key, and crashes with an **unhandled** `ConnectionClosedError` — a raw traceback, not a clean error. `main()`'s handler is `except (FileNotFoundError, ValueError, RuntimeError)`, which does not include `websockets.exceptions.ConnectionClosedError` (it subclasses `Exception`, not those).

### 6b. With correct dry-run config — WORKS end to end, exit 0

```
$ printf 'hello world\nq\n' | CONVERSATION_MODE=dry-run INPUT_MODE=text TTS_MODE=dry-run \
    ACTIVE_LLM=dry-run OPENAI_API_KEY= .venv_audit/Scripts/python.exe -u main.py pilot
Episode: pilot
Session: ...\sessions\pilot_20260609_151432_..._1d312b05.json
Text input mode is active. Type your host turn, or q/quit/end to finish.
FLORIAN> FLORIAN: hello world
AI thinking...
AI: I'm in dry-run mode, so no external model was called. I heard: hello world
[dry-run voice saved] ...\audio\pilot_20260609_151432_..._1d312b05\output\turn_000000.txt
FLORIAN> Episode ended. Session saved.
===== EXIT: 0 =====
```

**Artifacts confirmed on disk:**

- Session JSON `sessions/pilot_20260609_151432_852781_1d312b05.json` — contains `history` (user + assistant turns), `events` (llm_completed/ok, tts_saved/ok, turn/complete, episode/ended), and an `artifacts.dryrun_text` manifest pointing at the txt file.
- Dry-run voice artifact `audio/pilot_20260609_151432_852781_1d312b05/output/turn_000000.txt` (74 bytes), content: `I'm in dry-run mode, so no external model was called. I heard: hello world`.

The happy-path dry-run completes fully when the config is correct. `--max-turns 1` also works:

```
$ printf 'turn one\n' | <dry-run env> ... main.py pilot --max-turns 1
... AI: I'm in dry-run mode ... I heard: turn one
Max turns reached. Session saved.   (exit 0)
```

---

## 7. Resume smoke test

```
$ printf 'q\n' | CONVERSATION_MODE=dry-run INPUT_MODE=text TTS_MODE=dry-run ACTIVE_LLM=dry-run \
    OPENAI_API_KEY= .venv_audit/Scripts/python.exe -u main.py pilot --resume
Episode: pilot
Session: ...\sessions\pilot_20260609_151432_..._1d312b05.json   <- loaded the latest session
Text input mode is active. ...
FLORIAN> Episode ended. Session saved.
===== RESUME EXIT: 0 =====
```

Resume loads the most recent `pilot_*` session and exits cleanly. Works.

> Same caveat as §6: `--resume` with the committed `.env` would hit the realtime path and crash, because resume routing happens after the realtime/non-realtime branch in `main()`. Resume only works in the dry-run/chained branch.

---

## 8. CLI surface

### `python main.py --help` — well-formed argparse, exit 0

```
usage: main.py [-h] [--resume] [--session SESSION] [--doctor] [--list-devices]
               [--confirm-transcript] [--no-confirm-transcript]
               [--max-turns MAX_TURNS]
               [episode_name]

Run an AI podcast episode.
positional arguments:
  episode_name
options:
  -h, --help            show this help message and exit
  --resume              Resume the latest session for this episode.
  --session SESSION     Resume a specific session JSON file.
  --doctor              Run preflight checks and exit.
  --list-devices        List audio input devices and exit.
  --confirm-transcript  Confirm/edit mic transcripts before LLM calls.
  --no-confirm-transcript
  --max-turns MAX_TURNS Stop after this many completed new turns.
```

Note: there is **no subcommand** — `pilot` is the positional `episode_name`. (`python main.py pilot --doctor` = episode "pilot" + doctor flag.)

### Invalid config → clean error (exit 1, no traceback)

```
$ printf 'q\n' | ACTIVE_LLM=bogus CONVERSATION_MODE=chained INPUT_MODE=text TTS_MODE=dry-run \
    OPENAI_API_KEY= .venv_audit/Scripts/python.exe -u main.py pilot
Error: Unsupported ACTIVE_LLM 'bogus'. Expected one of: dry-run, anthropic, openai, google.
===== EXIT: 1 =====
```

`SettingsError` subclasses `RuntimeError`, which `main()` catches, so config errors surface as a clean `Error: ...` line. (`CONVERSATION_MODE=chained` is required to actually exercise the provider check; in dry-run/realtime the active_llm validation is skipped.)

---

## Verdict — what actually works vs. what is broken on Windows right now

### Works (empirically verified)
- **Install:** `pip install -r requirements.txt` into a fresh Python 3.13 venv succeeds with zero build errors — all native deps (numpy, sounddevice/PortAudio, soundfile/libsndfile, cffi, cryptography) have Windows wheels.
- **Imports:** every module imports cleanly.
- **Test suite logic:** 73/73 pass **once the dry-run config is correct**. The code is healthy.
- **Dry-run episode:** completes end to end (session JSON + dry-run voice artifact written) when config is dry-run. `--max-turns` works.
- **Resume:** loads the latest session and exits cleanly (dry-run config).
- **Doctor:** runs, validates, reports.
- **`--list-devices`:** enumerates real Windows audio devices via PortAudio.
- **`--help`** and **invalid-config handling:** clean.

### Broken / requires undocumented setup on a real machine
1. **Committed `.env` forces realtime/mic and breaks the default experience.** It has duplicate keys; the bottom block (`CONVERSATION_MODE=realtime`, `INPUT_MODE=mic`, `OPENAI_API_KEY=your_key_here`) overrides the dry-run block. Result: `pytest` fails 1 test, and `python main.py pilot` crashes into a realtime WebSocket instead of running the documented dry-run. **This is the single highest-impact defect.** Fix: make `.env` match `.env.example` (one dry-run block, no realtime override, empty/blank `OPENAI_API_KEY`), and remove duplicate keys. (Verified: `.env.example` values load as a valid dry-run config, `is_dry_run=True`, `uses_realtime=False`.)
2. **Realtime path leaks raw tracebacks.** WebSocket/auth failures (`websockets.exceptions.ConnectionClosedError`) are not caught by `main()` (which only catches `FileNotFoundError/ValueError/RuntimeError`) and there is no `except` around the WebSocket block in `run_realtime_episode`. A user with a wrong/expired OpenAI key gets a stack trace instead of `Error: ...`.
3. **The CLI test is non-hermetic.** `test_main_passes_resume_and_session_flags` reads the real `.env` (it doesn't patch `load_settings`), so the test outcome depends on the developer's machine `.env`. Even after fixing `.env`, this test should patch `load_settings` (or set the env explicitly) to stay deterministic.
4. **Committed `.venv` is stale/mixed-platform and empty.** It mixes a Windows `Scripts/` tree with a POSIX `bin/` stub and has no dependencies. It should be git-ignored and not committed; following the README to "use the existing venv" would fail.
5. **`requirements.txt` is fully unpinned** (no upper bounds except deepgram/websockets). Reproducibility risk over time; today's resolution worked.
6. **Windows console mojibake** for non-ASCII device names in `--list-devices` (cosmetic).

### Bottom line
The pipeline is fundamentally sound and runs on Windows out of the box for install/import/tests/dry-run **provided the `.env` is corrected to dry-run**. As literally shipped (committed `.env`), a fresh user gets a failing test and a crashing `python main.py pilot`. The fixes are small and config-centric, not architectural.

---

### Cleanup note
Created during this audit and **removed** afterward: `.venv_audit/` (throwaway venv), `sessions/pilot_20260609_151416_*.json`, `sessions/pilot_20260609_151432_*.json`, `sessions/pilot_20260609_151511_*.json`, and their `audio/pilot_20260609_15*` artifact dirs. No project source files were modified. The committed `.env` and `.venv` were left untouched. This report (`audit/context/07-execution-report/README.md`) is the only intentional addition left behind.
