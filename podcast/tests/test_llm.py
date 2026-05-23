from __future__ import annotations

from types import SimpleNamespace

from config.settings import Settings
from pipeline.llm import call_llm, load_system_prompt


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
