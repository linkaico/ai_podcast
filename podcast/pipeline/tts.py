from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from typing import Any, Callable, Iterable
from uuid import uuid4

from config.settings import Settings
from pipeline.reliability import is_transient_provider_error, retry_call


def speak(
    text: str,
    turn_index: int,
    settings: Settings,
    output_fn: Callable[[str], None] | None = None,
    output_dir: str | Path | None = None,
) -> Path | None:
    """Speak an AI turn or save a dry-run text artifact."""
    return speak_with_client(text, turn_index, settings, output_fn=output_fn, output_dir=output_dir)


def speak_with_client(
    text: str,
    turn_index: int,
    settings: Settings,
    output_fn: Callable[[str], None] | None = None,
    client_factory: Callable[[str], Any] | None = None,
    stream_fn: Callable[[Any], None] | None = None,
    http_post: Callable[..., Any] | None = None,
    output_dir: str | Path | None = None,
) -> Path | None:
    """Speak an AI turn, with injectable provider hooks for tests."""
    if settings.uses_dry_run_tts:
        voice_dir = Path(output_dir) if output_dir else settings.audio_output_dir
        voice_dir.mkdir(parents=True, exist_ok=True)
        output_path = voice_dir / f"turn_{turn_index:06d}.txt"
        _atomic_write_text(output_path, text)
        if output_fn:
            output_fn(f"[dry-run voice saved] {output_path}")
        return output_path

    if settings.uses_xai_tts:
        return _speak_with_xai(
            text,
            turn_index,
            settings,
            output_fn=output_fn,
            stream_fn=stream_fn,
            http_post=http_post,
            output_dir=output_dir,
        )

    return _speak_with_elevenlabs(
        text,
        turn_index,
        settings,
        output_fn=output_fn,
        client_factory=client_factory,
        stream_fn=stream_fn,
        output_dir=output_dir,
    )


