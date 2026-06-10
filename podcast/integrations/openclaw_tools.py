from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.settings import PROJECT_ROOT, load_settings
from main import run_episode as _run_episode
from pipeline.memory import ConversationMemory, safe_episode_name
from pipeline.realtime import run_realtime_episode as _run_realtime_episode


def run_episode(
    name: str,
    resume: bool = False,
    session_path: str | Path | None = None,
    max_turns: int | None = None,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run an episode and return machine-readable session metadata.

    Pass `input_fn`/`output_fn` to drive the chained loop non-interactively (e.g. from an
    agent); omit them to use the console. Realtime mode is microphone-driven and cannot be
    fully driven via `input_fn`.
    """
    settings = load_settings()
    driver: dict[str, Any] = {}
    if input_fn is not None:
        driver["input_fn"] = input_fn
    if output_fn is not None:
        driver["output_fn"] = output_fn
    if settings.uses_realtime:
        memory = asyncio.run(
            _run_realtime_episode(name, settings, resume=resume, session_path=session_path, **driver)
        )
    else:
        memory = _run_episode(
            name,
            settings=settings,
            resume=resume,
            session_path=session_path,
            max_turns=max_turns,
            **driver,
        )
    return _session_metadata(memory.session_file)


def write_episode_context(
    episode_name: str,
    content: str,
    sources: list[dict[str, str]] | None = None,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write agent-prepared episode context into the prompts directory."""
    if not content or not content.strip():
        raise ValueError("Episode context content must not be empty.")

    root = _resolve_root(root_dir)
    safe_name = safe_episode_name(episode_name)
    prompt_dir = root / "config" / "prompts" / "episodes"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / f"{safe_name}.txt"

    source_lines = _format_sources(sources or [])
    rendered = "\n".join(
        [
            f"# Episode Context: {safe_name}",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "Sources:",
            *source_lines,
            "",
            content.strip(),
            "",
        ]
    )
    path.write_text(rendered, encoding="utf-8")
    return {
        "episode": safe_name,
        "path": str(path),
        "sources_count": len(sources or []),
    }


def list_sessions(
    episode_name: str | None = None,
    root_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """List saved sessions, optionally filtered by episode."""
    root = _resolve_root(root_dir)
    sessions_dir = root / "sessions"
    safe_name = safe_episode_name(episode_name) if episode_name else None
    pattern = f"{safe_name}_*.json" if safe_name else "*.json"
    sessions = [_session_metadata(path) for path in sorted(sessions_dir.glob(pattern))]
    return [session for session in sessions if session]


def load_session(session_path: str | Path, root_dir: str | Path | None = None) -> dict[str, Any]:
    """Load a session JSON file."""
    path = _resolve_session_path(session_path, root_dir)
    if not path.exists():
        raise FileNotFoundError(f"Session file does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def latest_session(
    episode_name: str,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return metadata for the newest session for an episode."""
    root = _resolve_root(root_dir)
    memory = ConversationMemory.latest_for_episode(episode_name, root / "sessions")
    return _session_metadata(memory.session_file)


def episode_artifacts(
    episode_name: str,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return discovered audio/text artifacts for the latest episode session."""
    root = _resolve_root(root_dir)
    memory = ConversationMemory.latest_for_episode(episode_name, root / "sessions")
    return {
        "episode": memory.episode_name,
        "session_path": str(memory.session_file),
        "artifacts": memory.artifacts(),
    }


def export_transcript(
    session_path: str | Path,
    format: str = "markdown",
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Export a session transcript for post-production review."""
    if format != "markdown":
        raise ValueError("Only markdown transcript export is currently supported.")

    path = _resolve_session_path(session_path, root_dir)  # resolves + containment-checks once
    if not path.exists():
        raise FileNotFoundError(f"Session file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = _resolve_root(root_dir)
    episode = safe_episode_name(payload.get("episode", "default"))
    exports_dir = root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    output_path = exports_dir / f"{path.stem}.md"

    lines = [
        f"# Transcript: {episode}",
        "",
        f"Session: `{path}`",
        f"Saved: {payload.get('saved_at', 'unknown')}",
        "",
        "## Conversation",
        "",
    ]
    for turn in payload.get("history", []):
        speaker = "Florian" if turn.get("role") == "user" else "AI"
        lines.extend([f"**{speaker}:** {turn.get('content', '')}", ""])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "episode": episode,
        "format": format,
        "path": str(output_path),
        "turns": len(payload.get("history", [])),
    }


def _resolve_root(root_dir: str | Path | None) -> Path:
    if root_dir is not None:
        return Path(root_dir).resolve()
    return PROJECT_ROOT


def _resolve_session_path(session_path: str | Path, root_dir: str | Path | None = None) -> Path:
    root = _resolve_root(root_dir)
    sessions_dir = (root / "sessions").resolve()
    candidate = Path(session_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(sessions_dir):
        raise ValueError(f"Session path escapes the sessions directory: {session_path}")
    return resolved


def _session_metadata(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "episode": payload.get("episode"),
        "path": str(path),
        "saved_at": payload.get("saved_at"),
        "turns": len(payload.get("history", [])),
        "artifacts": payload.get("artifacts", {}),
    }


def _format_sources(sources: list[dict[str, str]]) -> list[str]:
    if not sources:
        return ["- none"]
    lines = []
    for source in sources:
        title = source.get("title") or "Untitled source"
        url = source.get("url")
        lines.append(f"- {title}: {url}" if url else f"- {title}")
    return lines
