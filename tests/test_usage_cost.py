import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "alethe" / "scripts" / "orchestrate.py"
spec = importlib.util.spec_from_file_location("usage_orchestrate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class UsageCostTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="usage-cost-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
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
        self.now = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def rollout(self, thread="mine", percent=33, observed=None):
        folder = self.codex / "sessions" / "2026" / "10" / "22"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("rollout-2026-10-22-" + thread + ".jsonl")
        event = {"timestamp": observed or self.now.isoformat(), "type": "event_msg", "payload": {
            "type": "token_count", "rate_limits": {"limit_id": "codex", "primary": {
                "used_percent": percent, "window_minutes": 300,
                "resets_at": (self.now + dt.timedelta(hours=2)).timestamp()},
                "secondary": {"used_percent": 29, "window_minutes": 10080,
                              "resets_at": (self.now + dt.timedelta(days=3)).timestamp()}, "credits": {}}}}
        path.write_text("x" * (300 * 1024) + "\n" + json.dumps(event) + "\n{bad\n", encoding="utf-8")
        return path

    def test_codex_turn_usage_is_summed_without_counting_cache_reads(self):
        events = [{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80,
                   "output_tokens": 10}}, {"type": "turn.completed", "usage": {"input_tokens": 200,
                   "cached_input_tokens": 100, "output_tokens": 20}}]
        metrics = runner.extract_metrics(events)
        self.assertEqual(metrics["usage"]["inputTokens"], 120)
        self.assertEqual(metrics["usage"]["cacheReadTokens"], 180)
        self.assertEqual(metrics["usage"]["outputTokens"], 30)
        self.assertEqual(runner.reported_total_tokens(metrics["usage"]), 150)

    def test_claude_final_usage_and_model_cost_are_preserved(self):
        events = [{"type": "assistant", "usage": {"input_tokens": 900}},
                  {"type": "result", "usage": {"input_tokens": 20, "cache_read_input_tokens": 800,
                   "cache_creation_input_tokens": 40, "output_tokens": 10}, "total_cost_usd": 0.02,
                   "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.02}}}]
        metrics = runner.extract_metrics(events)
        self.assertEqual(runner.reported_total_tokens(metrics["usage"]), 70)
        self.assertEqual(metrics["usage"]["cacheReadTokens"], 800)
        self.assertEqual(metrics["costByModel"], {"claude-sonnet-5-5": 0.02})
        self.assertEqual(metrics["totalCostUsd"], 0.02)

    def test_missing_usage_stays_unknown(self):
        self.assertEqual(runner.extract_metrics([{"type": "turn.failed"}]), {})
        self.assertIsNone(runner.reported_total_tokens({}))

    def test_last_claude_quota_event_maps_all_reported_windows(self):
        events = [{"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": percent, "resetsAt": 1792677600},
            "seven_day": {"utilization": 0.08, "resetsAt": 1792936800},
            "seven_day_opus": {"utilization": 0.9, "resetsAt": 1792936800}}}}
            for percent in (0.1, 0.86)]
        quota = runner.claude_quota(events, self.now.isoformat())
        self.assertEqual(quota["provider"], "claude")
        self.assertEqual(quota["windows"]["5h"]["usedPercent"], 86)
        self.assertEqual(quota["windows"]["opus"]["usedPercent"], 90)
        self.assertIn("T", quota["windows"]["week"]["resetsAt"])

    def test_codex_reader_prefers_worker_thread_and_reads_last_tail_event(self):
        mine = self.rollout()
        os.utime(mine, (self.now.timestamp(), self.now.timestamp()))
        other = self.rollout("other", 99)
        os.utime(other, (self.now.timestamp()+10, self.now.timestamp()+10))
        quota = runner.codex_quota("mine")
        self.assertEqual(quota["windows"]["5h"]["usedPercent"], 33)
        self.assertEqual(quota["source"], str(mine))
        self.assertEqual(runner.codex_quota()["windows"]["5h"]["usedPercent"], 99)

    def test_absent_or_invalid_codex_files_do_not_invent_usage(self):
        self.assertIsNone(runner.codex_quota())
        path = self.rollout()
        path.write_text('{"payload":{"rate_limits":null}}\n', encoding="utf-8")
        self.assertIsNone(runner.codex_quota())

    def test_usage_refresh_keeps_observation_age_and_reports_expired_resets(self):
        old = self.now - dt.timedelta(minutes=45)
        self.rollout(observed=old.isoformat())
        runner.save_usage(self.policy, {"provider": "claude", "observedAt": old.isoformat(),
            "source": "stream-json", "windows": {"5h": {"usedPercent": 99,
            "resetsAt": (self.now-dt.timedelta(minutes=1)).isoformat()},
            "week": {"usedPercent": 81, "resetsAt": (self.now+dt.timedelta(days=1)).isoformat()}}})
        payload = runner.usage_payload(self.policy, now=self.now)
        claude = payload["providers"]["claude"]
        self.assertEqual(claude["windows"]["5h"]["usedPercent"], 0)
        self.assertTrue(claude["windows"]["5h"]["reset"])
        self.assertTrue(claude["low"])
        self.assertTrue(claude["stale"])
        self.assertEqual(payload["providers"]["codex"]["ageMinutes"], 45)
        self.assertTrue((self.root / "alethe-usage.json").is_file())

    def test_quota_thresholds_follow_the_active_profile(self):
        bundled = (SCRIPT.parents[1] / "playbooks.yaml").read_text(encoding="utf-8")
        copy = self.root / "alethe-playbooks.yaml"
        copy.write_text(bundled, encoding="utf-8")
        self.assertEqual(runner.quota_thresholds(self.policy), (80, 95))
        copy.write_text(bundled.replace("\nprofile: base\n", "\nprofile: savings\n"), encoding="utf-8")
        self.assertEqual(runner.quota_thresholds(self.policy), (70, 90))
        copy.write_text(bundled.replace("  stopAt: 95\n", "  stopAt: 60\n"), encoding="utf-8")
        with self.assertRaisesRegex(runner.PlanError, "stopAt"):
            runner.quota_thresholds(self.policy)

    def test_models_merge_prices_overrides_playbooks_and_codex_cache(self):
        (self.root / "alethe-playbooks.yaml").write_text(
            "version: 1\nmodels:\n  codex:\n    mystery: {costMultiplier: 3}\ntiers:\n  cheap:\n    claude: {model: sonnet}\n", encoding="utf-8")
        (self.codex / "models_cache.json").write_text(json.dumps({"models": [{"slug": "unpriced-codex"}]}), encoding="utf-8")
        (self.root / "alethe-prices.json").write_text(json.dumps({"models": {"claude/sonnet": {
            "input": 2, "output": 10, "asOf": "2026-01-01", "source": "https://claude.com/pricing"}},
            "billing": {"claude": "subscription"}}), encoding="utf-8")
        payload = runner.models_payload(self.policy, now=self.now)
        claude = payload["providers"]["claude"]
        self.assertEqual(claude["billing"], "subscription")
        sonnet = next(m for m in claude["models"] if m["model"] == "sonnet")
        self.assertEqual(sonnet["input"], 2)
        self.assertTrue(sonnet["stale"])
        codex = {m["model"]: m for m in payload["providers"]["codex"]["models"]}
        self.assertTrue(codex["unpriced-codex"]["missing"])
        self.assertEqual(codex["mystery"]["rank"], "high")

    def test_explicit_totals_are_preserved_when_parts_are_unavailable(self):
        metrics = runner.extract_metrics([{"usage": {"total_tokens": 80}}])
        self.assertEqual(runner.reported_total_tokens(metrics["usage"]), 80)

    def test_invalid_cached_token_count_fails_at_the_boundary(self):
        with self.assertRaises(runner.PlanError):
            runner.extract_metrics([{"type": "turn.completed", "usage": {
                "input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 2}}])

    def test_invalid_price_override_is_an_actionable_cli_error(self):
        (self.root / "alethe-prices.json").write_text(json.dumps({"models": {
            "claude/sonnet": {"input": -1}}}), encoding="utf-8")
        code, _, err = self.call("models", "--policy", str(self.policy))
        self.assertEqual(code, 2)
        self.assertIn("claude/sonnet.input", err)

    def test_dry_run_warns_for_each_low_provider_and_estimates_only_priced_workers(self):
        current = dt.datetime.now(dt.timezone.utc)
        for provider, used in (("claude", 86), ("codex", 33)):
            runner.save_usage(self.policy, {"provider": provider, "observedAt": current.isoformat(),
                "source": "fixture", "windows": {"5h": {"usedPercent": used,
                "resetsAt": (current+dt.timedelta(hours=1)).isoformat()}}})
        workers = [{"id": "a", "provider": "claude", "model": "sonnet", "estimatedPromptTokens": 1000},
                   {"id": "b", "provider": "claude", "model": "unpriced", "estimatedPromptTokens": 100}]
        advisory = runner.preview_usage_cost(workers, self.policy)
        self.assertEqual(len(advisory["warnings"]), 2)
        self.assertIn("claude is at 86%", advisory["warnings"][0])
        self.assertIn("consider codex at 33%", advisory["warnings"][0])
        price = runner.load_prices(self.policy)["models"]["claude/sonnet"]["input"]
        self.assertEqual(advisory["workers"][0]["listPriceEstimateUsd"], 1000*price/1000000)
        self.assertIsNone(advisory["total"])
        self.assertIn("estimate", advisory["label"])
        self.assertTrue(advisory["asOf"])

    def test_workers_persist_real_shaped_quota_and_correct_budget_totals(self):
        import sys
        fake = Path(__file__).with_name("fake_usage_cli.py")
        for provider, mode, code, tokens in (("claude", "claude", 0, 70),
                ("codex", "codex", 0, 150), ("claude", "claude-fail", 1, 70)):
            plan = self.root / (mode + ".json")
            folder = self.root / mode
            plan.write_text(json.dumps({"version": 1, "strategy": "economy", "workers": [{
                "id": "fixture", "provider": provider, "mode": "read-only", "cwd": str(self.root),
                "task": "Read the fixture.", "model": "sonnet" if provider == "claude" else "gpt-5.3-codex"}]}), encoding="utf-8")
            def invocation(worker, cwd, worker_dir, *args):
                result = worker_dir / "result.md"
                return [sys.executable, str(fake), mode, str(result)], True, "jsonl", result
            with mock.patch.object(runner, "build_invocation", side_effect=invocation):
                result_code, out, err = self.call("run", str(plan), "--run-dir", str(folder), "--policy", str(self.policy))
            self.assertEqual(result_code, code, err)
            result = json.loads(out)
            self.assertEqual(result["budgetUsage"]["reportedTokens"], tokens)
            outcome_path = folder / "workers" / "fixture" / "outcome.json"
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            self.assertEqual(outcome["quota"]["provider"], provider)
            self.assertNotIn("quotaError", outcome)
            cached = json.loads((self.root / "alethe-usage.json").read_text(encoding="utf-8"))
            self.assertEqual(cached["providers"][provider], outcome["quota"])
            report = runner.build_report([folder])
            self.assertEqual(report["totals"]["tokens"], tokens)
            self.assertIn("cache reads separately", runner.render_markdown(report))
            self.assertNotIn("listPriceEstimateUsd", report["totals"])

    def test_claude_quota_events_after_the_result_do_not_turn_success_into_failure(self):
        stdout = self.root / "stdout.log"
        stdout.write_text(json.dumps({"type": "result", "subtype": "success", "result": "Finished."}) + "\n" +
            json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}) + "\n", encoding="utf-8")
        status, _, _, error = runner.classify_result("claude", "jsonl", stdout, self.root / "result.md", 0, False)
        self.assertEqual(status, "succeeded", error)

    def test_usage_and_price_age_boundaries_and_opus_only_limits(self):
        runner.save_usage(self.policy, {"provider": "claude", "source": "fixture",
            "observedAt": (self.now-dt.timedelta(minutes=30)).isoformat(), "windows": {
                "5h": {"usedPercent": 79, "resetsAt": (self.now+dt.timedelta(hours=1)).isoformat()},
                "opus": {"usedPercent": 99, "resetsAt": (self.now+dt.timedelta(hours=1)).isoformat()}}})
        reading = runner.usage_payload(self.policy, refresh=False, now=self.now)["providers"]["claude"]
        self.assertFalse(reading["stale"])
        self.assertFalse(reading["low"])
        self.assertTrue(reading["windows"]["opus"]["low"])
        self.assertFalse(reading["windows"]["5h"]["reset"])

    def test_older_readings_cannot_replace_newer_provider_readings(self):
        reading = {"provider": "claude", "source": "fixture", "observedAt": self.now.isoformat(),
                   "windows": {"5h": {"usedPercent": 80, "resetsAt": (self.now+dt.timedelta(hours=1)).isoformat()}}}
        runner.save_usage(self.policy, reading)
        runner.save_usage(self.policy, {**reading, "observedAt": (self.now-dt.timedelta(hours=1)).isoformat()})
        self.assertEqual(runner.saved_usage(self.policy)["claude"], reading)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_invalid_cache_is_an_actionable_cli_error(self):
        (self.root / "alethe-usage.json").write_text('{"providers":{"claude": []}}', encoding="utf-8")
        code, _, err = self.call("usage", "--policy", str(self.policy))
        self.assertEqual(code, 2)
        self.assertIn("claude", err)

    def test_models_inventory_handles_flow_and_block_lists(self):
        (self.root / "alethe-playbooks.yaml").write_text(
            "version: 1\nmodels:\n  claude: [sonnet, {id: other, cost: 3}]\n  codex:\n    - id: custom-codex\n      cost: 2\ntiers:\n  cheap:\n    agy:\n      model: custom-gemini\n", encoding="utf-8")
        inventory = runner.playbook_model_inventory(self.policy)
        self.assertIn("sonnet", inventory["claude"])
        self.assertEqual(inventory["claude"]["other"], 3)
        self.assertEqual(inventory["codex"]["custom-codex"], 2)
        self.assertIn("custom-gemini", inventory["agy"])

    def test_token_budget_excludes_cache_reads_when_admitting_the_next_worker(self):
        import sys
        fake = Path(__file__).with_name("fake_usage_cli.py")
        plan = self.root / "budget-plan.json"
        workers = [{"id": worker, "provider": "claude", "mode": "read-only", "cwd": str(self.root),
                    "task": "Read the fixture.", "model": "sonnet"} for worker in ("first", "second")]
        plan.write_text(json.dumps({"version": 1, "strategy": "research", "maxConcurrent": 1,
            "budget": {"maxReportedTokens": 100}, "workers": workers}), encoding="utf-8")
        def invocation(worker, cwd, worker_dir, *args):
            result = worker_dir / "result.md"
            return [sys.executable, str(fake), "claude", str(result)], True, "jsonl", result
        with mock.patch.object(runner, "build_invocation", side_effect=invocation):
            code, out, err = self.call("run", str(plan), "--run-dir", str(self.root / "budget-run"), "--policy", str(self.policy))
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["budgetUsage"]["calls"], 2)
        self.assertEqual(payload["budgetUsage"]["reportedTokens"], 140)
        self.assertTrue(all(worker["status"] == "succeeded" for worker in payload["workers"]))

    def test_usage_and_models_commands_default_to_json(self):
        for command in ("usage", "models"):
            for flags in ([], ["--json"]):
                code, out, err = self.call(command, "--policy", str(self.policy), *flags)
                self.assertEqual(code, 0, err)
                self.assertIn("providers", json.loads(out))


if __name__ == "__main__":
    unittest.main()
