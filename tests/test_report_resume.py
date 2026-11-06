"""The report must not count a resumed session's running total twice (measured 2026-11-04).

Claude's total_cost_usd and the agy token usage of a resumed worker include the earlier turns of the
same conversation. Real round 1 of the resume experiment: E1 0.0709145, A (new session) 0.0797338,
B resumes E1 0.0951905, C resumes E1 again 0.1224037. Summed as reported = 0.3682; real spend = 0.2021.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "alethe" / "scripts" / "orchestrate.py"
spec = importlib.util.spec_from_file_location("resume_report_orchestrate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

E1_SESSION = "67a82ae2-15a8-432b-b6b2-520a1ca2d125"
A_SESSION = "86b31446-91ec-46d2-b101-4e93abe4e42e"


def usage(inp, out):
    return {"inputTokens": inp, "cacheReadTokens": 0, "cacheWriteTokens": 0, "outputTokens": out}


class ResumeReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="report-resume-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.count = 0

    def run_dir(self, worker_id, provider, session, cost=None, tokens=None, resume=None):
        self.count += 1
        run = self.root / f"{self.count:02d}-wave"
        folder = run / "workers" / worker_id
        folder.mkdir(parents=True)
        started = f"2026-11-04T05:4{self.count}:00+00:00"
        metrics = {}
        if cost is not None:
            metrics["totalCostUsd"] = cost
        if tokens is not None:
            metrics["usage"] = usage(*tokens)
        (run / "state.json").write_text(json.dumps({
            "version": 1, "runId": f"r{self.count}", "status": "done", "startedAt": started, "finishedAt": started,
            "workers": {worker_id: {"status": "succeeded"}},
        }), encoding="utf-8")
        (run / "plan.json").write_text(json.dumps({
            "workers": [{"id": worker_id, "provider": provider, "resume": resume}],
        }), encoding="utf-8")
        (folder / "outcome.json").write_text(json.dumps({
            "provider": provider, "status": "succeeded", "sessionId": session, "metrics": metrics,
            "durationSeconds": 10, "workerStatus": "DONE",
        }), encoding="utf-8")
        (folder / "result.md").write_text("STATUS: DONE\nSUMMARY: ok\n", encoding="utf-8")
        return run

    def report(self, runs):
        return runner.build_report(runs)

    def by_id(self, report):
        return {w["id"]: w for run in report["runs"] for w in run["workers"]}

    def test_claude_round_one_real_numbers(self):
        runs = [
            self.run_dir("e1", "claude", E1_SESSION, cost=0.0709145),
            self.run_dir("a", "claude", A_SESSION, cost=0.0797338),
            self.run_dir("b", "claude", E1_SESSION, cost=0.09519050000000001),
            self.run_dir("c", "claude", E1_SESSION, cost=0.1224037),
        ]
        report = self.report(runs)
        workers = self.by_id(report)
        self.assertAlmostEqual(report["totals"]["costUsd"], 0.2021375, places=6)
        self.assertLess(report["totals"]["costUsd"], 0.21)  # not the 0.368 the old report showed
        self.assertAlmostEqual(workers["b"]["costUsd"], 0.0242760, places=6)
        self.assertAlmostEqual(workers["c"]["costUsd"], 0.0272132, places=6)
        self.assertAlmostEqual(workers["b"]["costUsdReported"], 0.0951905, places=6)
        self.assertEqual(workers["b"]["incremental"], "confirmed")
        self.assertEqual(report["totals"]["resumedIncremental"], ["b", "c"])
        self.assertEqual(report["totals"]["resumedUnconfirmed"], [])

    def test_workers_that_were_not_resumed_do_not_change(self):
        runs = [
            self.run_dir("x", "claude", "sess-x", cost=0.05, tokens=(1000, 200)),
            self.run_dir("y", "claude", "sess-y", cost=0.07, tokens=(2000, 300)),
            self.run_dir("z", "claude", None, cost=0.01),
        ]
        report = self.report(runs)
        workers = self.by_id(report)
        self.assertAlmostEqual(report["totals"]["costUsd"], 0.13, places=9)
        self.assertEqual(report["totals"]["tokens"], 3500)
        for name in ("x", "y", "z"):
            self.assertIsNone(workers[name]["incremental"])
            self.assertEqual(workers[name]["costUsd"], workers[name]["costUsdReported"])
        self.assertEqual(report["totals"]["resumedIncremental"], [])
        self.assertEqual(report["totals"]["resumedUnconfirmed"], [])

    def test_claude_tokens_are_not_subtracted(self):
        runs = [
            self.run_dir("e1", "claude", E1_SESSION, cost=0.07, tokens=(1000, 500)),
            self.run_dir("b", "claude", E1_SESSION, cost=0.09, tokens=(300, 100)),
        ]
        report = self.report(runs)
        self.assertEqual(report["totals"]["tokens"], 1500 + 400)

    def test_agy_resumed_tokens_become_increment(self):
        runs = [
            self.run_dir("e1", "agy", "agy-1", tokens=(100000, 3000)),
            self.run_dir("b", "agy", "agy-1", tokens=(119855, 4905)),
        ]
        report = self.report(runs)
        workers = self.by_id(report)
        self.assertEqual(workers["b"]["tokens"], (119855 + 4905) - (100000 + 3000))
        self.assertEqual(workers["b"]["tokensReported"], 119855 + 4905)
        self.assertEqual(report["totals"]["tokens"], 124760)
        self.assertEqual(workers["b"]["incremental"], "confirmed")

    def test_unknown_earlier_total_is_marked_not_confirmed(self):
        # B resumes a session whose first worker is not part of this report.
        runs = [self.run_dir("b", "claude", E1_SESSION, cost=0.095, resume=E1_SESSION)]
        report = self.report(runs)
        worker = self.by_id(report)["b"]
        self.assertEqual(worker["incremental"], "unconfirmed")
        self.assertEqual(worker["costUsd"], 0.095)  # nothing invented
        self.assertEqual(report["totals"]["costUsd"], 0.095)
        self.assertEqual(report["totals"]["resumedUnconfirmed"], ["b"])
        self.assertIn("Incremental not confirmed", runner.render_markdown(report))

    def test_missing_cost_or_lower_total_is_not_confirmed(self):
        runs = [
            self.run_dir("e1", "claude", "s1", cost=0.08),
            self.run_dir("b", "claude", "s1", cost=0.05),  # lower than before: cannot be an accumulated total
            self.run_dir("e2", "claude", "s2"),            # no cost reported at all
            self.run_dir("c", "claude", "s2", cost=0.03),
        ]
        workers = self.by_id(self.report(runs))
        self.assertEqual(workers["b"]["incremental"], "unconfirmed")
        self.assertEqual(workers["b"]["costUsd"], 0.05)
        self.assertEqual(workers["c"]["incremental"], "unconfirmed")
        self.assertEqual(workers["c"]["costUsd"], 0.03)

    def test_provider_with_unknown_accumulation_is_left_alone(self):
        runs = [
            self.run_dir("e1", "codex", "t1", tokens=(1000, 100)),
            self.run_dir("b", "codex", "t1", tokens=(1500, 150)),
        ]
        report = self.report(runs)
        self.assertEqual(report["totals"]["tokens"], 1100 + 1650)
        self.assertEqual(self.by_id(report)["b"]["incremental"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
