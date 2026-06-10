# 03 — LLM Adapter Layer

> ✅ **Status (Batch C, + A/F):** every Known Issue **resolved** — LLM-01 (model-id validation), LLM-02 (`max_completion_tokens`), LLM-03 (Google timeout), LLM-04 (truncation detection + `provider_max_output_tokens`), LLM-05 (distinct refusal/safety errors), LLM-06 (single retry layer + preserved error type), LLM-07 (Anthropic prompt caching), LLM-08 (failure-path tests). Per-ticket status: [`../../tickets/README.md`](../../tickets/README.md).

Audit context for the LLM adapter that turns conversation history into a single
text reply from the configured model provider.

**In scope (audited):**
- [pipeline/llm.py](../../../pipeline/llm.py) — the adapter (Anthropic, OpenAI responses + chat, Google GenAI, dry-run)
- [tests/test_llm.py](../../../tests/test_llm.py) — adapter tests
- [pipeline/reliability.py](../../../pipeline/reliability.py) — retry/timeout wrappers (only as used by `llm.py`)
- LLM-relevant knobs in [config/settings.py](../../../config/settings.py) (`active_llm`, `active_model`, `openai_api_mode`, `provider_timeout_seconds`, `provider_max_retries`) — read, not broadly audited (settings are another agent's scope)
- [requirements.txt](../../../requirements.txt) lines for `anthropic`, `openai`, `google-genai`

---

## Purpose of this layer

This layer is the single text-in / text-out boundary between the podcast loop
and whatever LLM provider is configured. Given the running conversation
(`history`) and a system prompt, it returns one assistant string. It hides
provider differences behind one function, `call_llm()`, and supports four
backends selected by `ACTIVE_LLM`:

- `anthropic` — Anthropic Messages API
- `openai` — OpenAI, either the **Responses** API or the **Chat Completions** API (selected by `OPENAI_API_MODE`)
- `google` — Google GenAI (`google-genai`, `from google import genai`)
- `dry-run` / `dry_run` / `local` — no network call; echoes the last user turn

It also owns `load_system_prompt()`, which assembles the base persona prompt
plus an optional per-episode override file.

---

## Data flow

```
main.run_episode (main.py:97)
  memory.get()  ──►  call_llm(history, system_prompt, settings)        [llm.py:32]
                       │
                       ├─ settings.validate_for_active_provider()       (key present?)
                       ├─ _to_provider_messages(history)                [llm.py:58] keep user/assistant turns with non-empty content; drop role/created_at extras
                       ├─ if is_dry_run: _dry_run_response(...)          [llm.py:66]
                       └─ retry_call( _call_provider(...) )             [reliability.py:33]
                            │
                            ├─ _call_anthropic  → client.messages.create(...)            [llm.py:91]
                            ├─ _call_openai     → responses.create / chat.completions     [llm.py:115]
                            └─ _call_google     → client.models.generate_content(...)     [llm.py:145]
                       ◄── text  (RuntimeError if text.strip() == "")   [llm.py:53]
```

`call_llm` returns a plain `str`. The caller ([main.py:97-100](../../../main.py))
prints it, stores it in `ConversationMemory` as an `assistant` turn, then passes
it to TTS. History fed in comes from
[pipeline/memory.py](../../../pipeline/memory.py) `get()`, where each turn is
`{"role", "content", "created_at", optional "metadata"}`. Memory trims to
`max_turns * 2` messages (default 80) — see `_trim()` in memory.py:194 — so the
adapter itself does no context-window management.

---

## Provider paths in detail

### dry-run ([llm.py:66](../../../pipeline/llm.py))
Returns a canned string referencing the last user turn. No key, no network.
This is the default (`ACTIVE_LLM=dry-run`, `ACTIVE_MODEL=dry-run-v1` in
[.env.example](../../../.env.example)).

### Anthropic ([llm.py:91](../../../pipeline/llm.py))
```python
client = factory(api_key=..., timeout=settings.provider_timeout_seconds)
response = client.messages.create(
    model=settings.active_model,
    max_tokens=1024,
    system=system_prompt,
    messages=messages,        # [{role: user|assistant, content: str}, ...]
)
return "\n".join(b.text for b in response.content if getattr(b,"type",None)=="text").strip()
```
- Matches the current (2026) Messages API shape: `system` is a top-level param, `messages` carry only `user`/`assistant`, response is a list of content blocks filtered by `type == "text"`. Correct.
- `max_tokens=1024` is hardcoded (no streaming). For a conversational reply this is within limits and below the SDK's non-streaming timeout guard, so it won't raise — but a long answer is silently truncated at 1024 tokens (`stop_reason == "max_tokens"`, never inspected).
- Default model in `.env.example` is the placeholder `dry-run-v1` — not a real Anthropic model ID (e.g. `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5`). No model validation → 404 if the user switches `ACTIVE_LLM` without setting `ACTIVE_MODEL`.

### OpenAI — Responses mode ([llm.py:128](../../../pipeline/llm.py), `OPENAI_API_MODE=responses`, default)
```python
client.responses.create(
    model=settings.active_model,
    instructions=system_prompt,
    input=messages,                  # list of {role, content:str}
    max_output_tokens=1024,
)
return _extract_openai_responses_text(response)
```
- Correct for the Responses API: `instructions` is the system channel, `input` accepts an array of role/content message objects, `max_output_tokens` is the right cap. `_extract_openai_responses_text` ([llm.py:174](../../../pipeline/llm.py)) is robust: prefers `response.output_text`, falls back to walking `output[].content[]` for `output_text`/`text` parts, and handles both object and dict shapes.

### OpenAI — Chat mode ([llm.py:137](../../../pipeline/llm.py), `OPENAI_API_MODE=chat`)
```python
client.chat.completions.create(
    model=settings.active_model,
    max_tokens=1024,                 # ⚠ rejected by reasoning models
    messages=[{"role":"system","content":system_prompt}, *messages],
)
return _extract_openai_chat_text(response)
```
- System prompt is correctly prepended as a `system` message; extraction reads `choices[0].message.content` (object or dict). Correct for classic chat models.
- **`max_tokens` is the legacy parameter.** Reasoning models (o1/o3/o4/gpt-5 family) reject it with a 400 `unsupported_parameter` and require `max_completion_tokens`. So chat mode works for `gpt-4o`-class models but breaks on reasoning models. See Known Issues.

### Google GenAI ([llm.py:145](../../../pipeline/llm.py))
```python
client = genai.Client(api_key=settings.google_api_key)     # NO timeout
contents = [{"role": "model" if role=="assistant" else "user",
             "parts": [{"text": content}]} for ...]
response = client.models.generate_content(
    model=settings.active_model,
    contents=contents,
    config={"system_instruction": system_prompt},
)
return response.text or ""
```
- Correct shape for `google-genai`: `assistant`→`model` role mapping, `parts:[{text}]`, and `system_instruction` as a plain dict key in `config` (the SDK accepts dicts as well as `types.GenerateContentConfig`). For plain-text chat, `response.text` concatenates text parts — fine.
- Two robustness gaps: (1) the client is created with **no timeout**, and the retry wrapper deliberately discards `timeout_seconds`, so a hung Google call can block forever; (2) when a response is blocked (`finish_reason=SAFETY`) or has no candidates/parts, `response.text` can return `None` (→ `""` → caught by the empty-response guard, acceptable) but in some `google-genai` versions accessing `.text` on a response containing non-text parts emits a `UserWarning` or raises `ValueError`. For this pure-text use case the common path is fine; the blocked/empty path degrades to the generic "empty response" error rather than a clear safety message.

---

## Config knobs that affect this layer

| Setting | Default | Effect on adapter |
|---|---|---|
| `ACTIVE_LLM` | `dry-run` | Selects provider path. Validated in `validate_for_active_provider` (settings.py:156) — checks the matching API key is set, but **not** the model ID. |
| `ACTIVE_MODEL` | `dry-run-v1` | Passed verbatim as `model=` to every provider. No validation; placeholder default is not a real ID for any live provider. |
| `OPENAI_API_MODE` | `responses` | `responses` → Responses API; `chat` → Chat Completions. Validated to be one of those two in `validate_audio_modes` (settings.py:183). |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | Passed to the Anthropic and OpenAI client constructors. **Not** applied to Google. The retry wrapper itself discards it (`del timeout_seconds`, reliability.py:51). |
| `PROVIDER_MAX_RETRIES` | `1` | `attempts = max_retries + 1` retries inside `retry_call`. Layered *on top of* each SDK's own built-in retries. |

---

## Reliability wrapper ([pipeline/reliability.py](../../../pipeline/reliability.py))

`call_llm` wraps the provider call in `retry_call` with
`retry_predicate=is_transient_provider_error`.

- `retry_call` (reliability.py:33): runs the op up to `max_retries+1` times, sleeping `backoff_seconds * attempt` between tries; stops early if the predicate says the error isn't transient; on exhaustion raises `ProviderCallError` (a `@dataclass`-decorated `RuntimeError`). It **explicitly discards `timeout_seconds`** (`del timeout_seconds`, line 51) — it does not enforce any wall-clock timeout; that is delegated to the SDK clients (which Google's path never receives).
- `is_transient_provider_error` (reliability.py:73): treats `TimeoutError`/`ConnectionError` as transient, plus any error exposing `status_code` (or `response.status_code`) in `{408,409,429}` or `>= 500`. The `or ... and ...` on line 80 parses correctly (`and` binds tighter than `or`) — verified, **not** a bug.
- Note: each SDK already auto-retries 429/5xx with backoff, so total retry budget is `(max_retries+1) * (SDK retries+1)`. Transient errors are retried twice over.

