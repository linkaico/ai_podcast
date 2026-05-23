from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeVar


T = TypeVar("T")


@dataclass
class ProviderCallError(RuntimeError):
    provider: str
    stage: str
    attempts: int
    original_error: str

    def __str__(self) -> str:
        return (
            f"{self.provider} {self.stage} failed after {self.attempts} attempt(s): "
            f"{self.original_error}"
        )

    def to_event(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "stage": self.stage,
            "attempts": self.attempts,
            "error": self.original_error,
        }


def retry_call(
    operation: Callable[[], T],
    *,
    provider: str,
    stage: str,
    max_retries: int,
    timeout_seconds: int,
    retry_exceptions: Iterable[type[BaseException]] = (Exception,),
    sleep_fn: Callable[[float], None] = time.sleep,
    backoff_seconds: float = 0.5,
) -> T:
    """Run a provider operation with a small retry budget.

    The sync SDKs used here do not expose one common timeout interface, so
    timeout_seconds is passed to clients where supported and preserved for
    diagnostics here.
    """
    del timeout_seconds
    exceptions = tuple(retry_exceptions)
    attempts = max_retries + 1
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except exceptions as exc:
            last_error = exc
            if attempt >= attempts:
                break
            sleep_fn(backoff_seconds * attempt)

    raise ProviderCallError(
        provider=provider,
        stage=stage,
        attempts=attempts,
        original_error=str(last_error) if last_error else "unknown error",
    )


def structured_error(exc: BaseException, stage: str) -> dict[str, Any]:
    if isinstance(exc, ProviderCallError):
        payload = exc.to_event()
        payload["stage"] = stage or payload.get("stage")
        return payload
    return {"stage": stage, "error": str(exc), "type": exc.__class__.__name__}
