from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from config.settings import PROJECT_ROOT


Turn = dict[str, Any]


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
    session_id: str | None = None
    next_turn_id_value: int = 0
    now_fn: Callable[[], datetime] = _utc_now
    history: list[Turn] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    artifact_manifest: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.episode_name = _safe_episode_name(self.episode_name)
        self.sessions_dir = self.sessions_dir or PROJECT_ROOT / "sessions"
        self.root_dir = self.root_dir or _infer_root_dir(self.sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        if self.session_id is None:
            timestamp = self.now_fn().strftime("%Y%m%d_%H%M%S_%f")
            self.session_id = f"{self.episode_name}_{timestamp}_{uuid4().hex[:8]}"
        if self.session_file is None:
            self.session_file = self.sessions_dir / f"{self.session_id}.json"
        else:
            self.session_file = Path(self.session_file)
        self.artifact_manifest = _normalize_artifact_manifest(self.artifact_manifest)

    @property
    def audio_input_dir(self) -> Path:
        return (self.root_dir or PROJECT_ROOT) / "audio" / str(self.session_id) / "input"

    @property
    def audio_output_dir(self) -> Path:
        return (self.root_dir or PROJECT_ROOT) / "audio" / str(self.session_id) / "output"

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
            session_id=payload.get("session_id") or session_path.stem,
            next_turn_id_value=_next_turn_id_from_payload(payload),
            now_fn=now_fn,
            history=history,
            events=events,
            artifact_manifest=payload.get("artifacts", {}),
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
            self._register_paths(metadata)
        self.history.append(turn)
        self._trim()
        self._save()

    def get(self) -> list[Turn]:
        return list(self.history)

    def messages(self) -> list[dict[str, str]]:
        return [{"role": turn["role"], "content": turn["content"]} for turn in self.history]

    def next_turn_index(self) -> int:
        return self.next_turn_id_value

    def reserve_turn_id(self) -> int:
        turn_id = self.next_turn_id_value
        self.next_turn_id_value += 1
        self._save()
        return turn_id

    def update_turn_metadata(self, role: str, turn_id: int, **metadata: Any) -> None:
        for turn in reversed(self.history):
            existing = turn.get("metadata") or {}
            if turn.get("role") == role and existing.get("turn_id") == turn_id:
                existing.update(metadata)
                turn["metadata"] = existing
                self._register_paths(existing)
                self._save()
                return
        raise ValueError(f"No {role} turn found for turn_id={turn_id}.")

    def order_realtime_transcripts(self) -> None:
        positions = [
            index
            for index, turn in enumerate(self.history)
            if isinstance((turn.get("metadata") or {}).get("item_sequence"), int)
        ]
        ordered = sorted(
            (self.history[index] for index in positions),
            key=lambda turn: turn["metadata"]["item_sequence"],
        )
        for index, turn in zip(positions, ordered):
            self.history[index] = turn
        if positions:
            self._save()

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
            self._register_paths(details)
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
            "session_id": self.session_id,
            "next_turn_id": self.next_turn_id_value,
            "saved_at": self.now_fn().isoformat(),
            "history": self.history,
            "events": self.events,
            "artifacts": self.artifacts(),
        }
        temporary_path = self.session_file.with_name(f".{self.session_file.name}.{uuid4().hex}.tmp")
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary_path, self.session_file)

    def artifacts(self) -> dict[str, list[str]]:
        return {key: sorted(set(values)) for key, values in self.artifact_manifest.items()}

    def register_artifact(self, path: str | Path, kind: str | None = None) -> None:
        root = self.root_dir or _infer_root_dir(self.sessions_dir)
        relative_path = _relative_path(root, Path(path))
        artifact_kind = kind or _artifact_kind(relative_path)
        if not artifact_kind:
            return
        paths = self.artifact_manifest.setdefault(artifact_kind, [])
        if relative_path not in paths:
            paths.append(relative_path)

    def _register_paths(self, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if not isinstance(value, str) or "path" not in key:
                continue
            self.register_artifact(value)


def safe_episode_name(name: str) -> str:
    return _safe_episode_name(name)


def _infer_root_dir(sessions_dir: Path | None) -> Path:
    if sessions_dir is None:
        return PROJECT_ROOT
    path = Path(sessions_dir)
    return path.parent if path.name == "sessions" else path


def _relative_path(root: Path, path: Path) -> str:
    path = path if path.is_absolute() else (root / path)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _artifact_kind(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    suffix = Path(normalized).suffix.lower()
    if suffix == ".wav":
        return "input_wav" if "/input/" in f"/{normalized}" else "output_wav"
    if suffix == ".mp3":
        return "output_mp3"
    if suffix == ".txt":
        return "dryrun_text"
    return None


def _normalize_artifact_manifest(payload: Any) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): [str(value) for value in values if isinstance(value, str)]
        for key, values in payload.items()
        if isinstance(values, list)
    }


def _next_turn_id_from_payload(payload: dict[str, Any]) -> int:
    stored = payload.get("next_turn_id")
    if isinstance(stored, int) and stored >= 0:
        return stored
    indices = [
        event.get("turn_index")
        for event in payload.get("events", [])
        if isinstance(event, dict) and isinstance(event.get("turn_index"), int)
    ]
    if indices:
        return max(indices) + 1
    return sum(1 for turn in payload.get("history", []) if turn.get("role") == "assistant")
