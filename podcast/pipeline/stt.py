from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from config.settings import Settings
from pipeline.reliability import is_transient_provider_error, retry_call


def capture_text_turn(input_fn: Callable[[str], str] = input, prompt: str = "FLORIAN> ") -> str:
    """Read a host turn in dry-run mode."""
    return input_fn(prompt).strip()


def list_input_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Install sounddevice to list audio input devices.") from exc

    devices = sd.query_devices()
    result = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0:
            result.append(
                {
                    "index": index,
                    "name": device.get("name", ""),
                    "max_input_channels": device.get("max_input_channels", 0),
                    "default_samplerate": device.get("default_samplerate"),
                }
            )
    return result


def record_until_keypress(
    settings: Settings,
    turn_index: int = 0,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] | None = None,
    output_dir: str | Path | None = None,
) -> str:
    """Record microphone audio until ENTER is pressed and save a WAV file."""
    if not settings.uses_microphone_input:
        raise RuntimeError("Microphone recording requires INPUT_MODE=mic.")

    try:
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "Install sounddevice, soundfile, and numpy to use INPUT_MODE=mic."
        ) from exc

    recording_dir = Path(output_dir) if output_dir else settings.audio_input_dir
    recording_dir.mkdir(parents=True, exist_ok=True)
    audio_chunks = []

    def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status and output_fn:
            output_fn(f"[audio status] {status}")
        audio_chunks.append(indata.copy())

    if output_fn:
        output_fn("Recording host audio. Press ENTER to stop.")

    device = _input_device(settings.audio_device_index)
    with sd.InputStream(
        samplerate=settings.audio_sample_rate,
        channels=settings.audio_channels,
        dtype="int16",
        device=device,
        callback=callback,
    ):
        input_fn("")

    if not audio_chunks:
        raise RuntimeError("No microphone audio was captured.")

    audio_data = np.concatenate(audio_chunks, axis=0)
    if audio_data.size == 0:
        raise RuntimeError("Captured microphone audio was empty.")

    path = recording_dir / f"turn_{turn_index:06d}.wav"
    temporary_path = path.with_name(f".{path.stem}.{uuid4().hex}.tmp.wav")
    sf.write(temporary_path, audio_data, settings.audio_sample_rate)
    os.replace(temporary_path, path)
    return str(path)


def transcribe(audio_path: str | Path, settings: Settings) -> str:
    """Transcribe an audio file or read a dry-run text transcript."""
    return transcribe_with_client(audio_path, settings)


def transcribe_with_client(
    audio_path: str | Path,
    settings: Settings,
    client_factory: Callable[[str], Any] | None = None,
    options_factory: Callable[..., Any] | None = None,
    http_post: Callable[..., Any] | None = None,
) -> str:
    """Transcribe an audio file, with injectable provider hooks for tests."""
    path = Path(audio_path)
    if settings.uses_text_input and path.suffix == ".txt":
        return path.read_text(encoding="utf-8").strip()

    if settings.uses_xai_stt:
        return _transcribe_with_xai(path, settings, http_post=http_post)

    return _transcribe_with_deepgram_provider(
        path,
        settings,
        client_factory=client_factory,
        options_factory=options_factory,
    )


def _transcribe_with_deepgram_provider(
    path: Path,
    settings: Settings,
    client_factory: Callable[[str], Any] | None = None,
    options_factory: Callable[..., Any] | None = None,
) -> str:
    """Transcribe an audio file with Deepgram."""
    if not settings.deepgram_api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is required for live transcription.")

    if not path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {path}")
    if path.stat().st_size == 0:
        raise RuntimeError(f"Audio file is empty: {path}")

    using_default_client = client_factory is None
    if using_default_client:
        try:
            from deepgram import DeepgramClient
        except ImportError as exc:
            raise RuntimeError("Install deepgram-sdk to use INPUT_MODE=mic transcription.") from exc

        client = DeepgramClient(api_key=settings.deepgram_api_key, timeout=settings.provider_timeout_seconds)
        options = None
    else:
        client = client_factory(settings.deepgram_api_key)
        options = options_factory(model=settings.deepgram_model, language="en", smart_format=True) if options_factory else None

    with path.open("rb") as audio_file:
        buffer_data = audio_file.read()

    response = retry_call(
        lambda: _transcribe_with_deepgram(client, buffer_data, options, settings),
        provider="deepgram",
        stage="transcribe",
        max_retries=settings.provider_max_retries,
        timeout_seconds=settings.provider_timeout_seconds,
        retry_predicate=is_transient_provider_error,
    )
    transcript = _extract_transcript(response)
    if not transcript:
        raise RuntimeError("Deepgram returned an empty transcript.")
    return transcript


