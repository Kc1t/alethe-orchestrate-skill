import datetime as dt
import json
import os
import sys
from pathlib import Path


def main():
    mode = sys.argv[1]
    result = sys.argv[2] if len(sys.argv) > 2 else ""
    now = dt.datetime.now(dt.timezone.utc)
    reset_iso = (now + dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    if mode == "agy-usage":
        print(json.dumps({
            "status": "SUCCESS",
            "command": {
                "data": {
                    "groups": [
                        {
                            "buckets": [
                                {"id": "gemini-5h", "remaining_fraction": 0.14, "reset_time": reset_iso},
                                {"id": "gemini-weekly", "remaining_fraction": 0.80, "reset_time": reset_iso},
                                {"id": "gemini-argon-wtu-weekly", "remaining_fraction": 0.03, "reset_time": reset_iso},
                                {"id": "3p-5h", "remaining_fraction": 0.52, "reset_time": reset_iso},
                                {"id": "3p-weekly", "remaining_fraction": 0.75, "reset_time": reset_iso},
                            ]
                        }
                    ]
                }
            },
        }))
        return 0
    sys.stdin.read()
    reset = int((now + dt.timedelta(hours=2)).timestamp())
    if mode.startswith("claude"):
        for used in (0.1, 0.86):
            print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "resetsAt": reset, "rateLimitType": "five_hour",
                "unifiedWindows": {"five_hour": {"utilization": used, "resetsAt": reset},
                    "seven_day": {"utilization": 0.08, "resetsAt": reset + 86400}}}}))
        print(json.dumps({"type": "result", "subtype": "success", "result": "STATUS: DONE\nSUMMARY: Fixture completed.",
            "session_id": "fixture-claude", "total_cost_usd": 0.02,
            "usage": {"input_tokens": 20, "cache_creation_input_tokens": 40,
                      "cache_read_input_tokens": 800, "output_tokens": 10},
            "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.02}}}))
        return 1 if mode == "claude-fail" else 0
    thread = "fixture-codex-thread"
    folder = Path(os.environ["CODEX_HOME"]) / "sessions" / now.strftime("%Y/%m/%d")
    folder.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": now.isoformat(), "type": "event_msg", "payload": {"type": "token_count",
        "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 97.0 if mode == "codex-high" else 33.0, "window_minutes": 300,
            "resets_at": reset}, "secondary": {"used_percent": 29.0, "window_minutes": 10080,
            "resets_at": reset + 86400}, "credits": {}}}}
    (folder / ("rollout-" + now.strftime("%Y-%m-%dT%H-%M-%S") + "-" + thread + ".jsonl")).write_text(
        json.dumps(event) + "\n", encoding="utf-8")
    Path(result).write_text("STATUS: DONE\nSUMMARY: Fixture completed.\n", encoding="utf-8")
    print(json.dumps({"type": "thread.started", "thread_id": thread}))
    for total, cached, output in ((100, 80, 10), (200, 100, 20)):
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": total,
            "cached_input_tokens": cached, "output_tokens": output}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
