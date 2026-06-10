from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import suppress
from pathlib import Path
from typing import Callable

from config.settings import Settings, load_settings
from pipeline.llm import call_llm, load_system_prompt
from pipeline.memory import ConversationMemory
from pipeline.preflight import format_preflight_report, run_preflight
from pipeline.realtime import run_realtime_episode
from pipeline.reliability import structured_error
from pipeline.stt import capture_text_turn, list_input_devices, record_until_keypress, transcribe
from pipeline.tts import speak


EXIT_COMMANDS = {"q", "quit", "end"}


def _run_step(fn, stage, turn_id, memory, input_fn, output_fn):
    """Run one turn step; on error let the operator retry/skip/quit.

    Returns (status, result) where status is "ok" | "skip" | "quit".
    """
    while True:
        try:
            return "ok", fn()
        except Exception as exc:
            memory.add_event(stage, "failed", turn_id, structured_error(exc, stage))
            output_fn(f"{stage} failed: {exc}")
            choice = input_fn("[Enter=retry] s=skip / q=quit > ").strip().lower()
            if choice in ("", "r"):
                continue
            return ("quit" if choice == "q" else "skip"), None


def run_episode(
    episode_name: str,
    settings: Settings | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    max_turns: int | None = None,
    resume: bool = False,
    session_path: str | Path | None = None,
    confirm_transcript: bool | None = None,
) -> ConversationMemory:
    active_settings = settings or load_settings()
    if confirm_transcript is not None:
        active_settings = active_settings.with_overrides(confirm_transcript=confirm_transcript)
    active_settings.sessions_dir.mkdir(parents=True, exist_ok=True)

    if session_path:
        memory = ConversationMemory.from_session_file(session_path)
    elif resume:
        memory = ConversationMemory.latest_for_episode(episode_name, active_settings.sessions_dir)
    else:
        memory = ConversationMemory(
            episode_name=episode_name,
            sessions_dir=active_settings.sessions_dir,
            root_dir=active_settings.root_dir,
        )

    memory.audio_input_dir.mkdir(parents=True, exist_ok=True)
    memory.audio_output_dir.mkdir(parents=True, exist_ok=True)
    system_prompt = load_system_prompt(episode_name=memory.episode_name, settings=active_settings)
    completed_turns = 0

    output_fn(f"Episode: {memory.episode_name}")
    output_fn(f"Session: {memory.session_file}")
    if active_settings.uses_text_input:
        output_fn("Text input mode is active. Type your host turn, or q/quit/end to finish.")
    else:
        output_fn("Microphone input mode is active. Press ENTER after each recording.")

    try:
        while True:
            if max_turns is not None and completed_turns >= max_turns:
                output_fn("Max turns reached. Session saved.")
                break

            # Mic recordings need the turn id up front (filename); text turns reserve lazily (INF-13).
            turn_id = memory.reserve_turn_id() if active_settings.uses_microphone_input else None

            status, captured = _run_step(
                lambda: _capture_host_turn(
                    active_settings,
                    turn_id if turn_id is not None else 0,
                    input_fn,
                    output_fn,
                    memory,
                ),
                "host_turn",
                turn_id,
                memory,
                input_fn,
                output_fn,
            )
            if status == "quit":
                output_fn("Episode ended. Session saved.")
                memory.add_event("episode", "ended", turn_id)
                break
            if status == "skip":
                output_fn("Turn skipped.")
                memory.add_event("turn", "skipped", turn_id)
                continue
            host_text, user_metadata = captured

            if host_text is None:
                output_fn("Turn skipped.")
                memory.add_event("turn", "skipped", turn_id)
                continue
            if host_text.lower() in EXIT_COMMANDS:
                output_fn("Episode ended. Session saved.")
                memory.add_event("episode", "ended", turn_id)
                break
            if not host_text:
                output_fn("Empty turn skipped.")
                memory.add_event("turn", "skipped_empty", turn_id)
                continue

            if turn_id is None:  # text mode: commit an id now that the turn is real
                turn_id = memory.reserve_turn_id()

            output_fn(f"FLORIAN: {host_text}")
            memory.add("user", host_text, metadata={**(user_metadata or {}), "turn_id": turn_id})

            output_fn("AI thinking...")
            status, ai_response = _run_step(
                lambda: call_llm(memory.get(), system_prompt, active_settings),
                "llm_completed",
                turn_id,
                memory,
                input_fn,
                output_fn,
            )
            if status == "quit":
                output_fn("Episode ended. Session saved.")
                memory.add_event("episode", "ended", turn_id)
                break
            if status == "skip":
                output_fn("Turn skipped.")
                continue
            memory.add_event("llm_completed", "ok", turn_id)
            output_fn(f"AI: {ai_response}")
            memory.add("assistant", ai_response, metadata={"status": "tts_pending", "turn_id": turn_id})

            status, ai_audio_path = _run_step(
                lambda: speak(
                    ai_response,
                    turn_id,
                    active_settings,
                    output_fn=output_fn,
                    output_dir=memory.audio_output_dir,
                ),
                "tts_saved",
                turn_id,
                memory,
                input_fn,
                output_fn,
            )
            if status in ("quit", "skip"):
                memory.update_turn_metadata("assistant", turn_id, status="tts_failed")
                if status == "quit":
                    output_fn("Episode ended. Session saved.")
                    memory.add_event("episode", "ended", turn_id)
                    break
                continue

            assistant_metadata = {"status": "tts_saved"}
            if ai_audio_path:
                assistant_metadata["audio_path"] = str(ai_audio_path)
            memory.update_turn_metadata("assistant", turn_id, **assistant_metadata)
            memory.add_event("tts_saved", "ok", turn_id, {"audio_path": str(ai_audio_path) if ai_audio_path else ""})
            if ai_audio_path and active_settings.uses_live_tts:
                output_fn(f"AI audio saved: {ai_audio_path}")

            memory.add_event("turn", "complete", turn_id)
            memory.flush()
            completed_turns += 1
    finally:
        memory.close()

    return memory


