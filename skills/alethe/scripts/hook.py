#!/usr/bin/env python3
"""Cheap Claude Code hook gate; import the runner only when a check may print."""

from __future__ import annotations

import json
import os
import re
import sys
import time


def session_state_filename(session: str) -> str:
    import hashlib

    # Kept in sync with orchestrate.session_state_filename by a parity test.
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", session)[:80] or "session"
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:12]
    return f"{safe}-{digest}.json"


def orchestrations_home() -> str:
    configured = os.environ.get("ALETHE_ORCHESTRATE_HOME")
    if configured:
        return os.path.expanduser(configured)
    policy = os.environ.get("ALETHE_ORCHESTRATE_POLICY")
    if policy:
        return os.path.join(os.path.dirname(os.path.expanduser(policy)), "orchestrations")
    if os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~/AppData/Roaming")), "Alethe")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "alethe")
    return os.path.join(base, "orchestrations")


def read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def covers(scope: str, cwd: str) -> bool:
    root = os.path.realpath(scope)
    try:
        return os.path.commonpath([cwd, root]) == root
    except ValueError:
        return False


def mode_is_off(state: dict, cwd: str, now: float, post_tool: bool) -> bool:
    modes = state.get("modes") or {}
    matches = [scope for scope, mode in modes.items() if isinstance(mode, dict)
               and now - float(mode.get("seenAt") or 0) < 12 * 3600 and covers(scope, cwd)]
    if matches:
        mode = modes[max(matches, key=len)]
        return post_tool and mode.get("goal") != "passive"
    if any(covers(scope, cwd) for scope in state.get("offIn") or []):
        return True
    mode = state.get("everywhere")
    if isinstance(mode, dict) and mode:
        return post_tool and mode.get("goal") != "passive"
    return state.get("passive") is False or os.environ.get("ALETHE_PASSIVE") == "0"


def skip_hook(event: dict, now: float) -> bool:
    cwd = os.path.realpath(os.path.expanduser(event.get("cwd") or os.getcwd()))
    post_tool = event.get("hook_event_name", "UserPromptSubmit") == "PostToolUse"
    home = orchestrations_home()
    if mode_is_off(read_state(os.path.join(home, "modes.json")), cwd, now, post_tool):
        return True
    if not post_tool:
        return False
    transcript = event.get("transcript_path")
    session = str(event.get("session_id") or (os.path.splitext(os.path.basename(transcript))[0] if transcript else "cwd:" + cwd))
    check = read_state(os.path.join(home, "passive", session_state_filename(session))).get("check", {})
    minutes, printed = check.get("checkEveryMinutes"), check.get("lastPrinted")
    if type(minutes) not in (int, float) or type(printed) not in (int, float):
        return False  # Old or absent state needs the full path to initialize the interval.
    return 0 < minutes < float("inf") and abs(printed) < float("inf") and now - printed < minutes * 60


def full_hook_output(stdin_text: str) -> str:
    import orchestrate

    return orchestrate.hook_output(stdin_text)


def hook_output(stdin_text: str) -> str:
    if os.environ.get("ALETHE_ORCHESTRATE_WORKER") == "1":
        return ""
    try:
        event = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(event, dict) or skip_hook(event, time.time()):
            return ""
        return full_hook_output(stdin_text)
    except Exception:  # A hook must never block or fail the prompt.
        return ""


def main() -> int:
    if os.environ.get("ALETHE_ORCHESTRATE_WORKER") == "1":
        return 0
    try:
        stream = getattr(sys.stdin, "buffer", None)
        text = stream.read().decode("utf-8", errors="replace") if stream is not None else sys.stdin.read()
        print(hook_output(text), end="")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
