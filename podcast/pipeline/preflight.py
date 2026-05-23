from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from config.settings import Settings, SettingsError
from pipeline.stt import list_input_devices


def run_preflight(settings: Settings) -> dict[str, Any]:
    checks = [
        _check_settings(settings),
        _check_base_prompt(settings),
        _check_writable_dir("sessions", settings.sessions_dir),
        _check_writable_dir("audio_input", settings.audio_input_dir),
        _check_writable_dir("audio_output", settings.audio_output_dir),
        _check_writable_dir("exports", settings.root_dir / "exports"),
    ]

    checks.extend(_check_provider_sdks(settings))
    if settings.uses_microphone_input:
        checks.append(_check_audio_device(settings))

    return {
        "ok": all(check["status"] != "error" for check in checks),
        "checks": checks,
    }


def format_preflight_report(result: dict[str, Any]) -> str:
    lines = ["Preflight checks:"]
    for check in result["checks"]:
        lines.append(f"- {check['status'].upper()} {check['name']}: {check['message']}")
    lines.append("Result: OK" if result["ok"] else "Result: FAILED")
    return "\n".join(lines)


def _check_settings(settings: Settings) -> dict[str, str]:
    try:
        settings.validate_runtime()
    except SettingsError as exc:
        return _check("settings", "error", str(exc))
    return _check("settings", "ok", "runtime settings are valid")


def _check_base_prompt(settings: Settings) -> dict[str, str]:
    path = settings.prompts_dir / "base_system.txt"
    if not path.exists():
        return _check("base_prompt", "error", f"missing base prompt: {path}")
    if not path.read_text(encoding="utf-8").strip():
        return _check("base_prompt", "error", f"base prompt is empty: {path}")
    return _check("base_prompt", "ok", str(path))


def _check_writable_dir(name: str, path: Path) -> dict[str, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _check(name, "error", f"not writable: {path} ({exc})")
    return _check(name, "ok", f"writable: {path}")


def _check_provider_sdks(settings: Settings) -> list[dict[str, str]]:
    checks = []
    if settings.active_llm == "anthropic" and not settings.uses_realtime:
        checks.append(_check_import("anthropic", "anthropic"))
    if settings.active_llm == "openai" and not settings.uses_realtime:
        checks.append(_check_import("openai", "openai"))
    if settings.active_llm == "google" and not settings.uses_realtime:
        checks.append(_check_import("google.genai", "google-genai"))
    if settings.uses_microphone_input:
        checks.extend(
            [
                _check_import("sounddevice", "sounddevice"),
                _check_import("soundfile", "soundfile"),
                _check_import("numpy", "numpy"),
            ]
        )
        if settings.uses_realtime:
            checks.append(_check_import("websockets", "websockets"))
        elif settings.uses_deepgram_stt:
            checks.append(_check_import("deepgram", "deepgram-sdk"))
        elif settings.uses_xai_stt:
            checks.append(_check_import("requests", "requests"))
    if settings.uses_elevenlabs_tts and not settings.uses_realtime:
        checks.append(_check_import("elevenlabs", "elevenlabs"))
    if settings.uses_xai_tts and not settings.uses_realtime:
        checks.append(_check_import("requests", "requests"))
    return checks


def _check_import(module_name: str, package_name: str) -> dict[str, str]:
    if importlib.util.find_spec(module_name) is None:
        return _check(f"sdk:{package_name}", "error", f"install missing package: {package_name}")
    return _check(f"sdk:{package_name}", "ok", "installed")


def _check_audio_device(settings: Settings) -> dict[str, str]:
    try:
        devices = list_input_devices()
    except RuntimeError as exc:
        return _check("audio_device", "error", str(exc))

    if settings.audio_device_index.lower() == "default":
        return _check("audio_device", "ok", "using default input device")

    target = settings.audio_device_index
    for device in devices:
        if str(device.get("index")) == target or str(device.get("name")) == target:
            return _check("audio_device", "ok", f"found input device: {target}")
    return _check("audio_device", "error", f"input device not found: {target}")


def _check(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}