---

## External dependencies / SDK versions ([requirements.txt](../../../requirements.txt))

| Package | Pin | Used by | Notes |
|---|---|---|---|
| `anthropic` | `>=0.50.0` | `_call_anthropic` | Lower bound only; Messages API shape used is stable in current SDKs. |
| `openai` | `>=1.70.0` | `_call_openai` | Responses API + Chat Completions both present at this floor. |
| `google-genai` | `>=1.0.0` | `_call_google` | `from google import genai` / `genai.Client` API. |

All SDKs are imported lazily inside their respective functions, so a missing
package only fails when that provider is actually selected (raising a clear
`RuntimeError: Install <pkg> ...`). No upper bounds are pinned, so a future
breaking SDK release could silently change request/response shapes.

---

## Integration points

- **Caller:** [main.py:97](../../../main.py) (`run_episode`) — the only production caller of `call_llm`. Wraps it in a broad `except Exception` that logs a structured error and breaks the loop.
- **System prompt source:** `load_system_prompt` ([llm.py:10](../../../pipeline/llm.py)) reads `config/prompts/base_system.txt` (required; raises `FileNotFoundError` if absent) and appends `config/prompts/episodes/<episode>.txt` if present. Also used by the realtime path ([pipeline/realtime.py:196](../../../pipeline/realtime.py)) — but realtime does **not** use `call_llm` (it talks to the OpenAI Realtime API directly).
- **History source:** `ConversationMemory.get()` ([memory.py:133](../../../pipeline/memory.py)) returns the trimmed history list. `_to_provider_messages` re-projects to `{role, content}` and drops any turn whose content is empty/falsy.

