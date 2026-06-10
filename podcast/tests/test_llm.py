from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.settings import Settings
from pipeline.llm import call_llm, load_system_prompt
from pipeline.reliability import ProviderCallError


def test_load_system_prompt_reads_base_prompt(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")

    prompt = load_system_prompt(root_dir=tmp_path)

    assert prompt == "Base persona"


def test_load_system_prompt_appends_episode_override(tmp_path):
    prompts_dir = tmp_path / "config" / "prompts"
    episodes_dir = prompts_dir / "episodes"
    episodes_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    (episodes_dir / "pilot.txt").write_text("Pilot context", encoding="utf-8")

    prompt = load_system_prompt("pilot", root_dir=tmp_path)

    assert prompt == "Base persona\n\nPilot context"


def test_load_system_prompt_sanitizes_episode_name(tmp_path):
    # write_episode_context writes the safe-name file; load_system_prompt must read the same.
    from integrations.openclaw_tools import write_episode_context

    prompts_dir = tmp_path / "config" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "base_system.txt").write_text("Base persona", encoding="utf-8")
    write_episode_context("Pilot Episode", "Episode research.", root_dir=tmp_path)

    prompt = load_system_prompt("Pilot Episode", root_dir=tmp_path)

    assert "Base persona" in prompt
    assert "Episode research." in prompt


def test_call_llm_dry_run_does_not_require_external_api(tmp_path):
    settings = Settings(root_dir=tmp_path, active_llm="dry-run", active_model="dry-run-v1")

    response = call_llm(
        [{"role": "user", "content": "Tell me what this backend does.", "created_at": "now"}],
        "System prompt",
        settings,
    )

    assert "dry-run mode" in response
    assert "Tell me what this backend does." in response


def test_call_llm_openai_responses_mode_extracts_output_text(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="openai",
        active_model="gpt-test",
        openai_api_key="test-key",
        openai_api_mode="responses",
        provider_max_retries=0,
    )

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["instructions"] == "System prompt"
            assert kwargs["input"][0]["content"] == "hello"
            return SimpleNamespace(output_text="spoken response")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"

        responses = FakeResponses()

    response = call_llm(
        [{"role": "user", "content": "hello"}],
        "System prompt",
        settings,
        client_factories={"openai": FakeOpenAI},
    )

    assert response == "spoken response"


def test_call_llm_openai_chat_mode_still_works(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="openai",
        active_model="gpt-test",
        openai_api_key="test-key",
        openai_api_mode="chat",
        provider_max_retries=0,
    )

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["messages"][0]["role"] == "system"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="chat response"))]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        chat = FakeChat()

    response = call_llm(
        [{"role": "user", "content": "hello"}],
        "System prompt",
        settings,
        client_factories={"openai": FakeOpenAI},
    )

    assert response == "chat response"


def test_call_llm_google_uses_supported_client_contract(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="google",
        active_model="gemini-test",
        google_api_key="test-key",
        provider_max_retries=0,
    )
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="google response")

    class FakeClient:
        models = FakeModels()

    response = call_llm(
        [{"role": "user", "content": "hello"}],
        "System prompt",
        settings,
        client_factories={"google": lambda **_kwargs: FakeClient()},
    )

    assert response == "google response"
    assert captured["model"] == "gemini-test"
    assert captured["config"]["system_instruction"] == "System prompt"


def test_call_llm_anthropic_joins_text_blocks_and_caches_system(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="anthropic",
        active_model="claude-test",
        anthropic_api_key="test-key",
        provider_max_retries=0,
        provider_max_output_tokens=1500,
    )
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="hello"), SimpleNamespace(type="text", text="world")],
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        messages = FakeMessages()

    response = call_llm(
        [{"role": "user", "content": "hi"}],
        "System prompt",
        settings,
        client_factories={"anthropic": FakeAnthropic},
    )

    assert response == "hello\nworld"
    assert captured["max_tokens"] == 1500
    assert captured["system"][0]["text"] == "System prompt"
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["init"]["max_retries"] == 0


def test_call_llm_anthropic_empty_response_raises(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="anthropic",
        active_model="claude-test",
        anthropic_api_key="test-key",
        provider_max_retries=0,
    )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            pass

        messages = SimpleNamespace(create=lambda **_kw: SimpleNamespace(stop_reason="end_turn", content=[]))

    with pytest.raises(RuntimeError, match="empty response"):
        call_llm([{"role": "user", "content": "hi"}], "sys", settings, client_factories={"anthropic": FakeAnthropic})


def test_call_llm_anthropic_refusal_raises_descriptive_error(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="anthropic",
        active_model="claude-test",
        anthropic_api_key="test-key",
        provider_max_retries=0,
    )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            pass

        messages = SimpleNamespace(create=lambda **_kw: SimpleNamespace(stop_reason="refusal", content=[]))

    with pytest.raises(RuntimeError, match="refusal"):
        call_llm([{"role": "user", "content": "hi"}], "sys", settings, client_factories={"anthropic": FakeAnthropic})


def test_call_llm_openai_chat_uses_max_completion_tokens_and_flags_content_filter(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="openai",
        active_model="gpt-x",
        openai_api_key="test-key",
        openai_api_mode="chat",
        provider_max_retries=0,
    )
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="content_filter", message=SimpleNamespace(content=""))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        chat = SimpleNamespace(completions=FakeCompletions())

    with pytest.raises(RuntimeError, match="content_filter"):
        call_llm([{"role": "user", "content": "hi"}], "sys", settings, client_factories={"openai": FakeOpenAI})

    assert "max_completion_tokens" in captured
    assert "max_tokens" not in captured


def test_call_llm_openai_responses_falls_back_to_output_array(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="openai",
        active_model="gpt-x",
        openai_api_key="test-key",
        openai_api_mode="responses",
        provider_max_retries=0,
    )

    class FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(
                output_text=None,
                output=[SimpleNamespace(content=[SimpleNamespace(type="output_text", text="assembled")])],
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        responses = FakeResponses()

    response = call_llm(
        [{"role": "user", "content": "hi"}],
        "sys",
        settings,
        client_factories={"openai": FakeOpenAI},
    )

    assert response == "assembled"


def test_call_llm_retries_transient_error_then_raises_provider_call_error(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.reliability.time.sleep", lambda _seconds: None)
    settings = Settings(
        root_dir=tmp_path,
        active_llm="openai",
        active_model="gpt-x",
        openai_api_key="test-key",
        openai_api_mode="chat",
        provider_max_retries=2,
    )
    calls = {"n": 0}

    class Boom(Exception):
        status_code = 503

    class FakeCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            raise Boom("server error")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        chat = SimpleNamespace(completions=FakeCompletions())

    with pytest.raises(ProviderCallError) as exc_info:
        call_llm([{"role": "user", "content": "hi"}], "sys", settings, client_factories={"openai": FakeOpenAI})

    assert calls["n"] == 3  # provider_max_retries=2 -> 3 attempts
    assert "Boom" in str(exc_info.value)  # original error type preserved


def test_call_llm_google_safety_block_raises(tmp_path):
    settings = Settings(
        root_dir=tmp_path,
        active_llm="google",
        active_model="gemini-x",
        google_api_key="test-key",
        provider_max_retries=0,
    )

    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="SAFETY"))], text=None)

    class FakeClient:
        models = FakeModels()

    with pytest.raises(RuntimeError, match="SAFETY"):
        call_llm(
            [{"role": "user", "content": "hi"}],
            "sys",
            settings,
            client_factories={"google": lambda **_kwargs: FakeClient()},
        )
