from __future__ import annotations

import os
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SettingsError(RuntimeError):
    """Raised when runtime configuration is missing or invalid."""


def _load_dotenv(root_dir: Path) -> None:
    """Load .env if python-dotenv is installed; otherwise keep env-only config."""
    dotenv_path = root_dir / ".env"
    if not dotenv_path.exists():
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(dotenv_path)


def _getenv(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    return value if value else default


def _getenv_int(name: str, default: int) -> int:
    raw_value = _getenv(name, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got '{raw_value}'.") from exc


def _getenv_float(name: str, default: float) -> float:
    raw_value = _getenv(name, str(default))
    try:
        return float(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number, got '{raw_value}'.") from exc


def _getenv_bool(name: str, default: bool) -> bool:
    raw_value = _getenv(name, "true" if default else "false").lower()
    if raw_value in {"1", "true", "yes", "y", "on"}:
        return True
    if raw_value in {"0", "false", "no", "n", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false, got '{raw_value}'.")


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    active_llm: str
    active_model: str
    conversation_mode: str = "dry-run"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    xai_api_key: str = ""
    audio_device_index: str = "default"
    output_audio_device: str = "default"
    input_mode: str = "text"
    stt_mode: str = "deepgram"
    tts_mode: str = "dry-run"
    deepgram_model: str = "nova-3"
    elevenlabs_model: str = "eleven_flash_v2_5"
    xai_stt_language: str = "en"
    xai_tts_voice: str = "eve"
    xai_tts_language: str = "en"
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_max_record_seconds: int = 600
    confirm_transcript: bool = True
    provider_timeout_seconds: int = 60
    provider_max_retries: int = 1
    provider_max_output_tokens: int = 2048
    openai_api_mode: str = "responses"
    playback_mode: str = "file-only"
    elevenlabs_output_format: str = "mp3_22050_32"
    elevenlabs_stability: float = 0.45
    elevenlabs_similarity_boost: float = 0.80
    elevenlabs_style: float = 0.35
    elevenlabs_speed: float = 1.0
    realtime_model: str = "gpt-realtime"
    realtime_voice: str = "marin"
    realtime_transcription_model: str = "gpt-4o-transcribe"
    realtime_vad_mode: str = "semantic_vad"
    realtime_sample_rate: int = 24000

    @property
    def prompts_dir(self) -> Path:
        return self.root_dir / "config" / "prompts"

    @property
    def sessions_dir(self) -> Path:
        return self.root_dir / "sessions"

    @property
    def audio_input_dir(self) -> Path:
        return self.root_dir / "audio" / "input"

    @property
    def audio_output_dir(self) -> Path:
        return self.root_dir / "audio" / "output"

    @property
    def is_dry_run(self) -> bool:
        return self.active_llm in {"dry-run", "dry_run", "local"}

    @property
    def uses_text_input(self) -> bool:
        return self.input_mode == "text"

    @property
    def uses_microphone_input(self) -> bool:
        return self.input_mode == "mic"

    @property
    def uses_deepgram_stt(self) -> bool:
        return self.stt_mode == "deepgram"

    @property
    def uses_xai_stt(self) -> bool:
        return self.stt_mode == "xai"

    @property
    def uses_dry_run_tts(self) -> bool:
        return self.tts_mode in {"dry-run", "dry_run", "text"}

    @property
    def uses_elevenlabs_tts(self) -> bool:
        return self.tts_mode == "elevenlabs"

    @property
    def uses_xai_tts(self) -> bool:
        return self.tts_mode == "xai"

    @property
    def uses_live_tts(self) -> bool:
        return not self.uses_dry_run_tts

    @property
    def uses_realtime(self) -> bool:
        return self.conversation_mode == "realtime"

    def validate_for_active_provider(self) -> None:
        required_by_provider = {
            "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            "openai": ("OPENAI_API_KEY", self.openai_api_key),
            "google": ("GOOGLE_API_KEY", self.google_api_key),
        }

        if self.is_dry_run:
            return

        if self.active_llm not in required_by_provider:
            valid = "dry-run, anthropic, openai, google"
            raise SettingsError(f"Unsupported ACTIVE_LLM '{self.active_llm}'. Expected one of: {valid}.")

        env_name, value = required_by_provider[self.active_llm]
        if not value:
            raise SettingsError(f"{env_name} is required when ACTIVE_LLM={self.active_llm}.")

        if not self.active_model or self.active_model.lower().startswith("dry-run"):
            raise SettingsError(
                f"ACTIVE_MODEL must be a real {self.active_llm} model id when "
                f"ACTIVE_LLM={self.active_llm} (got '{self.active_model}'). "
                f"E.g. anthropic=claude-opus-4-8, openai=gpt-4o, google=gemini-2.0-flash."
            )

    def validate_audio_modes(self) -> None:
        if self.conversation_mode not in {"dry-run", "chained", "realtime"}:
            raise SettingsError("CONVERSATION_MODE must be one of: dry-run, chained, realtime.")
        if self.input_mode not in {"text", "mic"}:
            raise SettingsError("INPUT_MODE must be either 'text' or 'mic'.")
        if self.stt_mode not in {"deepgram", "xai"}:
            raise SettingsError("STT_MODE must be either 'deepgram' or 'xai'.")
        if not self.uses_dry_run_tts and not self.uses_elevenlabs_tts and not self.uses_xai_tts:
            raise SettingsError("TTS_MODE must be either 'dry-run', 'elevenlabs', or 'xai'.")
        if self.openai_api_mode not in {"responses", "chat"}:
            raise SettingsError("OPENAI_API_MODE must be either 'responses' or 'chat'.")
        if self.playback_mode not in {"file-only", "sdk", "system"}:
            raise SettingsError("PLAYBACK_MODE must be one of: file-only, sdk, system.")
        if self.audio_sample_rate <= 0:
            raise SettingsError("AUDIO_SAMPLE_RATE must be greater than zero.")
        if self.audio_channels <= 0:
            raise SettingsError("AUDIO_CHANNELS must be greater than zero.")
        if self.audio_max_record_seconds <= 0:
            raise SettingsError("AUDIO_MAX_RECORD_SECONDS must be greater than zero.")
        if self.provider_timeout_seconds <= 0:
            raise SettingsError("PROVIDER_TIMEOUT_SECONDS must be greater than zero.")
        if self.provider_max_retries < 0:
            raise SettingsError("PROVIDER_MAX_RETRIES must be zero or greater.")
        if self.provider_max_output_tokens <= 0:
            raise SettingsError("PROVIDER_MAX_OUTPUT_TOKENS must be greater than zero.")
        if self.conversation_mode == "dry-run" and (
            not self.is_dry_run or not self.uses_text_input or not self.uses_dry_run_tts
        ):
            raise SettingsError(
                "CONVERSATION_MODE=dry-run requires ACTIVE_LLM=dry-run, INPUT_MODE=text, "
                "and TTS_MODE=dry-run; use CONVERSATION_MODE=chained for live providers."
            )
        if self.uses_realtime:
            if not self.openai_api_key:
                raise SettingsError("OPENAI_API_KEY is required when CONVERSATION_MODE=realtime.")
            if not self.uses_microphone_input:
                raise SettingsError("INPUT_MODE=mic is required when CONVERSATION_MODE=realtime.")
            if self.realtime_vad_mode not in {"semantic_vad", "server_vad"}:
                raise SettingsError("REALTIME_VAD_MODE must be either 'semantic_vad' or 'server_vad'.")
            if self.realtime_sample_rate != 24000:
                raise SettingsError("REALTIME_SAMPLE_RATE must be 24000 for PCM realtime audio.")
            return
        if self.uses_microphone_input and self.uses_deepgram_stt and not self.deepgram_api_key:
            raise SettingsError("DEEPGRAM_API_KEY is required when INPUT_MODE=mic.")
        if self.uses_microphone_input and self.uses_xai_stt and not self.xai_api_key:
            raise SettingsError("XAI_API_KEY is required when INPUT_MODE=mic and STT_MODE=xai.")
        if self.uses_elevenlabs_tts:
            if not self.elevenlabs_api_key:
                raise SettingsError("ELEVENLABS_API_KEY is required when TTS_MODE=elevenlabs.")
            if not self.elevenlabs_voice_id:
                raise SettingsError("ELEVENLABS_VOICE_ID is required when TTS_MODE=elevenlabs.")
        if self.uses_xai_tts and not self.xai_api_key:
            raise SettingsError("XAI_API_KEY is required when TTS_MODE=xai.")

    def validate_runtime(self) -> None:
        if not self.uses_realtime:
            self.validate_for_active_provider()
        self.validate_audio_modes()

    def with_overrides(self, **kwargs) -> "Settings":
        updated = replace(self, **kwargs)
        updated.validate_runtime()
        return updated


def load_settings(root_dir: str | Path | None = None, validate: bool = True) -> Settings:
    root = Path(root_dir).resolve() if root_dir else PROJECT_ROOT
    _load_dotenv(root)

    settings = Settings(
        root_dir=root,
        active_llm=_getenv("ACTIVE_LLM", "dry-run").lower(),
        active_model=_getenv("ACTIVE_MODEL", "dry-run-v1"),
        conversation_mode=_getenv("CONVERSATION_MODE", "dry-run").lower(),
        anthropic_api_key=_getenv("ANTHROPIC_API_KEY"),
        openai_api_key=_getenv("OPENAI_API_KEY"),
        google_api_key=_getenv("GOOGLE_API_KEY"),
        deepgram_api_key=_getenv("DEEPGRAM_API_KEY"),
        elevenlabs_api_key=_getenv("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=_getenv("ELEVENLABS_VOICE_ID"),
        xai_api_key=_getenv("XAI_API_KEY"),
        audio_device_index=_getenv("AUDIO_DEVICE_INDEX", "default"),
        output_audio_device=_getenv("OUTPUT_AUDIO_DEVICE", "default"),
        input_mode=_getenv("INPUT_MODE", "text").lower(),
        stt_mode=_getenv("STT_MODE", "deepgram").lower(),
        tts_mode=_getenv("TTS_MODE", "dry-run").lower(),
        deepgram_model=_getenv("DEEPGRAM_MODEL", "nova-3"),
        elevenlabs_model=_getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        xai_stt_language=_getenv("XAI_STT_LANGUAGE", "en") or "en",
        xai_tts_voice=_getenv("XAI_TTS_VOICE", "eve") or "eve",
        xai_tts_language=_getenv("XAI_TTS_LANGUAGE", "en") or "en",
        audio_sample_rate=_getenv_int("AUDIO_SAMPLE_RATE", 16000),
        audio_channels=_getenv_int("AUDIO_CHANNELS", 1),
        audio_max_record_seconds=_getenv_int("AUDIO_MAX_RECORD_SECONDS", 600),
        confirm_transcript=_getenv_bool("CONFIRM_TRANSCRIPT", True),
        provider_timeout_seconds=_getenv_int("PROVIDER_TIMEOUT_SECONDS", 60),
        provider_max_retries=_getenv_int("PROVIDER_MAX_RETRIES", 1),
        provider_max_output_tokens=_getenv_int("PROVIDER_MAX_OUTPUT_TOKENS", 2048),
        openai_api_mode=_getenv("OPENAI_API_MODE", "responses").lower(),
        playback_mode=_getenv("PLAYBACK_MODE", "file-only").lower(),
        elevenlabs_output_format=_getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_22050_32"),
        elevenlabs_stability=_getenv_float("ELEVENLABS_STABILITY", 0.45),
        elevenlabs_similarity_boost=_getenv_float("ELEVENLABS_SIMILARITY_BOOST", 0.80),
        elevenlabs_style=_getenv_float("ELEVENLABS_STYLE", 0.35),
        elevenlabs_speed=_getenv_float("ELEVENLABS_SPEED", 1.0),
        realtime_model=_getenv("REALTIME_MODEL", "gpt-realtime"),
        realtime_voice=_getenv("REALTIME_VOICE", "marin"),
        realtime_transcription_model=_getenv("REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
        realtime_vad_mode=_getenv("REALTIME_VAD_MODE", "semantic_vad").lower(),
        realtime_sample_rate=_getenv_int("REALTIME_SAMPLE_RATE", 24000),
    )
    if validate:
        settings.validate_runtime()
    return settings
