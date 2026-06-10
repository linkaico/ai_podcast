from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeVar


T = TypeVar("T")


def _run_with_deadline(operation: Callable[[], T], timeout_seconds: int) -> T:
    """Run `operation` under a wall-clock deadline using a daemon thread.

    A daemon thread can't block interpreter exit, so a genuinely hung call is
    abandoned (it dies with the process / when its own socket timeout fires).
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = operation()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(f"operation exceeded {timeout_seconds}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


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
    retry_predicate: Callable[[BaseException], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    backoff_seconds: float = 0.5,
) -> T:
    """Run a provider operation with a small retry budget and a wall-clock deadline.

    Each attempt runs under `timeout_seconds`; a timeout is treated as a transient
    error (see `is_transient_provider_error`) and retried within the budget. This is
    a backstop on top of the per-SDK socket timeouts.
    """
    exceptions = tuple(retry_exceptions)
    attempts = max_retries + 1
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return _run_with_deadline(operation, timeout_seconds)
        except exceptions as exc:
            last_error = exc
            if attempt >= attempts or (retry_predicate is not None and not retry_predicate(exc)):
                break
            sleep_fn(backoff_seconds * attempt)

    raise ProviderCallError(
        provider=provider,
        stage=stage,
        attempts=attempts,
        original_error=(f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"),
    )


def is_transient_provider_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    return status_code in {408, 409, 429} or isinstance(status_code, int) and status_code >= 500


def structured_error(exc: BaseException, stage: str) -> dict[str, Any]:
    if isinstance(exc, ProviderCallError):
        payload = exc.to_event()
        payload["stage"] = stage or payload.get("stage")
        return payload
    return {"stage": stage, "error": str(exc), "type": exc.__class__.__name__}
