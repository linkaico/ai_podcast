from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from config.settings import Settings, load_settings
from pipeline.llm import call_llm, load_system_prompt
from pipeline.memory import ConversationMemory
from pipeline.preflight import format_preflight_report, run_preflight
from pipeline.reliability import structured_error
from pipeline.stt import capture_text_turn, list_input_devices, record_until_keypress, transcribe
from pipeline.tts import speak


EXIT_COMMANDS = {"q", "quit", "end"}


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
    active_settings.audio_input_dir.mkdir(parents=True, exist_ok=True)
    active_settings.audio_output_dir.mkdir(parents=True, exist_ok=True)

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

    system_prompt = load_system_prompt(episode_name=episode_name, settings=active_settings)
    turn_index = memory.next_turn_index()
    starting_turn_index = turn_index

    output_fn(f"Episode: {memory.episode_name}")
    output_fn(f"Session: {memory.session_file}")
    if active_settings.uses_text_input:
        output_fn("Text input mode is active. Type your host turn, or q/quit/end to finish.")
    else:
        output_fn("Microphone input mode is active. Press ENTER after each recording.")

    while True:
        if max_turns is not None and (turn_index - starting_turn_index) >= max_turns:
            output_fn("Max turns reached. Session saved.")
            break

        try:
            host_text, user_metadata = _capture_host_turn(
                active_settings,
                turn_index,
                input_fn,
                output_fn,
                memory,
            )
        except Exception as exc:
            memory.add_event("host_turn", "failed", turn_index, structured_error(exc, "host_turn"))
            output_fn(f"Host turn failed: {exc}")
            break

        if host_text is None:
            output_fn("Turn skipped.")
            memory.add_event("turn", "skipped", turn_index)
            continue
        if host_text.lower() in EXIT_COMMANDS:
            output_fn("Episode ended. Session saved.")
            memory.add_event("episode", "ended", turn_index)
            break
        if not host_text:
            output_fn("Empty turn skipped.")
            memory.add_event("turn", "skipped_empty", turn_index)
            continue

        output_fn(f"FLORIAN: {host_text}")
        memory.add("user", host_text, metadata={**(user_metadata or {}), "status": "transcript_confirmed"})

        try:
            output_fn("AI thinking...")
            ai_response = call_llm(memory.get(), system_prompt, active_settings)
            memory.add_event("llm_completed", "ok", turn_index)
            output_fn(f"AI: {ai_response}")
        except Exception as exc:
            memory.add_event("llm_completed", "failed", turn_index, structured_error(exc, "llm_completed"))
            output_fn(f"AI response failed: {exc}")
            break

        try:
            ai_audio_path = speak(ai_response, turn_index, active_settings, output_fn=output_fn)
            assistant_metadata = {"status": "tts_saved"}
            if ai_audio_path:
                assistant_metadata["audio_path"] = str(ai_audio_path)
            memory.add("assistant", ai_response, metadata=assistant_metadata)
            memory.add_event("tts_saved", "ok", turn_index, {"audio_path": str(ai_audio_path) if ai_audio_path else ""})
            if active_settings.playback_mode != "file-only":
                memory.add_event("playback_attempted", "ok", turn_index)
            if ai_audio_path and active_settings.uses_live_tts:
                output_fn(f"AI audio saved: {ai_audio_path}")
        except Exception as exc:
            memory.add_event("tts_saved", "failed", turn_index, structured_error(exc, "tts_saved"))
            output_fn(f"AI voice failed: {exc}")
            break

        memory.add_event("turn", "complete", turn_index)
        turn_index += 1

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

        run_episode(
            args.episode_name,
            settings=settings,
            resume=args.resume,
            session_path=args.session,
            max_turns=args.max_turns,
            confirm_transcript=confirm_override,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
