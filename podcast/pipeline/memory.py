from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.settings import PROJECT_ROOT


Turn = dict[str, str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_episode_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned.strip("._") or "default"


@dataclass
class ConversationMemory:
    episode_name: str
    max_turns: int = 40
    sessions_dir: Path | None = None
    session_file: Path | None = None
    root_dir: Path | None = None
    now_fn: Callable[[], datetime] = _utc_now
    history: list[Turn] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.episode_name = _safe_episode_name(self.episode_name)
        self.sessions_dir = self.sessions_dir or PROJECT_ROOT / "sessions"
        self.root_dir = self.root_dir or _infer_root_dir(self.sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        if self.session_file is None:
            timestamp = self.now_fn().strftime("%Y%m%d_%H%M%S")
            self.session_file = self.sessions_dir / f"{self.episode_name}_{timestamp}.json"
        else:
            self.session_file = Path(self.session_file)

    @classmethod
    def from_session_file(
        cls,
        path: str | Path,
        max_turns: int = 40,
        now_fn: Callable[[], datetime] = _utc_now,
    ) -> "ConversationMemory":
        session_path = Path(path)
        if not session_path.exists():
            raise FileNotFoundError(f"Session file does not exist: {session_path}")

        payload = json.loads(session_path.read_text(encoding="utf-8"))
        episode_name = payload.get("episode")
        if not episode_name:
            raise ValueError(f"Session file is missing an episode name: {session_path}")

        history = payload.get("history", [])
        if not isinstance(history, list):
            raise ValueError(f"Session history must be a list: {session_path}")
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []

        return cls(
            episode_name=episode_name,
            max_turns=max_turns,
            sessions_dir=session_path.parent,
            session_file=session_path,
            root_dir=_infer_root_dir(session_path.parent),
            now_fn=now_fn,
            history=history,
            events=events,
        )

    @classmethod
    def latest_for_episode(
        cls,
        episode_name: str,
        sessions_dir: str | Path,
        max_turns: int = 40,
        now_fn: Callable[[], datetime] = _utc_now,
    ) -> "ConversationMemory":
        safe_name = _safe_episode_name(episode_name)
        session_root = Path(sessions_dir)
        matches = sorted(session_root.glob(f"{safe_name}_*.json"))
        if not matches:
            raise FileNotFoundError(f"No sessions found for episode '{safe_name}' in {session_root}.")
        return cls.from_session_file(matches[-1], max_turns=max_turns, now_fn=now_fn)

    def add(self, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be 'user' or 'assistant'")
        if not content or not content.strip():
            raise ValueError("content must not be empty")

        turn = {
            "role": role,
            "content": content.strip(),
            "created_at": self.now_fn().isoformat(),
        }
        if metadata:
            turn["metadata"] = metadata
        self.history.append(turn)
        self._trim()
        self._save()

    def get(self) -> list[Turn]:
        return list(self.history)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": turn["role"], "content": turn["content"]} for turn in self.history]

    def next_turn_index(self) -> int:
        return sum(1 for turn in self.history if turn.get("role") == "assistant")

    def add_event(
        self,
        stage: str,
        status: str,
        turn_index: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "stage": stage,
            "status": status,
            "created_at": self.now_fn().isoformat(),
        }
        if turn_index is not None:
            event["turn_index"] = turn_index
        if details:
            event["details"] = details
        self.events.append(event)
        self._save()

    def _trim(self) -> None:
        max_messages = self.max_turns * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    def _save(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "episode": self.episode_name,
            "saved_at": self.now_fn().isoformat(),
            "history": self.history,
            "events": self.events,
            "artifacts": self.artifacts(),
        }
        self.session_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def artifacts(self) -> dict[str, list[str]]:
        root = self.root_dir or _infer_root_dir(self.sessions_dir)
        audio_input_dir = root / "audio" / "input"
        audio_output_dir = root / "audio" / "output"

        artifacts = {
            "input_wav": _relative_paths(root, audio_input_dir.glob("*.wav")),
            "output_mp3": _relative_paths(root, audio_output_dir.glob("*.mp3")),
            "dryrun_text": _relative_paths(root, audio_output_dir.glob("*.txt")),
        }

        metadata_paths: dict[str, set[str]] = {
            "input_wav": set(artifacts["input_wav"]),
            "output_mp3": set(artifacts["output_mp3"]),
            "dryrun_text": set(artifacts["dryrun_text"]),
        }
        for turn in self.history:
            metadata = turn.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            for value in metadata.values():
                if not isinstance(value, str):
                    continue
                suffix = Path(value).suffix.lower()
                rel_value = _relative_path(root, Path(value))
                if suffix == ".wav":
                    metadata_paths["input_wav"].add(rel_value)
                elif suffix == ".mp3":
                    metadata_paths["output_mp3"].add(rel_value)
                elif suffix == ".txt":
                    metadata_paths["dryrun_text"].add(rel_value)

        return {key: sorted(values) for key, values in metadata_paths.items()}


def safe_episode_name(name: str) -> str:
    return _safe_episode_name(name)


def _infer_root_dir(sessions_dir: Path | None) -> Path:
    if sessions_dir is None:
        return PROJECT_ROOT
    path = Path(sessions_dir)
    return path.parent if path.name == "sessions" else path


def _relative_paths(root: Path, paths: Any) -> list[str]:
    return sorted(_relative_path(root, path) for path in paths)


def _relative_path(root: Path, path: Path) -> str:
    path = path if path.is_absolute() else (root / path)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)
