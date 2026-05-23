from __future__ import annotations

import pytest

from pipeline.reliability import ProviderCallError, retry_call, structured_error


def test_retry_call_retries_transient_failure():
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")
        return "ok"

    result = retry_call(
        operation,
        provider="provider",
        stage="stage",
        max_retries=1,
        timeout_seconds=10,
        sleep_fn=lambda _seconds: None,
    )

    assert result == "ok"
    assert calls["count"] == 2


def test_retry_call_raises_structured_provider_error():
    with pytest.raises(ProviderCallError) as exc:
        retry_call(
            lambda: (_ for _ in ()).throw(RuntimeError("nope")),
            provider="provider",
            stage="stage",
            max_retries=1,
            timeout_seconds=10,
            sleep_fn=lambda _seconds: None,
        )

    assert exc.value.attempts == 2
    assert structured_error(exc.value, "stage")["provider"] == "provider"


def test_retry_call_does_not_retry_non_transient_failure():
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        raise ValueError("bad request")

    with pytest.raises(ProviderCallError):
        retry_call(
            operation,
            provider="provider",
            stage="stage",
            max_retries=3,
            timeout_seconds=10,
            retry_predicate=lambda _exc: False,
            sleep_fn=lambda _seconds: None,
        )

    assert calls["count"] == 1
