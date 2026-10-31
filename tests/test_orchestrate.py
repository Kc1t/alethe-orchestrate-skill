import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "alethe"
SCRIPT = SKILL / "scripts" / "orchestrate.py"
FAKE = Path(__file__).resolve().parent / "fake_cli.py"

spec = importlib.util.spec_from_file_location("orchestrate", SCRIPT)
orchestrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrate)


QUOTA_HOME = tempfile.TemporaryDirectory(prefix="quota-home-")
QUOTA_ENVIRONMENT = mock.patch.dict(os.environ, {
    "CODEX_HOME": str(Path(QUOTA_HOME.name) / "codex"),
    "ALETHE_ORCHESTRATE_POLICY": str(Path(QUOTA_HOME.name) / "policy.json"),
})


def setUpModule():
    QUOTA_ENVIRONMENT.start()


def tearDownModule():
    QUOTA_ENVIRONMENT.stop()
    QUOTA_HOME.cleanup()


def remove_tree(path):
    def retry(function, target, _info):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orchestrate-test-"))
        self.addCleanup(remove_tree, self.root)
        self.policy = self.root / "policy.json"
        self.home = self.root / "home"
        patcher = mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = orchestrate.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def make_repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        git(repo, "add", "README.md")
        git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init")
        return repo

    def worker(self, worker_id, mode, args, output_format, cwd, **extra):
        return {
            "id": worker_id,
            "provider": "custom",
            "mode": mode,
            "cwd": str(cwd),
            "command": sys.executable,
            "args": [str(FAKE), *args],
            "outputFormat": output_format,
            "task": f"Task for {worker_id}.",
            **extra,
        }

    def write_plan(self, name, plan):
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def run_two_waves(self):
        repo = self.make_repo()
        orchestration = self.root / "orchestration"
        shared = {"version": 1, "title": "Rename the login helper", "playbook": "plan-big-execute-small"}
        explore = self.write_plan(
            "explore",
            {
                **shared,
                "phase": "explore",
                "why": "Map the callers before changing anything.",
                "strategy": "research",
                "maxConcurrent": 1,
                "budget": {"maxCalls": 2},
                "workers": [
                    self.worker("map-callers", "read-only", ["claude"], "json", repo, why="find every caller"),
                    self.worker("broken", "read-only", ["fail"], "text", repo, why="expected to fail"),
                    self.worker("extra", "read-only", ["claude"], "json", repo, why="stopped by the budget"),
                ],
            },
        )
        build = self.write_plan(
            "build",
            {
                **shared,
                "phase": "build",
                "strategy": "balanced",
                "maxConcurrent": 1,
                "workers": [
                    self.worker(
                        "rename", "write", ["codex", "{result}", "README.md"], "jsonl", repo, why="mechanical rename"
                    ),
                    self.worker("add-file", "write", ["write", "hello.txt"], "text", repo, isolate=True),
                ],
            },
        )
        code, _, err = self.call("run", explore, "--run-dir", orchestration / "01-explore", "--policy", self.policy)
        self.assertEqual(code, 1, err)
        code, _, err = self.call("run", build, "--run-dir", orchestration / "02-build", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        return repo, orchestration


class RunnerTest(Fixtures):
    def test_plan_notes_reach_the_preview(self):
        plan = self.write_plan(
            "plan",
            {
                "version": 1,
                "title": "Rename   the\nhelper",
                "why": "Map callers first.",
                "playbook": "plan-big-execute-small",
                "phase": "explore",
                "workers": [self.worker("map", "read-only", ["claude"], "json", self.root, why="find every caller")],
            },
        )
        code, out, err = self.call("dry-run", plan, "--policy", self.policy)
        self.assertEqual(code, 0, err)
        preview = json.loads(out)
        self.assertEqual(preview["title"], "Rename the helper")
        self.assertEqual(preview["phase"], "explore")
        self.assertEqual(preview["workers"][0]["why"], "find every caller")
        self.assertEqual(preview["workers"][0]["command"], Path(sys.executable).stem)

    def test_long_or_unknown_notes_are_refused(self):
        worker = self.worker("a", "read-only", ["claude"], "json", self.root)
        too_long = self.write_plan("long", {"version": 1, "why": "x" * 501, "workers": [worker]})
        code, _, err = self.call("validate", too_long, "--policy", self.policy)
        self.assertEqual(code, 2)
        self.assertIn("plan.why must be at most 500 characters", err)
        unknown = self.write_plan("unknown", {"version": 1, "reason": "x", "workers": [worker]})
        code, _, err = self.call("validate", unknown, "--policy", self.policy)
        self.assertEqual(code, 2)
        self.assertIn("unknown plan fields: reason", err)

    def test_flags_that_approve_every_tool_need_permission(self):
        for flag in ("--trust-all-tools", "--allow-all-tools", "--yolo"):
            plan = self.write_plan("unsafe", {"version": 1, "workers": [self.worker("a", "write", [flag], "text", self.root)]})
            code, _, err = self.call("validate", plan, "--policy", self.policy)
            self.assertEqual(code, 2)
            self.assertIn("unsafe flag", err)
        allowed = self.worker("a", "write", ["--yolo"], "text", self.root, allowUnsafe=True)
        plan = self.write_plan("allowed", {"version": 1, "workers": [allowed]})
        self.assertEqual(self.call("validate", plan, "--policy", self.policy)[0], 0)

    def test_report_explains_what_each_wave_did_and_spent(self):
        _, orchestration = self.run_two_waves()
        code, out, err = self.call("report", orchestration, "--format", "json")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        totals = report["totals"]
        self.assertEqual(totals["runs"], 2)
        self.assertEqual(totals["statuses"], {"succeeded": 3, "failed": 1, "skipped": 1})
        self.assertEqual(totals["tokens"], 19100)
        self.assertEqual(totals["cachedTokens"], 58000)
        self.assertAlmostEqual(totals["costUsd"], 0.0421)
        self.assertEqual(totals["budgetStops"], ["budget maxCalls 2 exhausted"])
        self.assertEqual(totals["changes"], {"writers": 2, "files": 2, "insertions": 3, "deletions": 0})
        self.assertEqual(report["playbooks"], ["plan-big-execute-small"])

        explore = {worker["id"]: worker for worker in report["runs"][0]["workers"]}
        self.assertEqual(explore["map-callers"]["summary"], "Found 14 callers of login() across src/api and src/ui.")
        self.assertEqual(explore["map-callers"]["why"], "find every caller")
        self.assertEqual(explore["broken"]["stderrTail"], "boom: missing fixture")
        self.assertEqual(explore["extra"]["error"], "budget maxCalls 2 exhausted")

        follow = report["followUps"]
        self.assertEqual([item["worker"] for item in follow["worktrees"]], ["add-file"])
        self.assertEqual([item["worker"] for item in follow["mainCheckoutChanges"]], ["rename"])
        self.assertEqual(sorted(item["worker"] for item in follow["attention"]), ["broken", "extra"])

        saved = self.root / "report.md"
        code, markdown, _ = self.call("report", orchestration, "--write", saved)
        self.assertEqual(code, 0)
        self.assertIn("## 1. explore", markdown)
        self.assertIn("## 2. build", markdown)
        self.assertIn("- **Cost:** $0.04 reported by", markdown)
        self.assertIn("Failed: worker exited with status 1 (boom: missing fixture).", markdown)
        self.assertIn("- **Budget:** stopped 1 worker (budget maxCalls 2 exhausted)", markdown)
        self.assertEqual(saved.read_text(encoding="utf-8"), markdown)

    def test_worktrees_stay_out_of_git_status(self):
        repo, _ = self.run_two_waves()
        again = self.write_plan(
            "again", {"version": 1, "workers": [self.worker("second", "write", ["write", "b.txt"], "text", repo, isolate=True)]}
        )
        code, _, err = self.call("run", again, "--run-dir", self.root / "again", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        self.assertEqual(git(repo, "status", "--porcelain").splitlines(), [" M README.md"])
        exclude = Path(git(repo, "rev-parse", "--git-path", "info/exclude").strip())
        exclude = exclude if exclude.is_absolute() else repo / exclude
        self.assertEqual(exclude.read_text(encoding="utf-8").count("/.alethe/worktrees/"), 1)

    def test_report_reads_runs_saved_before_plan_json(self):
        _, orchestration = self.run_two_waves()
        (orchestration / "01-explore" / "plan.json").unlink()
        code, out, err = self.call("report", orchestration / "01-explore", "--format", "json")
        self.assertEqual(code, 0, err)
        run = json.loads(out)["runs"][0]
        self.assertIsNone(run["phase"])
        self.assertEqual(len(run["workers"]), 3)

    def test_report_defaults_to_the_latest_run(self):
        scratch = self.root / "tmp"
        scratch.mkdir()
        previous = tempfile.tempdir
        tempfile.tempdir = str(scratch)
        self.addCleanup(setattr, tempfile, "tempdir", previous)
        plan = self.write_plan("one", {"version": 1, "workers": [self.worker("only", "read-only", ["claude"], "json", self.root)]})
        code, _, err = self.call("run", plan, "--policy", self.policy)
        self.assertEqual(code, 0, err)
        code, out, err = self.call("report", "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["totals"]["workers"], 1)

    def test_report_prints_non_ascii_results_through_a_pipe(self):
        run_dir = self.root / "unicode"
        plan = self.write_plan("unicode", {"version": 1, "workers": [self.worker("only", "read-only", ["write", "out.txt"], "text", self.root)]})
        self.assertEqual(self.call("run", plan, "--run-dir", run_dir, "--policy", self.policy)[0], 0)
        (run_dir / "workers" / "only" / "result.md").write_text("Concluído: três arquivos → revisados.\n", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        result = subprocess.run([sys.executable, str(SCRIPT), "report", str(run_dir)], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertIn("três arquivos → revisados", result.stdout.decode("utf-8"))

    def test_version_comes_from_the_skill_metadata(self):
        version = re.search(r'version: "(\d+)"', (SKILL / "SKILL.md").read_text(encoding="utf-8")).group(1)
        self.assertEqual(orchestrate.skill_version(), version)
        result = subprocess.run([sys.executable, str(SCRIPT), "--version"], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f"alethe-orchestrate {version}")
        code, out, _ = self.call("doctor", "--policy", self.policy)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skill"]["version"], version)

    def test_plugin_manifests_and_readme_carry_the_skill_version(self):
        version = orchestrate.skill_version()
        expected = f"{version}.0.0"
        plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], expected)
        self.assertEqual([entry["version"] for entry in marketplace["plugins"]], [expected])
        self.assertTrue((ROOT / plugin["skills"] / "alethe" / "SKILL.md").is_file())
        for readme in ("README.md", "README.pt-BR.md"):
            self.assertIn(f"badge/skill-v{version}-", (ROOT / readme).read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "batch files exist only on Windows")
    def test_npm_style_shims_receive_the_whole_prompt(self):
        shim = self.root / "fake-agent.cmd"
        shim.write_text(f'@ECHO off\r\n"{sys.executable}"  "{FAKE}" %*\r\n', encoding="utf-8")
        recorded = self.root / "argv.json"
        task = 'First line.\nSecond "line" & echo INJECTED> injected.txt'
        worker = {
            **self.worker("shim", "read-only", [], "text", self.root, promptTransport="argument"),
            "command": str(shim),
            "args": ["argv", str(recorded)],
            "task": task,
        }
        plan = self.write_plan("shim", {"version": 1, "workers": [worker]})
        code, _, err = self.call("run", plan, "--run-dir", self.root / "run", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        prompt = json.loads(recorded.read_text(encoding="utf-8"))[-1]
        self.assertTrue(prompt.startswith(task + "\n\nRuntime contract:"), prompt)
        self.assertFalse((self.root / "injected.txt").exists())

    @unittest.skipUnless(os.name == "nt", "batch files exist only on Windows")
    def test_unknown_batch_files_refuse_arguments_cmd_would_rewrite(self):
        shim = self.root / "wrapper.cmd"
        shim.write_text('@ECHO off\r\ncall "%~dp0missing.exe" %*\r\n', encoding="utf-8")
        worker = {
            **self.worker("wrapped", "read-only", [], "text", self.root, promptTransport="argument"),
            "command": str(shim),
            "args": [],
        }
        plan = self.write_plan("wrapped", {"version": 1, "workers": [worker]})
        code, out, _ = self.call("run", plan, "--run-dir", self.root / "run", "--policy", self.policy)
        self.assertEqual(code, 1)
        outcome = json.loads(out)["workers"][0]
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("is a batch file", outcome["error"])

    def test_the_preview_renders_as_a_diagram(self):
        plan = self.write_plan("plan", {"version": 1, "workers": [self.worker("map", "read-only", ["claude"], "json", self.root)]})
        preview = self.root / "preview.json"
        code, _, err = self.call("dry-run", plan, "--policy", self.policy, "--write", preview)
        self.assertEqual(code, 0, err)
        svg = self.root / "plan.svg"
        result = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "diagram.py"), str(preview), str(svg)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<svg", svg.read_text(encoding="utf-8"))


class InvocationTest(unittest.TestCase):
    HELP = "--permission-prompts --disable-slash-commands --disallowedTools --strict-mcp-config --max-budget-usd --add-dir"
    FEATURES = "multi_agent                              stable             true\nmulti_agent_v2                           stable             false\n"

    def build(self, provider, mode="write", cost_cap=None, **extra):
        worker = {
            "id": "w",
            "provider": provider,
            "mode": mode,
            "model": None,
            "effort": None,
            "resume": None,
            "mcp": False,
            "task": "Do the unit.\n- second line",
            **extra,
        }
        outputs = lambda args, cache, lock: self.FEATURES if args[-2:] == ["features", "list"] else self.HELP
        root = Path(tempfile.mkdtemp(prefix="invocation-test-"))
        self.addCleanup(remove_tree, root)
        with mock.patch.object(orchestrate, "resolve_executable", lambda name: name), mock.patch.object(
            orchestrate, "launch_command", lambda executable: [executable]
        ), mock.patch.object(orchestrate, "command_output", outputs), mock.patch.object(
            orchestrate, "in_git_worktree", lambda cwd: True
        ):
            return orchestrate.build_invocation(worker, root, root, {}, None, 60, cost_cap)[0]

    def test_kimi_takes_the_prompt_attached_and_never_yolo(self):
        args = self.build("kimi")
        self.assertNotIn("--yolo", args)
        self.assertEqual(args[-1], "--prompt=Do the unit.\n- second line")

    def test_codex_resume_keeps_the_sandbox_and_effort(self):
        args = self.build("codex", effort="high", resume="thread-1")
        self.assertEqual(args[:5], ["codex", "--disable", "multi_agent", "--disable", "multi_agent_v2"])
        self.assertLess(args.index("--sandbox"), args.index("resume"))
        self.assertGreater(args.index("model_reasoning_effort=high"), args.index("resume"))
        self.assertIn("mcp_servers={}", args)
        self.assertEqual(args[-2:], ["thread-1", "-"])

    def test_claude_streams_and_cannot_start_agents(self):
        args = self.build("claude", cost_cap=0.5)
        self.assertIn("stream-json", args)
        self.assertIn("--verbose", args)
        self.assertEqual(args[args.index("--disallowedTools") + 1], "Agent,Workflow")
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(args[args.index("--max-budget-usd") + 1], "0.50")
        self.assertNotIn("--strict-mcp-config", self.build("claude", mcp=True))

    def test_json_output_survives_noise(self):
        self.assertEqual(len(orchestrate.json_values('{"a": 1}\nnot json\n{"b": 2}\n', "jsonl")), 2)
        self.assertEqual(orchestrate.json_values('warning: slow\n{"type": "result"}\n', "json"), [{"type": "result"}])

    def test_handoff_lines_are_read_from_markdown(self):
        handoff = orchestrate.parse_handoff("Did it.\n**STATUS:** done_with_concerns\n- SUMMARY: Parser fixed.\nVerdict: not proven")
        self.assertEqual(handoff, {"workerStatus": "DONE_WITH_CONCERNS", "workerSummary": "Parser fixed.", "verdict": "NOT_PROVEN"})


class GuardTest(Fixtures):
    def run_plan(self, name, workers, run_dir=None, **plan):
        path = self.write_plan(name, {"version": 1, "workers": workers, **plan})
        run_dir = run_dir or self.root / f"run-{name}"
        code, out, err = self.call("run", path, "--run-dir", run_dir, "--policy", self.policy)
        return code, (json.loads(out) if out.strip() else None), err, run_dir

    def test_read_only_workers_that_write_are_caught(self):
        repo = self.make_repo()
        workers = [
            self.worker("honest", "read-only", ["say", "Read it."], "text", repo),
            self.worker("sneaky", "read-only", ["write", "new.txt"], "text", repo),
        ]
        code, result, err, _ = self.run_plan("tripwire", workers, run_dir=repo / ".orchestration" / "01-read", maxConcurrent=1)
        self.assertEqual(code, 0, err)
        outcomes = {item["id"]: item for item in result["workers"]}
        self.assertFalse(outcomes["honest"]["readOnlyViolation"])
        self.assertTrue(outcomes["sneaky"]["readOnlyViolation"])
        self.assertEqual(outcomes["sneaky"]["touchedFiles"], ["new.txt"])
        report = json.loads(self.call("report", repo / ".orchestration", "--format", "json")[1])
        flagged = {item["worker"]: item for item in report["followUps"]["attention"]}
        self.assertIn("changed the checkout while read-only (new.txt)", flagged["sneaky"]["reasons"][0])

    def test_handoff_status_and_denied_tools_reach_the_report(self):
        workers = [
            self.worker("stuck", "read-only", ["say", "Looked.\nSTATUS: BLOCKED\nSUMMARY: Needs the API key name."], "text", self.root),
            self.worker("denied", "read-only", ["denied"], "jsonl", self.root),
        ]
        code, result, err, run_dir = self.run_plan("handoff", workers, maxConcurrent=1)
        self.assertEqual(code, 0, err)
        outcomes = {item["id"]: item for item in result["workers"]}
        self.assertEqual(outcomes["stuck"]["workerStatus"], "BLOCKED")
        self.assertEqual(outcomes["denied"]["permissionDenials"], ["Bash"])
        self.assertEqual(outcomes["denied"]["billedModels"], ["claude-haiku-4-5"])
        report = json.loads(self.call("report", run_dir, "--format", "json")[1])
        self.assertEqual(report["totals"]["workerStatuses"], {"BLOCKED": 1, "DONE": 1})
        stuck = report["runs"][0]["workers"][0]
        self.assertEqual(stuck["summary"], "Needs the API key name.")
        markdown = self.call("report", run_dir)[1]
        self.assertIn("| succeeded, blocked |", markdown)
        self.assertIn("1 tool call denied (Bash), so its checks may not have run", markdown)

    def test_failures_are_classified_with_a_hint(self):
        code, result, _, run_dir = self.run_plan("limits", [self.worker("limited", "read-only", ["ratelimit"], "text", self.root)])
        self.assertEqual(code, 1)
        outcome = result["workers"][0]
        self.assertEqual(outcome["failureKind"], "rate_limited")
        self.assertIn("resets at 5:00 PM", outcome["resetHint"])
        markdown = self.call("report", run_dir)[1]
        self.assertIn("The provider hit a usage limit; ask the user whether to wait for the reset", markdown)

    def test_a_quiet_worker_is_stopped_by_its_idle_timeout(self):
        worker = self.worker("quiet", "read-only", ["sleep", "30"], "text", self.root, idleTimeoutSeconds=5)
        code, result, _, _ = self.run_plan("quiet", [worker])
        self.assertEqual(code, 1)
        outcome = result["workers"][0]
        self.assertEqual((outcome["status"], outcome["failureKind"]), ("timed_out", "stalled"))
        self.assertEqual(outcome["error"], "worker printed nothing for 5s")
        self.assertLess(outcome["durationSeconds"], 25)

    def test_workers_get_a_marked_environment_and_cannot_orchestrate(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            code, result, err, run_dir = self.run_plan("env", [self.worker("probe", "read-only", ["env"], "text", self.root)])
        self.assertEqual(code, 0, err)
        seen = json.loads((run_dir / "workers" / "probe" / "result.md").read_text(encoding="utf-8"))
        self.assertEqual(seen, {"ALETHE_ORCHESTRATE_WORKER": "1", "CLAUDECODE": None})
        with mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_WORKER": "1"}):
            code, _, err, _ = self.run_plan("nested", [self.worker("inner", "read-only", ["say", "x"], "text", self.root)])
        self.assertEqual(code, 2)
        self.assertIn("workers cannot start orchestrations", err)

    def test_an_orchestration_budget_spans_every_wave(self):
        folder = self.root / "orchestration"
        code, out, err = self.call("budget", folder, "--max-calls", "2")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["remaining"], {"calls": 2})
        one = [self.worker("first", "read-only", ["say", "One."], "text", self.root)]
        self.assertEqual(self.run_plan("w1", one, run_dir=folder / "01-a")[0], 0)
        two = [self.worker(name, "read-only", ["say", "Two."], "text", self.root) for name in ("second", "third")]
        code, result, _, _ = self.run_plan("w2", two, run_dir=folder / "02-b", maxConcurrent=1)
        self.assertEqual(code, 1)
        statuses = sorted((item["id"], item["status"]) for item in result["workers"])
        self.assertEqual(statuses, [("second", "succeeded"), ("third", "skipped")])
        code, _, err, _ = self.run_plan("w3", one, run_dir=folder / "03-c")
        self.assertEqual(code, 2)
        self.assertIn("orchestration budget maxCalls 2 exhausted", err)
        markdown = self.call("report", folder)[1]
        self.assertIn("- **Orchestration budget:** 2 of 2 calls", markdown)

    def test_reported_tokens_cap_workers_that_report_no_cost(self):
        repo = self.make_repo()
        workers = [
            self.worker("big", "write", ["codex", "{result}", "README.md"], "jsonl", repo),
            self.worker("next", "read-only", ["say", "Later."], "text", repo),
        ]
        code, result, _, _ = self.run_plan("tokens", workers, maxConcurrent=1, budget={"maxReportedTokens": 1000})
        self.assertEqual(code, 1)
        skipped = [item for item in result["workers"] if item["status"] == "skipped"]
        self.assertEqual(skipped[0]["error"], "budget maxReportedTokens 1000 exhausted")

    def test_marks_show_what_the_spend_bought(self):
        _, orchestration = self.run_two_waves()
        self.assertEqual(self.call("mark", orchestration / "01-explore", "map-callers", "used", "--why", "found every caller")[0], 0)
        self.assertEqual(self.call("mark", orchestration / "02-build", "rename", "discarded")[0], 0)
        self.assertEqual(self.call("mark", orchestration / "02-build", "missing", "used")[0], 2)
        report = json.loads(self.call("report", orchestration, "--format", "json")[1])
        value = report["totals"]["value"]
        self.assertEqual((value["used"]["workers"], value["used"]["tokens"], value["used"]["costUsd"]), (["map-callers"], 5000, 0.0421))
        self.assertEqual((value["discarded"]["workers"], value["discarded"]["tokens"]), (["rename"], 14100))
        self.assertEqual(sorted(value["unmarked"]["workers"]), ["add-file", "broken"])
        markdown = self.call("report", orchestration)[1]
        self.assertIn("- **Value:** 1 result used ($0.04, 5,000 tokens); 1 result discarded (no cost reported, 14,100 tokens); 2 results not marked", markdown)
        status = self.call("status", orchestration)[1].splitlines()
        self.assertLessEqual(len(status), 8)
        self.assertEqual(status[1], "Waves: 1. explore partial (1/3); 2. build done (2/2)")
        self.assertTrue(any(line.startswith("Not marked: ") for line in status))

    def test_the_current_orchestration_needs_no_folder_and_records_plan_changes(self):
        code, out, err = self.call("new", "Revisão do login", "--playbook", "plan-big-execute-small", "--why", "Fix the login flow.")
        self.assertEqual(code, 0, err)
        folder = Path(json.loads(out)["folder"])
        self.assertTrue(folder.name.endswith("-revisao-do-login"))
        self.assertIn("no waves yet", self.call("report")[2])

        scout = self.write_plan("scout", {"version": 1, "phase": "scout", "workers": [self.worker("map", "read-only", ["say", "Mapped."], "text", self.root)]})
        fix = self.write_plan("fix", {"version": 1, "phase": "Fix_It", "workers": [self.worker("patch", "read-only", ["say", "Patched."], "text", self.root)]})
        self.assertEqual(self.call("run", scout, "--in", "current", "--policy", self.policy)[0], 0)
        self.assertEqual(self.call("note", "the review phase, the diff was three lines", "--kind", "skip")[0], 0)
        self.assertEqual(self.call("note", "to freeform: the bug was in the build script", "--kind", "switch")[0], 0)
        self.assertEqual(self.call("run", fix, "--in", "current", "--policy", self.policy)[0], 0)
        self.assertEqual(sorted(path.name for path in folder.iterdir() if path.is_dir()), ["01-scout", "02-fix-it"])
        self.assertEqual(self.call("run", fix, "--in", "current", "--run-dir", self.root / "x", "--policy", self.policy)[0], 2)

        self.assertEqual(self.call("mark", "map", "used")[0], 0)
        markdown = self.call("report")[1]
        self.assertIn("**Revisão do login**, playbook `plan-big-execute-small`, 2 waves", markdown)
        self.assertIn("Why: Fix the login flow.", markdown)
        self.assertIn("## Changes to the plan", markdown)
        self.assertRegex(markdown, r"- \d\d:\d\d Skipped: the review phase, the diff was three lines")
        self.assertIn("Switched: to freeform: the bug was in the build script", markdown)
        self.assertIn("Marked used", markdown)
        status = self.call("status")[1]
        self.assertIn("Plan changes: 2, last: switched to freeform", status)

        listing = self.call("list")[1].splitlines()
        self.assertTrue(listing[0].startswith("* "), listing)
        self.assertIn("Revisão do login | playbook plan-big-execute-small | 2 waves, done", listing[0])
        self.assertIn("2 plan changes", listing[0])

        other = self.root / "other"
        self.assertEqual(self.run_plan("other", [self.worker("solo", "read-only", ["say", "Solo."], "text", self.root)], run_dir=other / "01-a")[0], 0)
        self.assertEqual(json.loads(self.call("report", "--format", "json")[1])["totals"]["workers"], 1)
        self.assertEqual(self.call("use", folder)[0], 0)
        self.assertEqual(json.loads(self.call("report", "--format", "json")[1])["totals"]["workers"], 2)

    def hook(self, cwd):
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"cwd": str(cwd), "prompt": "next"}))):
            return self.call("hook")[1]

    def test_a_mode_stays_on_for_the_project_and_reminds_every_prompt(self):
        repo = self.make_repo()
        (repo / "src").mkdir()
        self.assertEqual(self.hook(repo), "")
        code, out, err = self.call("mode", "on", "--cwd", repo / "src", "--care", "careful", "--rule", "a partir de agora preciso ir com cuidado")
        self.assertEqual(code, 0, err)
        self.assertEqual(Path(json.loads(out)["scope"]), repo.resolve())
        self.call("mode", "on", "--cwd", repo, "--goal", "save")

        reminder = self.hook(repo / "src")
        self.assertIn("Alethe orchestration is on for this project", reminder)
        self.assertIn("Goal: save", reminder)
        self.assertIn("Careful: read before writing", reminder)
        self.assertIn("User rules: a partir de agora preciso ir com cuidado.", reminder)
        self.assertEqual(self.hook(self.root), "")
        with mock.patch.object(sys, "stdin", io.StringIO("not json")):
            self.assertEqual(self.call("hook")[:2], (0, ""))

        with mock.patch.object(orchestrate.time, "time", return_value=time.time() + orchestrate.MODE_IDLE_SECONDS + 1):
            self.assertEqual(self.hook(repo), "")
        self.assertEqual(self.call("mode", "on", "--cwd", repo, "--clear-rules")[0], 0)
        self.assertNotIn("User rules", self.hook(repo))
        self.assertEqual(self.call("mode", "off", "--goal", "max", "--cwd", repo)[0], 2)
        self.assertEqual(json.loads(self.call("mode", "off", "--cwd", repo)[1])["on"], False)
        self.assertEqual(self.hook(repo), "")

    def test_a_mode_on_everywhere_needs_no_call_and_can_be_turned_off_per_project(self):
        repo = self.make_repo()
        other = self.root / "elsewhere"
        other.mkdir()
        self.assertEqual(self.call("mode", "on", "--everywhere", "--goal", "save")[0], 0)
        self.assertIn("on in every project", self.hook(other))
        self.assertIn("Goal: save", self.hook(repo))
        with mock.patch.object(orchestrate.time, "time", return_value=time.time() + 10 * orchestrate.MODE_IDLE_SECONDS):
            self.assertIn("on in every project", self.hook(other))

        self.assertEqual(self.call("mode", "off", "--cwd", repo)[0], 0)
        self.assertEqual(self.hook(repo), "")
        self.assertIn("on in every project", self.hook(other))
        self.assertEqual(json.loads(self.call("mode", "show", "--cwd", other)[1])["source"], "everywhere")

        self.call("mode", "on", "--cwd", repo, "--care", "careful")
        reminder = self.hook(repo)
        self.assertIn("on for this project", reminder)
        self.assertIn("Careful:", reminder)

        self.assertEqual(self.call("mode", "off", "--everywhere")[0], 0)
        self.assertEqual(self.hook(other), "")
        self.assertIn("on for this project", self.hook(repo))

    def test_freestyle_reminds_the_lead_to_watch_cost_instead_of_orchestrating(self):
        repo = self.make_repo()
        self.assertEqual(self.call("mode", "on", "--cwd", repo, "--goal", "freestyle", "--rule", "economiza")[0], 0)
        reminder = self.hook(repo)
        self.assertIn("Goal: freestyle", reminder)
        self.assertIn("with no playbook, plan or waves", reminder)
        self.assertIn("say so in one line, at most once per reply", reminder)
        self.assertIn("ask before anything that costs more", reminder)
        self.assertIn("User rules: economiza.", reminder)
        self.assertNotIn("delegate bounded units when it pays", reminder)
        self.call("mode", "on", "--cwd", repo, "--goal", "save")
        self.assertNotIn("with no playbook, plan or waves", self.hook(repo))

    def test_the_plugin_hook_and_command_point_at_the_runner(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/alethe/scripts/orchestrate.py\" hook", command)
        self.assertEqual(sorted(p.name for p in (ROOT / "skills").iterdir()), ["alethe"])
        self.assertIn("## Short commands", (SKILL / "SKILL.md").read_text(encoding="utf-8"))

    def test_stop_ends_the_running_worker_and_skips_the_queued_one(self):
        workers = [
            self.worker("slow", "read-only", ["sleep", "60"], "text", self.root),
            self.worker("next", "read-only", ["say", "Later."], "text", self.root),
        ]
        plan = self.write_plan("slow", {"version": 1, "maxConcurrent": 1, "workers": workers})
        folder = self.root / "orchestration"
        run_dir = folder / "01-slow"
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "run", str(plan), "--run-dir", str(run_dir), "--policy", str(self.policy)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        def finish():
            if process.poll() is None:
                (run_dir / "stop").write_text("stop\n", encoding="utf-8")
                try:
                    process.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()

        self.addCleanup(finish)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = orchestrate.read_optional_json(run_dir / "state.json")
            if isinstance(state, dict) and state["workers"]["slow"]["status"] == "running":
                break
            time.sleep(0.2)
        code, out, err = self.call("stop", folder)
        self.assertEqual(code, 0, err)
        self.assertEqual([Path(path).resolve() for path in json.loads(out)["stopping"]], [run_dir.resolve()])
        _, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 1, stderr)
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["workers"]["slow"]["status"], "stopped")
        self.assertLess(state["workers"]["slow"]["durationSeconds"], 30)
        self.assertEqual((state["workers"]["next"]["status"], state["workers"]["next"]["failureKind"]), ("skipped", "stopped"))
        self.assertEqual(json.loads(self.call("stop", folder)[1])["stopping"], [])

    def test_previews_warn_before_money_is_spent(self):
        repo = self.make_repo()
        (repo / "README.md").write_text("dirty\n", encoding="utf-8")
        workers = [
            self.worker("a", "write", ["write", "a.txt"], "text", repo),
            self.worker("b", "write", ["write", "b.txt"], "text", repo, isolate=True),
        ]
        plan = self.write_plan("warn", {"version": 1, "strategy": "balanced", "budget": {"maxReportedCostUsd": 1}, "workers": workers})
        code, out, err = self.call("dry-run", plan, "--policy", self.policy)
        self.assertEqual(code, 0, err)
        warnings = json.loads(out)["warnings"]
        self.assertTrue(any("maxReportedCostUsd only sees cost that Claude reports" in text for text in warnings), warnings)
        self.assertTrue(any("2 writers with short briefs" in text for text in warnings), warnings)
        self.assertTrue(any(text.startswith("a writes in a checkout with uncommitted changes") for text in warnings), warnings)

    def test_auto_never_sends_read_only_work_to_kimi(self):
        policy = {**orchestrate.DEFAULT_POLICY, "providerOrder": ["kimi", "codex"]}
        with mock.patch.object(orchestrate, "available_builtin_providers", lambda: ["kimi", "codex"]):
            reader = orchestrate.choose_provider("auto", {"id": "r", "mode": "read-only"}, policy)
            writer = orchestrate.choose_provider("auto", {"id": "w", "mode": "write"}, policy)
        self.assertEqual((reader, writer), ("codex", "kimi"))

    def test_argument_prompts_respect_the_command_line_limit(self):
        worker = self.worker("long", "read-only", [], "text", self.root, promptTransport="argument")
        worker["task"] = "x" * (orchestrate.ARGUMENT_PROMPT_LIMIT + 1)
        code, _, err = self.call("validate", self.write_plan("long", {"version": 1, "workers": [worker]}), "--policy", self.policy)
        self.assertEqual(code, 2)
        self.assertIn("as an argument limited to", err)

    def test_a_repository_without_commits_keeps_the_worker_result(self):
        repo = self.root / "fresh"
        repo.mkdir()
        git(repo, "init", "-q")
        code, result, err, _ = self.run_plan("fresh", [self.worker("first", "write", ["write", "a.txt"], "text", repo)])
        self.assertEqual(code, 0, err)
        outcome = result["workers"][0]
        self.assertEqual(outcome["status"], "succeeded")
        self.assertIn("no commits yet", outcome["diffError"])

    def test_the_runner_never_imports_a_network_module(self):
        banned = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "imaplib", "poplib", "xmlrpc", "asyncio", "webbrowser"}
        for script in (SCRIPT, SKILL / "scripts" / "diagram.py"):
            tree = ast.parse(script.read_text(encoding="utf-8"))
            names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
            self.assertFalse(names & banned, f"{script.name} imports {sorted(names & banned)}")


if __name__ == "__main__":
    unittest.main()
