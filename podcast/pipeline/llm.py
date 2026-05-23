from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from config.settings import PROJECT_ROOT, Settings, load_settings
from pipeline.reliability import retry_call


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
        episode_path = prompts_dir / "episodes" / f"{episode_name}.txt"
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
    client = factory(api_key=settings.anthropic_api_key, timeout=settings.provider_timeout_seconds)
    response = client.messages.create(
        model=settings.active_model,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )
    return "\n".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


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
    client = factory(api_key=settings.openai_api_key, timeout=settings.provider_timeout_seconds)
    if settings.openai_api_mode == "responses":
        response = client.responses.create(
            model=settings.active_model,
            instructions=system_prompt,
            input=messages,
            max_output_tokens=1024,
        )
        return _extract_openai_responses_text(response)

    response = client.chat.completions.create(
        model=settings.active_model,
        max_tokens=1024,
        messages=[{"role": "system", "content": system_prompt}, *messages],
    )
    return _extract_openai_chat_text(response)


def _call_google(
    messages: list[dict[str, str]],
    system_prompt: str,
    settings: Settings,
    client_factory: Callable[..., Any] | None = None,
) -> str:
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise RuntimeError("Install google-generativeai to use ACTIVE_LLM=google.") from exc

    if client_factory is None:
        genai.configure(api_key=settings.google_api_key)
        model = genai.GenerativeModel(model_name=settings.active_model, system_instruction=system_prompt)
    else:
        model = client_factory(model_name=settings.active_model, system_instruction=system_prompt)
    chat_history = [
        {
            "role": "model" if message["role"] == "assistant" else "user",
            "parts": [message["content"]],
        }
        for message in messages[:-1]
    ]
    chat = model.start_chat(history=chat_history)
    prompt = messages[-1]["content"] if messages else ""
    response = chat.send_message(prompt, request_options={"timeout": settings.provider_timeout_seconds})
    return response.text or ""


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
