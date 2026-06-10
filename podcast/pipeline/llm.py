from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Sequence

from config.settings import PROJECT_ROOT, Settings, load_settings
from pipeline.memory import safe_episode_name
from pipeline.reliability import is_transient_provider_error, retry_call

logger = logging.getLogger(__name__)


def load_system_prompt(
    episode_name: str | None = None,
    settings: Settings | None = None,
    root_dir: str | Path | None = None,
) -> str:
    root = Path(root_dir).resolve() if root_dir else (settings.root_dir if settings else PROJECT_ROOT)
    prompts_dir = root / "config" / "prompts"
    base_path = prompts_dir / "base_system.txt"

    if not base_path.exists():
        raise FileNotFoundError(f"Missing base system prompt: {base_path}")

    prompt_parts = [base_path.read_text(encoding="utf-8").strip()]

    if episode_name:
        episode_path = prompts_dir / "episodes" / f"{safe_episode_name(episode_name)}.txt"
        if episode_path.exists():
            prompt_parts.append(episode_path.read_text(encoding="utf-8").strip())

    return "\n\n".join(part for part in prompt_parts if part)


def call_llm(
    history: Sequence[dict[str, str]],
    system_prompt: str,
    settings: Settings | None = None,
    client_factories: dict[str, Callable[..., Any]] | None = None,
) -> str:
    active_settings = settings or load_settings()
    active_settings.validate_for_active_provider()
    messages = _to_provider_messages(history)

    if active_settings.is_dry_run:
        return _dry_run_response(messages)

    response = retry_call(
        lambda: _call_provider(messages, system_prompt, active_settings, client_factories or {}),
        provider=active_settings.active_llm,
        stage="llm",
        max_retries=active_settings.provider_max_retries,
        timeout_seconds=active_settings.provider_timeout_seconds,
        retry_predicate=is_transient_provider_error,
    )
    if not response.strip():
        raise RuntimeError(f"{active_settings.active_llm} returned an empty response.")
    return response


def _to_provider_messages(history: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"} and item.get("content")
    ]


def _dry_run_response(messages: Sequence[dict[str, str]]) -> str:
    last_user = next((item["content"] for item in reversed(messages) if item["role"] == "user"), "")
    if not last_user:
        return "I'm in dry-run mode and ready for the first host turn."
    return (
        "I'm in dry-run mode, so no external model was called. "
        f"I heard: {last_user}"
    )


def _call_provider(
    messages: list[dict[str, str]],
    system_prompt: str,
    settings: Settings,
    client_factories: dict[str, Callable[..., Any]],
) -> str:
    if settings.active_llm == "anthropic":
        return _call_anthropic(messages, system_prompt, settings, client_factories.get("anthropic"))
    if settings.active_llm == "openai":
        return _call_openai(messages, system_prompt, settings, client_factories.get("openai"))
    if settings.active_llm == "google":
        return _call_google(messages, system_prompt, settings, client_factories.get("google"))
    raise ValueError(f"Unsupported ACTIVE_LLM '{settings.active_llm}'.")