def _speak_with_elevenlabs(
    text: str,
    turn_index: int,
    settings: Settings,
    output_fn: Callable[[str], None] | None = None,
    client_factory: Callable[[str], Any] | None = None,
    stream_fn: Callable[[Any], None] | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """Speak an AI turn with ElevenLabs."""
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required for live TTS.")
    if not settings.elevenlabs_voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID is required for live TTS.")

    if client_factory is None:
        try:
            from elevenlabs.client import ElevenLabs
        except ImportError as exc:
            raise RuntimeError("Install elevenlabs to use TTS_MODE=elevenlabs.") from exc

        client = ElevenLabs(api_key=settings.elevenlabs_api_key, timeout=settings.provider_timeout_seconds)
    else:
        client = client_factory(settings.elevenlabs_api_key)
    voice_dir = Path(output_dir) if output_dir else settings.audio_output_dir
    voice_dir.mkdir(parents=True, exist_ok=True)
    output_path = voice_dir / f"turn_{turn_index:06d}.{_elevenlabs_extension(settings.elevenlabs_output_format)}"

    audio_result = retry_call(
        lambda: client.text_to_speech.convert(
            voice_id=settings.elevenlabs_voice_id,
            text=text,
            model_id=settings.elevenlabs_model,
            output_format=settings.elevenlabs_output_format,
            voice_settings=_voice_settings(settings),
        ),
        provider="elevenlabs",
        stage="tts",
        max_retries=settings.provider_max_retries,
        timeout_seconds=settings.provider_timeout_seconds,
        retry_predicate=is_transient_provider_error,
    )
    audio_bytes = _coerce_audio_bytes(audio_result)
    if not audio_bytes:
        raise RuntimeError("ElevenLabs returned empty audio.")

    _atomic_write_bytes(output_path, audio_bytes)
    if output_fn:
        output_fn(f"[AI voice saved] {output_path}")

    _try_play_audio(audio_bytes, output_path, settings, output_fn, stream_fn=stream_fn)
    return output_path


def _speak_with_xai(
    text: str,
    turn_index: int,
    settings: Settings,
    output_fn: Callable[[str], None] | None = None,
    stream_fn: Callable[[Any], None] | None = None,
    http_post: Callable[..., Any] | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """Speak an AI turn with xAI's REST TTS endpoint."""
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is required for xAI TTS.")

    if http_post is None:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("Install requests to use TTS_MODE=xai.") from exc

        http_post = requests.post

    voice_dir = Path(output_dir) if output_dir else settings.audio_output_dir
    voice_dir.mkdir(parents=True, exist_ok=True)
    output_path = voice_dir / f"turn_{turn_index:06d}.mp3"
    response = retry_call(
        lambda: _post_xai_tts(text, settings, http_post),
        provider="xai",
        stage="tts",
        max_retries=settings.provider_max_retries,
        timeout_seconds=settings.provider_timeout_seconds,
        retry_predicate=is_transient_provider_error,
    )
    audio_bytes = _response_content(response)
    if not audio_bytes:
        raise RuntimeError("xAI returned empty audio.")

    _atomic_write_bytes(output_path, audio_bytes)
    if output_fn:
        output_fn(f"[AI voice saved] {output_path}")

    _try_play_audio(audio_bytes, output_path, settings, output_fn, stream_fn=stream_fn)
    return output_path


def _post_xai_tts(text: str, settings: Settings, http_post: Callable[..., Any]) -> Any:
    response = http_post(
        "https://api.x.ai/v1/tts",
        headers={
            "Authorization": f"Bearer {settings.xai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "voice_id": settings.xai_tts_voice,
            "language": settings.xai_tts_language,
        },
        timeout=settings.provider_timeout_seconds,
    )
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    return response


def _response_content(response: Any) -> bytes:
    if isinstance(response, bytes):
        return response
    if isinstance(response, bytearray):
        return bytes(response)
    content = getattr(response, "content", b"")
    return bytes(content) if isinstance(content, bytearray) else content


def _coerce_audio_bytes(audio_result: bytes | bytearray | Iterable[bytes]) -> bytes:
    if isinstance(audio_result, bytes):
        return audio_result
    if isinstance(audio_result, bytearray):
        return bytes(audio_result)
    return b"".join(chunk for chunk in audio_result if chunk)


def _elevenlabs_extension(output_format: str) -> str:
    extension = output_format.split("_", 1)[0].lower()
    if extension not in {"mp3", "pcm", "ulaw", "alaw", "opus"}:
        raise RuntimeError(f"Unsupported ElevenLabs output format: {output_format}")
    return extension


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary_path.write_bytes(content)
    os.replace(temporary_path, path)


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    os.replace(temporary_path, path)


def _voice_settings(settings: Settings) -> Any:
    try:
        from elevenlabs import VoiceSettings
    except ImportError:
        return {
            "stability": settings.elevenlabs_stability,
            "similarity_boost": settings.elevenlabs_similarity_boost,
            "style": settings.elevenlabs_style,
            "speed": settings.elevenlabs_speed,
        }
    return VoiceSettings(
        stability=settings.elevenlabs_stability,
        similarity_boost=settings.elevenlabs_similarity_boost,
        style=settings.elevenlabs_style,
        speed=settings.elevenlabs_speed,
    )


def _try_play_audio(
    audio_bytes: bytes,
    output_path: Path,
    settings: Settings,
    output_fn: Callable[[str], None] | None,
    stream_fn: Callable[[Any], None] | None = None,
) -> None:
    if settings.playback_mode == "file-only":
        return

    try:
        if settings.playback_mode == "sdk":
            if stream_fn is None:
                from elevenlabs import play

                stream_fn = play
            stream_fn(audio_bytes)
        elif settings.playback_mode == "system":
            _system_play(output_path)
        if output_fn:
            output_fn("[AI voice playback attempted]")
    except Exception as exc:  # pragma: no cover - playback is best-effort.
        if output_fn:
            output_fn(f"[AI voice playback skipped] {exc}")


def _system_play(output_path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["afplay", str(output_path)], check=True)
    else:
        raise RuntimeError("system playback is currently only implemented for macOS afplay.")
