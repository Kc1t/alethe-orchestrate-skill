"""Fake Claude / Agy CLI for the resume tests.

argv[1] is the mode: "claude" or "agy". Environment:
  FAKE_COST          total_cost_usd to report (Claude)
  FAKE_TOKENS        total input tokens to report (Agy)
  FAKE_KEEP_SESSION  "1": a resume keeps the requested session id; otherwise it starts a new one (the Agy behavior)
"""
import json
import os
import sys


def value_after(flag):
    for index, arg in enumerate(sys.argv):
        if arg == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def main() -> int:
    if "--help" in sys.argv:
        print("--disable --disallowedTools --strict-mcp-config --max-budget-usd "
              "--permission-prompts --setting-sources --disable-slash-commands --add-dir --tools")
        return 0
    mode = sys.argv[1]
    if mode == "claude":
        sys.stdin.read()
        resume = value_after("--resume")
        session = resume or "claude-sess-1"
        print(json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
            "result": "Done.\nSTATUS: DONE\nSUMMARY: ok",
            "session_id": session,
            "total_cost_usd": float(os.environ.get("FAKE_COST", "0.01")),
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }))
        return 0
    resume = value_after("--conversation")
    keep = os.environ.get("FAKE_KEEP_SESSION") == "1"
    session = resume if (resume and keep) else ("agy-new-session" if resume else "agy-sess-1")
    print(json.dumps({
        "conversation_id": session, "status": "SUCCESS",
        "response": "All checked.\nSTATUS: DONE\nSUMMARY: ok",
        "usage": {"input_tokens": int(os.environ.get("FAKE_TOKENS", "1000")), "output_tokens": 10},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