def _capture_host_turn(
    settings: Settings,
    turn_index: int,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    memory: ConversationMemory,
) -> tuple[str | None, dict[str, str] | None]:
    if settings.uses_text_input:
        return capture_text_turn(input_fn=input_fn), None

    while True:
        audio_path = record_until_keypress(
            settings,
            turn_index=turn_index,
            input_fn=input_fn,
            output_fn=output_fn,
            output_dir=memory.audio_input_dir,
        )
        output_fn(f"Host audio saved: {audio_path}")
        memory.add_event("recording_saved", "ok", turn_index, {"audio_path": audio_path})

        transcript = transcribe(audio_path, settings)
        output_fn(f"Transcript: {transcript}")
        memory.add_event("transcribed", "ok", turn_index, {"audio_path": audio_path})

        if not settings.confirm_transcript:
            return transcript, {"audio_path": audio_path, "status": "transcribed"}

        decision = input_fn("Accept transcript? [Enter=accept, r=re-record, e=edit, s=skip, q=quit] ").strip().lower()
        if decision == "":
            memory.add_event("transcript_confirmed", "ok", turn_index)
            return transcript, {"audio_path": audio_path, "status": "transcript_confirmed"}
        if decision == "r":
            memory.add_event("transcript_confirmed", "re_record", turn_index)
            continue
        if decision == "e":
            edited = input_fn("Edited transcript> ").strip()
            if edited:
                memory.add_event("transcript_confirmed", "edited", turn_index)
                return edited, {"audio_path": audio_path, "status": "transcript_edited"}
            output_fn("Empty edit skipped; re-recording.")
            continue
        if decision == "s":
            return None, {"audio_path": audio_path, "status": "skipped"}
        if decision == "q":
            return "q", {"audio_path": audio_path, "status": "quit"}
        output_fn("Unknown choice; press Enter to accept, r, e, s, or q.")


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":  # so non-ASCII device names in --list-devices aren't mojibake
        with suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run an AI podcast episode.")
    parser.add_argument("episode_name", nargs="?", default="default")
    parser.add_argument("--resume", action="store_true", help="Resume the latest session for this episode.")
    parser.add_argument("--session", help="Resume a specific session JSON file.")
    parser.add_argument("--doctor", action="store_true", help="Run preflight checks and exit.")
    parser.add_argument("--list-devices", action="store_true", help="List audio input devices and exit.")
    parser.add_argument("--confirm-transcript", action="store_true", help="Confirm/edit mic transcripts before LLM calls.")
    parser.add_argument("--no-confirm-transcript", action="store_true", help="Disable mic transcript confirmation.")
    parser.add_argument("--max-turns", type=int, help="Stop after this many completed new turns.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.list_devices:
            for device in list_input_devices():
                print(
                    f"{device['index']}: {device['name']} "
                    f"(inputs={device['max_input_channels']}, rate={device['default_samplerate']})"
                )
            return 0

        settings = load_settings(validate=not args.doctor)
        if args.doctor:
            result = run_preflight(settings)
            print(format_preflight_report(result))
            return 0 if result["ok"] else 1

        confirm_override = None
        if args.confirm_transcript:
            confirm_override = True
        if args.no_confirm_transcript:
            confirm_override = False

        if settings.uses_realtime:
            # --max-turns and --confirm-transcript do not apply to realtime: turns are not
            # discrete and transcripts are produced live by the model. Reject the turn cap
            # explicitly rather than silently ignoring it; stop a realtime episode with ENTER.
            if args.max_turns is not None:
                print(
                    "Error: --max-turns is not supported in realtime mode (press ENTER to stop).",
                    file=sys.stderr,
                )
                return 1
            asyncio.run(
                run_realtime_episode(
                    args.episode_name,
                    settings,
                    resume=args.resume,
                    session_path=args.session,
                )
            )
        else:
            run_episode(
                args.episode_name,
                settings=settings,
                resume=args.resume,
                session_path=args.session,
                max_turns=args.max_turns,
                confirm_transcript=confirm_override,
            )
    except KeyboardInterrupt:
        print("\nInterrupted — session saved.", file=sys.stderr)
        return 130
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
