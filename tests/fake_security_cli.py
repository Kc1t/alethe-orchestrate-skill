"""Local structured-output fixture; never invokes an agent CLI."""
import json
import sys

provider = sys.argv[1]
args = sys.argv[2:]
if "--help" in args:
    print("--disable --disallowedTools --strict-mcp-config --max-budget-usd "
          "--permission-prompts --setting-sources --disable-slash-commands --add-dir")
elif args == ["features", "list"]:
    print("multi_agent stable true\nmulti_agent_v2 stable false")
else:
    task = sys.stdin.read() if provider == "claude" else " ".join(args)
    denied = "emit-denied" in task
    error = "emit-error" in task
    print(json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success",
                      "is_error": error, "result": "" if denied else "Finished fixture.",
                      "denied_actions": [{"action": "run_command"}] if denied else []}))
    if provider == "claude":
        print(json.dumps({"type": "system", "subtype": "task_summary"}))