def _transcribe_with_xai(
    path: Path,
    settings: Settings,
    http_post: Callable[..., Any] | None = None,
) -> str:
    """Transcribe an audio file with xAI's REST STT endpoint."""
    if not settings.xai_api_key:
        raise RuntimeError("XAI_API_KEY is required for xAI transcription.")
    if not path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {path}")
    if path.stat().st_size == 0:
        raise RuntimeError(f"Audio file is empty: {path}")

    if http_post is None:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("Install requests to use STT_MODE=xai.") from exc

        http_post = requests.post

    buffer_data = path.read_bytes()
    content_type = _audio_content_type(path)
    response = retry_call(
        lambda: _post_xai_stt(path, buffer_data, content_type, settings, http_post),
        provider="xai",
        stage="transcribe",
        max_retries=settings.provider_max_retries,
        timeout_seconds=settings.provider_timeout_seconds,
        retry_predicate=is_transient_provider_error,
    )
    transcript = _extract_xai_transcript(response)
    if not transcript:
        raise RuntimeError("xAI returned an empty transcript.")
    return transcript


def _input_device(audio_device_index: str) -> int | str | None:
    value = audio_device_index.strip()
    if not value or value.lower() == "default":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _transcribe_with_deepgram(client: Any, buffer_data: bytes, options: Any, settings: Settings) -> Any:
    source = {"buffer": buffer_data}
    listen = getattr(client, "listen", None)
    if listen is None:
        raise RuntimeError("Deepgram client does not expose a listen API.")

    v1 = getattr(listen, "v1", None)
    media = getattr(v1, "media", None)
    if media is not None:
        return media.transcribe_file(
            request=buffer_data,
            model=settings.deepgram_model,
            language="en",
            smart_format=True,
            request_options={"timeout_in_seconds": settings.provider_timeout_seconds},
        )

    rest = getattr(listen, "rest", None)
    if rest is not None:
        return rest.v("1").transcribe_file(source, options)

    prerecorded = getattr(listen, "prerecorded", None)
    if prerecorded is not None:
        return prerecorded.v("1").transcribe_file(source, options)

    raise RuntimeError("Deepgram client does not expose a prerecorded transcription API.")


def _post_xai_stt(
    path: Path,
    buffer_data: bytes,
    content_type: str,
    settings: Settings,
    http_post: Callable[..., Any],
) -> Any:
    response = http_post(
        "https://api.x.ai/v1/stt",
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        data={"format": "true", "language": settings.xai_stt_language},
        files={"file": (path.name, buffer_data, content_type)},
        timeout=settings.provider_timeout_seconds,
    )
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    return response


def _audio_content_type(path: Path) -> str:
    explicit_types = {
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
    }
    return explicit_types.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _extract_transcript(response: Any) -> str:
    if isinstance(response, dict):
        try:
            return response["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        except (KeyError, IndexError, TypeError):
            return ""

    try:
        channels = response.results.channels
        alternatives = channels[0].alternatives
        return alternatives[0].transcript.strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _extract_xai_transcript(response: Any) -> str:
    payload = response
    if not isinstance(response, dict):
        try:
            payload = response.json()
        except (AttributeError, ValueError, TypeError):
            return ""
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""