---

## Known Issues

Severity in parentheses; full detail and fixes are in the audit issue list.

1. **(P1) Placeholder default model ID for all live providers.** `ACTIVE_MODEL` defaults to `dry-run-v1` and the only example value in `.env.example` is that placeholder. Switching `ACTIVE_LLM` to a live provider without also setting a real model ID sends `model="dry-run-v1"` → 404 `NotFoundError`. No model validation exists. ([llm.py:105](../../../pipeline/llm.py), [llm.py:131](../../../pipeline/llm.py), [llm.py:167](../../../pipeline/llm.py), [.env.example:10](../../../.env.example))

2. **(P1) Chat mode sends `max_tokens`, which reasoning models reject.** `client.chat.completions.create(..., max_tokens=1024, ...)` returns a 400 `unsupported_parameter` on o1/o3/o4/gpt-5-class models, which require `max_completion_tokens`. Chat mode only works for `gpt-4o`-era models. ([llm.py:140](../../../pipeline/llm.py))

3. **(P2) Google path has no timeout.** `genai.Client(api_key=...)` is constructed without a timeout, and `retry_call` discards `timeout_seconds`, so a hung Google request blocks the whole episode indefinitely. Anthropic and OpenAI pass the timeout to their clients; Google does not. ([llm.py:158](../../../pipeline/llm.py), [reliability.py:51](../../../pipeline/reliability.py))

4. **(P2) Hardcoded 1024-token cap with no `stop_reason`/truncation handling.** Every live path caps output at 1024 tokens and ignores the stop reason. A long answer is silently cut mid-sentence; the truncated text still passes the non-empty guard and is spoken/stored as if complete. ([llm.py:106](../../../pipeline/llm.py), [llm.py:133](../../../pipeline/llm.py), [llm.py:140](../../../pipeline/llm.py))

5. **(P2) Blocked/refused responses surface as a generic "empty response" error.** Anthropic `stop_reason="refusal"`, an OpenAI refusal, or a Google `finish_reason=SAFETY` all collapse to either `""` (Google `response.text or ""`) or an empty-text join, tripping the generic `RuntimeError: ... returned an empty response` at [llm.py:53](../../../pipeline/llm.py) instead of a clear "model refused" message. ([llm.py:53](../../../pipeline/llm.py), [llm.py:110](../../../pipeline/llm.py), [llm.py:171](../../../pipeline/llm.py))

6. **(P2) Double retry budget; non-retryable 400s in chat mode (e.g. bad `max_tokens`) aren't fast-failed cleanly.** `retry_call` layers on top of each SDK's own auto-retry, so transient errors are retried `(max_retries+1) * (SDK_retries+1)` times. Permanent 400s (model-ID typo, unsupported param) are correctly *not* retried by the predicate, but the resulting `ProviderCallError` message buries the original cause. ([reliability.py:33](../../../pipeline/reliability.py), [llm.py:45](../../../pipeline/llm.py))

7. **(P3) No prompt caching / cost controls.** The full (trimmed) history and system prompt are re-sent every turn with no `cache_control` (Anthropic) or equivalent, so the system prompt and growing history are re-billed at full input price each turn. ([llm.py:104](../../../pipeline/llm.py))

8. **(P3) Test suite only exercises happy paths.** [tests/test_llm.py](../../../tests/test_llm.py) covers dry-run, OpenAI responses+chat, and Google success via fakes returning well-formed objects. No test covers: empty/blocked response → `RuntimeError`, the multi-block/`output_text`-fallback extraction branch, retry/transient-error behavior, the Anthropic path at all, or a missing-key validation failure.