def _call_anthropic(
    messages: list[dict[str, str]],
    system_prompt: str,
    settings: Settings,
    client_factory: Callable[..., Any] | None = None,
) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Install anthropic to use ACTIVE_LLM=anthropic.") from exc

    factory = client_factory or anthropic.Anthropic
    client = factory(
        api_key=settings.anthropic_api_key,
        timeout=settings.provider_timeout_seconds,
        max_retries=0,
    )
    response = client.messages.create(
        model=settings.active_model,
        max_tokens=settings.provider_max_output_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise RuntimeError("anthropic declined to respond (stop_reason=refusal).")
    text = "\n".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    if stop_reason == "max_tokens":
        logger.warning("anthropic response truncated at max_tokens=%s.", settings.provider_max_output_tokens)
    return text


def _call_openai(
    messages: list[dict[str, str]],
    system_prompt: str,
    settings: Settings,
    client_factory: Callable[..., Any] | None = None,
) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install openai to use ACTIVE_LLM=openai.") from exc

    factory = client_factory or OpenAI
    client = factory(
        api_key=settings.openai_api_key,
        timeout=settings.provider_timeout_seconds,
        max_retries=0,
    )
    if settings.openai_api_mode == "responses":
        response = client.responses.create(
            model=settings.active_model,
            instructions=system_prompt,
            input=messages,
            max_output_tokens=settings.provider_max_output_tokens,
        )
        text = _extract_openai_responses_text(response)
        _check_openai_responses_status(response)
        return text

    response = client.chat.completions.create(
        model=settings.active_model,
        max_completion_tokens=settings.provider_max_output_tokens,
        messages=[{"role": "system", "content": system_prompt}, *messages],
    )
    _check_openai_chat_finish(response)
    return _extract_openai_chat_text(response)


def _call_google(
    messages: list[dict[str, str]],
    system_prompt: str,
    settings: Settings,
    client_factory: Callable[..., Any] | None = None,
) -> str:
    if client_factory is not None:
        client = client_factory(api_key=settings.google_api_key)
    else:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Install google-genai to use ACTIVE_LLM=google.") from exc
        client = genai.Client(
            api_key=settings.google_api_key,
            http_options={"timeout": settings.provider_timeout_seconds * 1000},
        )
    contents = [
        {
            "role": "model" if message["role"] == "assistant" else "user",
            "parts": [{"text": message["content"]}],
        }
        for message in messages
    ]
    response = client.models.generate_content(
        model=settings.active_model,
        contents=contents,
        config={"system_instruction": system_prompt},
    )
    return _extract_google_text(response, settings)


def _extract_openai_responses_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    if isinstance(response, dict) and response.get("output_text"):
        return response["output_text"]

    output = response.get("output", []) if isinstance(response, dict) else getattr(response, "output", [])
    chunks = []
    for item in output or []:
        content = item.get("content", []) if isinstance(item, dict) else getattr(item, "content", [])
        for part in content or []:
            part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if part_type in {"output_text", "text"} and text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_openai_chat_text(response: Any) -> str:
    if isinstance(response, dict):
        return response["choices"][0]["message"].get("content") or ""
    return response.choices[0].message.content or ""


def _openai_chat_finish_reason(response: Any) -> str | None:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        return choices[0].get("finish_reason") if choices else None
    choices = getattr(response, "choices", None) or []
    return getattr(choices[0], "finish_reason", None) if choices else None


def _check_openai_chat_finish(response: Any) -> None:
    finish = _openai_chat_finish_reason(response)
    if finish == "content_filter":
        raise RuntimeError("openai blocked the response (finish_reason=content_filter).")
    if finish == "length":
        logger.warning("openai chat response truncated (finish_reason=length).")


def _check_openai_responses_status(response: Any) -> None:
    status = response.get("status") if isinstance(response, dict) else getattr(response, "status", None)
    if status != "incomplete":
        return
    details = (
        response.get("incomplete_details")
        if isinstance(response, dict)
        else getattr(response, "incomplete_details", None)
    )
    reason = None
    if isinstance(details, dict):
        reason = details.get("reason")
    elif details is not None:
        reason = getattr(details, "reason", None)
    if reason == "content_filter":
        raise RuntimeError("openai blocked the response (incomplete_details.reason=content_filter).")
    logger.warning("openai response incomplete (reason=%s).", reason or "unknown")


def _extract_google_text(response: Any, settings: Settings) -> str:
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None) if feedback is not None else None
    if block_reason:
        raise RuntimeError(f"google blocked the prompt (block_reason={block_reason}).")

    candidates = getattr(response, "candidates", None) or []
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    finish_name = getattr(finish_reason, "name", None) or (str(finish_reason) if finish_reason is not None else None)
    if finish_name in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
        raise RuntimeError(f"google blocked the response (finish_reason={finish_name}).")

    try:
        text = response.text or ""
    except Exception as exc:  # google-genai raises on blocked/non-text responses
        raise RuntimeError(f"google returned no usable text: {exc}") from exc

    if finish_name == "MAX_TOKENS":
        logger.warning("google response truncated (finish_reason=MAX_TOKENS).")
    return text
