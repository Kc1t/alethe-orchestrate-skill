"""Resume safety: a lost session must fail clearly, and the budget counts only what a resumed worker adds."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "alethe" / "scripts" / "orchestrate.py"
FAKE = Path(__file__).with_name("fake_resume_cli.py")
spec = importlib.util.spec_from_file_location("resume_guard_orchestrate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LostResumeUnitTest(unittest.TestCase):
    def worker(self, provider, resume):
        return {"provider": provider, "resume": resume}

    def test_a_different_session_is_lost_for_claude_and_agy(self):
        for provider in ("agy", "claude"):
            message = runner.lost_resume(self.worker(provider, "old"), "new")
            self.assertIn("old", message)
            self.assertIn("new", message)

    def test_same_session_no_resume_no_session_or_unmeasured_provider_is_not_lost(self):
        self.assertIsNone(runner.lost_resume(self.worker("agy", "same"), "same"))
        self.assertIsNone(runner.lost_resume(self.worker("agy", None), "any"))
        self.assertIsNone(runner.lost_resume(self.worker("agy", "old"), None))
        self.assertIsNone(runner.lost_resume(self.worker("codex", "old"), "new"))


class ResumeRunTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="resume-guard-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "work"
        self.work.mkdir()
        env = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"),
            "ALETHE_ORCHESTRATE_POLICY": str(self.root / "policy.json"),
            "ALETHE_ORCHESTRATE_HOME": str(self.root / "orchestrations"),
        })
        env.start()
        self.addCleanup(env.stop)
        for name, value in (("resolve_executable", lambda name: name),
                            ("launch_command", lambda executable: [sys.executable, str(FAKE), executable])):
            patcher = mock.patch.object(runner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        out = self.call("new", "resume guard", "--why", "test", "--lead", "claude:opus")
        self.folder = Path(json.loads(out)["folder"])
        self.count = 0

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runner.main(list(args))
        return out.getvalue()

    def run_worker(self, provider, resume=None, budget=None):
        self.count += 1
        worker_id = f"w{self.count}"
        worker = {"id": worker_id, "provider": provider, "mode": "read-only", "cwd": str(self.work),
                  "why": "test", "task": "Say done.", "timeoutSeconds": 60}
        if resume:
            worker["resume"] = resume
        plan = {"version": 1, "title": "t", "why": "w", "strategy": "economy", "maxConcurrent": 1, "workers": [worker]}
        if budget:
            plan["budget"] = budget
        path = self.root / f"plan-{self.count}.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        self.call("run", str(path), "--in", str(self.folder))
        outcome = json.loads(next(self.folder.glob(f"*/workers/{worker_id}/outcome.json")).read_text(encoding="utf-8"))
        state = json.loads(next(self.folder.glob(f"*/workers/{worker_id}")).parent.parent.joinpath("state.json").read_text(encoding="utf-8"))
        return outcome, state

    def test_agy_with_unknown_session_fails_and_is_never_done(self):
        first, _ = self.run_worker("agy")
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first["workerStatus"], "DONE")
        lost, _ = self.run_worker("agy", resume="ghost-session")
        self.assertEqual(lost["status"], "failed")
        self.assertEqual(lost["failureKind"], "session_not_found")
        self.assertIsNone(lost["workerStatus"])
        self.assertIn("ghost-session", lost["error"])
        self.assertIn("agy-new-session", lost["error"])
        report = runner.build_report(sorted(p.parent for p in self.folder.glob("*/state.json")))
        self.assertEqual(report["totals"]["statuses"].get("failed"), 1)

    def test_agy_that_keeps_the_session_still_succeeds(self):
        first, _ = self.run_worker("agy")
        with mock.patch.dict(os.environ, {"FAKE_KEEP_SESSION": "1"}):
            again, _ = self.run_worker("agy", resume=first["sessionId"])
        self.assertEqual(again["status"], "succeeded")
        self.assertEqual(again["workerStatus"], "DONE")
        self.assertEqual(again["sessionId"], first["sessionId"])

    def test_budget_counts_only_the_cost_a_resumed_claude_worker_added(self):
        with mock.patch.dict(os.environ, {"FAKE_COST": "0.06"}):
            first, _ = self.run_worker("claude")
        with mock.patch.dict(os.environ, {"FAKE_COST": "0.09"}):  # running total of the whole conversation
            resumed, state = self.run_worker("claude", resume=first["sessionId"], budget={"maxReportedCostUsd": 1})
        self.assertEqual(resumed["resumeIncrement"], "confirmed")
        self.assertAlmostEqual(resumed["metrics"]["totalCostUsd"], 0.09)  # outcome keeps what the provider said
        self.assertAlmostEqual(state["budgetUsage"]["reportedCostUsd"], 0.03, places=6)

    def test_budget_keeps_the_reported_total_when_the_earlier_session_is_unknown(self):
        with mock.patch.dict(os.environ, {"FAKE_COST": "0.09"}):
            resumed, state = self.run_worker("claude", resume="claude-sess-elsewhere", budget={"maxReportedCostUsd": 1})
        self.assertEqual(resumed["resumeIncrement"], "unconfirmed")
        self.assertAlmostEqual(state["budgetUsage"]["reportedCostUsd"], 0.09, places=6)

    def test_budget_for_a_worker_that_was_not_resumed_does_not_change(self):
        with mock.patch.dict(os.environ, {"FAKE_COST": "0.06"}):
            first, state = self.run_worker("claude", budget={"maxReportedCostUsd": 1})
        self.assertIsNone(first["resumeIncrement"])
        self.assertAlmostEqual(state["budgetUsage"]["reportedCostUsd"], 0.06, places=6)


if __name__ == "__main__":
    unittest.main()
