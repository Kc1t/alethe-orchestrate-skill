import contextlib
import datetime as dt
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
FAKE = Path(__file__).with_name("fake_usage_cli.py")
spec = importlib.util.spec_from_file_location("quota_guard_orchestrate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class QuotaGuardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="quota-guard-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / "policy.json"
        self.codex = self.root / "codex"
        self.codex.mkdir()
        patcher = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.codex),
            "ALETHE_ORCHESTRATE_POLICY": str(self.policy),
            "ALETHE_ORCHESTRATE_HOME": str(self.root / "orchestrations"),
            "ALETHE_ORCHESTRATE_PLAYBOOKS": str(self.root / "alethe-playbooks.yaml"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = dt.datetime.now(dt.timezone.utc)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def reading(self, provider, used, observed=None, resets=None, extra=None):
        windows = {"5h": {"usedPercent": used, "resetsAt": (resets or self.now + dt.timedelta(hours=2)).isoformat()}}
        runner.save_usage(self.policy, {"provider": provider, "source": "fixture",
            "observedAt": (observed or self.now).isoformat(), "windows": {**windows, **(extra or {})}})

    def plan(self, name, workers, **fields):
        path = self.root / (name + ".json")
        path.write_text(json.dumps({"version": 1, "strategy": "research", "maxConcurrent": 1, **fields, "workers": [
            {"id": worker, "provider": "codex", "mode": "read-only", "cwd": str(self.root),
             "task": "Read the fixture.", "model": "gpt-5.3-codex"} for worker in workers]}), encoding="utf-8")
        return path

    def fake(self, mode):
        def invocation(worker, cwd, worker_dir, *args):
            result = worker_dir / "result.md"
            return [sys.executable, str(FAKE), mode, str(result)], True, "jsonl", result
        return mock.patch.object(runner, "build_invocation", side_effect=invocation)

    def test_run_refuses_a_wave_past_the_stop_line_until_the_user_allows_it(self):
        self.reading("codex", 96)
        plan = self.plan("refused", ["scout"])
        with self.fake("codex"):
            code, _, err = self.call("run", plan, "--run-dir", self.root / "refused", "--policy", self.policy)
            self.assertEqual(code, 2)
            self.assertIn("codex is at 96% of its 5h window", err)
            self.assertIn("95% stop line", err)
            self.assertFalse((self.root / "refused").exists())
            code, _, err = self.call("run", plan, "--detach", "--run-dir", self.root / "detached", "--policy", self.policy)
            self.assertEqual(code, 2)
            self.assertFalse((self.root / "detached").exists())
            code, out, err = self.call("run", plan, "--run-dir", self.root / "allowed", "--policy", self.policy, "--allow-low-quota")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["workers"][0]["status"], "succeeded")

    def test_queued_workers_are_skipped_once_the_provider_crosses_the_stop_line(self):
        plan = self.plan("crossing", ["first", "second"])
        with self.fake("codex-high"):
            code, out, err = self.call("run", plan, "--run-dir", self.root / "crossing", "--policy", self.policy)
        self.assertEqual(code, 1, err)
        workers = {worker["id"]: worker for worker in json.loads(out)["workers"]}
        self.assertEqual(workers["first"]["status"], "succeeded")
        self.assertEqual(workers["second"]["status"], "skipped")
        self.assertEqual(workers["second"]["failureKind"], "quota_low")
        self.assertIn("97%", workers["second"]["error"])
        self.assertIn("ask the user", runner.FAILURE_HINTS["quota_low"])

    def test_stale_readings_still_block_until_their_window_resets(self):
        self.reading("codex", 96, observed=self.now - dt.timedelta(hours=3), resets=self.now + dt.timedelta(minutes=30))
        self.assertIsNotNone(runner.quota_refusal([{"provider": "codex", "model": None}], self.policy))
        self.reading("codex", 96, observed=self.now - dt.timedelta(hours=2), resets=self.now - dt.timedelta(minutes=1))
        self.assertIsNone(runner.quota_refusal([{"provider": "codex", "model": None}], self.policy))

    def test_opus_window_blocks_only_opus_workers(self):
        self.reading("claude", 50, extra={"opus": {"usedPercent": 97,
            "resetsAt": (self.now + dt.timedelta(days=2)).isoformat()}})
        self.assertIn("opus window", runner.quota_refusal([{"provider": "claude", "model": "opus"}], self.policy))
        self.assertIsNone(runner.quota_refusal([{"provider": "claude", "model": "sonnet"}], self.policy))

    def test_usage_marks_blocked_providers_and_summarizes_them(self):
        self.reading("claude", 96)
        payload = runner.usage_payload(self.policy, refresh=False)
        self.assertEqual(payload["stopAt"], 95)
        self.assertTrue(payload["providers"]["claude"]["blocked"])
        self.assertTrue(payload["providers"]["claude"]["windows"]["5h"]["blocked"])
        line = next(line for line in runner.usage_summary(payload) if line.startswith("Usage claude"))
        self.assertIn("[low, blocked]", line)

    def test_waves_record_what_each_worker_took_of_the_window(self):
        folder = self.codex / "sessions" / self.now.strftime("%Y/%m/%d")
        folder.mkdir(parents=True)
        event = {"timestamp": (self.now - dt.timedelta(minutes=1)).isoformat(), "type": "event_msg", "payload": {
            "type": "token_count", "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 20,
                "window_minutes": 300, "resets_at": (self.now + dt.timedelta(hours=2)).timestamp()}}}}
        (folder / "rollout-earlier-thread.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        with self.fake("codex"):
            code, _, err = self.call("run", self.plan("sampled", ["scout"]), "--run-dir", self.root / "sampled",
                                     "--policy", self.policy)
        self.assertEqual(code, 0, err)
        samples = runner.usage_file(self.policy)["samples"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["provider"], "codex")
        self.assertEqual(samples[0]["model"], "gpt-5.3-codex")
        self.assertEqual(samples[0]["perWorker"]["5h"], 13)

    def test_forecast_uses_recent_samples_and_warns_before_the_stop_line(self):
        self.reading("codex", 80)
        samples = [{"provider": "codex", "model": "gpt-5.3-codex", "workers": 1, "observedAt": self.now.isoformat(),
                    "perWorker": {"5h": share}} for share in (10, 12, 30)]
        workers = [{"id": name, "provider": "codex", "model": "gpt-5.3-codex"} for name in ("a", "b")]
        usage = runner.usage_payload(self.policy, refresh=False)
        forecast, warnings = runner.quota_forecast(workers, self.policy, usage)
        self.assertIsNone(forecast["codex"]["windows"]["5h"]["projectedPercent"])
        self.assertEqual(warnings, [])
        runner.save_quota_samples(self.policy, samples)
        forecast, warnings = runner.quota_forecast(workers, self.policy, usage)
        self.assertEqual(forecast["codex"]["windows"]["5h"]["projectedPercent"], 24)
        self.assertEqual(forecast["codex"]["windows"]["5h"]["afterPercent"], 104)
        self.assertEqual(len(warnings), 1)
        self.assertIn("ask the user first", warnings[0])
        other = [{"id": "c", "provider": "codex", "model": "unsampled"}]
        forecast, _ = runner.quota_forecast(other, self.policy, usage)
        self.assertEqual(forecast["codex"]["windows"]["5h"]["projectedPercent"], 12)
        code, out, err = self.call("dry-run", self.plan("forecast", ["a", "b"]), "--policy", self.policy)
        self.assertEqual(code, 0, err)
        preview = json.loads(out)
        self.assertEqual(preview["quotaForecast"]["codex"]["windows"]["5h"]["projectedPercent"], 24)
        self.assertTrue(any("stop line" in warning for warning in preview["warnings"]))

    def test_samples_are_capped_and_survive_new_readings(self):
        runner.save_quota_samples(self.policy, [{"provider": "codex", "model": None, "workers": 1,
            "observedAt": self.now.isoformat(), "perWorker": {"5h": index}} for index in range(runner.QUOTA_SAMPLE_LIMIT + 5)])
        self.reading("codex", 10)
        samples = runner.usage_file(self.policy)["samples"]
        self.assertEqual(len(samples), runner.QUOTA_SAMPLE_LIMIT)
        self.assertEqual(samples[0]["perWorker"]["5h"], 5)

    def test_refresh_claude_reads_quota_from_a_one_line_haiku_call(self):
        seen = []
        def invocation(worker, cwd, worker_dir, *args):
            seen.append(worker)
            return [sys.executable, str(FAKE), "claude", str(worker_dir / "result.md")], True, "jsonl", worker_dir / "result.md"
        with mock.patch.object(runner, "build_invocation", side_effect=invocation):
            code, out, err = self.call("usage", "--refresh-claude", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["providers"]["claude"]["windows"]["5h"]["usedPercent"], 86)
        self.assertEqual((seen[0]["model"], seen[0]["mode"], seen[0]["mcp"]), ("haiku", "read-only", False))
        with self.fake("codex"):
            code, _, err = self.call("usage", "--refresh-claude", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        self.assertIn("quota stays unknown", err)

    def test_agy_windows_guard_gemini_argon_and_third_party_models_independently(self):
        self.reading("agy", 30, extra={
            "week": {"usedPercent": 20, "resetsAt": (self.now + dt.timedelta(days=2)).isoformat()},
            "argon": {"usedPercent": 96, "resetsAt": (self.now + dt.timedelta(days=2)).isoformat()},
            "3p-5h": {"usedPercent": 97, "resetsAt": (self.now + dt.timedelta(hours=2)).isoformat()},
            "3p-week": {"usedPercent": 40, "resetsAt": (self.now + dt.timedelta(days=2)).isoformat()},
        })
        self.assertIsNone(runner.quota_refusal([{"provider": "agy", "model": "gemini-3.8-flash-medium"}], self.policy))
        self.assertIn("argon window", runner.quota_refusal([{"provider": "agy", "model": "gemini-4-argon-high"}], self.policy))
        self.assertIn("3p-5h window", runner.quota_refusal([{"provider": "agy", "model": "claude-opus-4-6"}], self.policy))
        advisory = runner.preview_usage_cost(
            [{"id": "a", "provider": "agy", "model": "gemini-4-argon-high", "estimatedPromptTokens": 500}],
            self.policy,
        )
        self.assertTrue(any("argon window" in w for w in advisory["warnings"]))

    def test_refresh_agy_reads_quota_buckets_from_usage_json(self):
        with mock.patch.object(runner, "resolve_executable", return_value=sys.executable), \
             mock.patch.object(runner, "launch_command", return_value=[sys.executable, str(FAKE), "agy-usage"]):
            code, out, err = self.call("usage", "--refresh-agy", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        agy = json.loads(out)["providers"]["agy"]
        self.assertEqual(agy["windows"]["5h"]["usedPercent"], 86)
        self.assertEqual(agy["windows"]["argon"]["usedPercent"], 97)
        self.assertEqual(agy["windows"]["3p-5h"]["usedPercent"], 48)


if __name__ == "__main__":
    unittest.main()
