import contextlib
import copy
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
spec = importlib.util.spec_from_file_location("playbooks_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


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
try:
    import yaml
except ImportError:
    yaml = None


class PlaybookReaderTest(unittest.TestCase):
    @unittest.skipIf(yaml is None, "PyYAML is not installed")
    def test_bundled_playbooks_equal_pyyaml(self):
        text = runner.bundled_playbooks_path().read_text(encoding="utf-8")
        self.assertEqual(runner.parse_playbooks_yaml(text), yaml.safe_load(text))

    def test_supported_yaml_subset_preserves_types_and_quoted_comments(self):
        text = """name: 'a # b: c'
quote: 'it''s fine'
escaped: "line\\nnext"
items:
  - hello # comment
  - { model: "a,b", effort: low, enabled: true }
  - id: build
    workers: 2
    rules:
      - if: [deep, kind:code]
        use: { tier: strong }
values: [1, -2, 3.5, true, false, {}, []]
empty: {}
url: https://example.test/a#fragment
"""
        parsed = runner.parse_playbooks_yaml(text)
        self.assertEqual(parsed["name"], "a # b: c")
        self.assertEqual(parsed["quote"], "it's fine")
        self.assertEqual(parsed["escaped"], "line\nnext")
        self.assertEqual(parsed["items"][1], {"model": "a,b", "effort": "low", "enabled": True})
        self.assertEqual(parsed["items"][2]["rules"][0]["if"], ["deep", "kind:code"])
        self.assertEqual(parsed["values"], [1, -2, 3.5, True, False, {}, []])
        self.assertEqual(parsed["url"], "https://example.test/a#fragment")

    def test_block_lists_accept_quoted_mapping_keys_and_indentless_items(self):
        text = """providers:
- claude
- codex
phases:
- 'id': build
  \"tier\": cheap
next: []
"""
        self.assertEqual(runner.parse_playbooks_yaml(text), {
            "providers": ["claude", "codex"], "phases": [{"id": "build", "tier": "cheap"}], "next": [],
        })

    def test_unsupported_or_malformed_yaml_names_its_line(self):
        for invalid in (
            "bad: |\n  multiline\n", "bad: >\n  folded\n", "bad: &anchor value\n",
            "bad: *anchor\n", "bad: !!str value\n", "bad: [one, two\n",
            "bad: {a: 1, a: 2}\n", "bad: \"unterminated\n", "bad: null\n",
            "bad: yes\n", "bad: {a: 1} trailing\n", "bad: one: two\n",
            "bad:\n\tchild: value\n", "bad: [a,,b]\n", "bad: [a,]\n",
            "bad: {a 1}\n", "bad: \"\\q\"\n", "bad: [a: b]\n",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(runner.PlanError, r"line [23]"):
                    runner.parse_playbooks_yaml("version: 1\n" + invalid)

    def test_duplicate_block_keys_and_inconsistent_indentation_are_refused(self):
        for text in ("a: 1\na: 2\n", "a:\n  b: 1\n c: 2\n"):
            with self.subTest(text=text), self.assertRaisesRegex(runner.PlanError, r"line [23]"):
                runner.parse_playbooks_yaml(text)


class PlaybookSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="alethe-playbooks-")
        self.addCleanup(self.temporary.cleanup)
        # Resolve so Windows 8.3 short names (RUNNER~1) match the long paths the runner reports.
        self.root = Path(self.temporary.name).resolve()
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        self.policy = self.root / "policy.json"
        self.playbooks = self.root / runner.USER_PLAYBOOKS_FILE
        self.bundled = runner.bundled_playbooks_path().read_text(encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {
            "ALETHE_ORCHESTRATE_POLICY": str(self.policy),
            "ALETHE_ORCHESTRATE_PLAYBOOKS": str(self.playbooks),
            "ALETHE_ORCHESTRATE_HOME": str(self.root / "orchestrations"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(runner, "checkout_root", return_value=None), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), mock.patch.object(
            runner.subprocess, "Popen", side_effect=AssertionError("tests must not launch a CLI"),
        ):
            code = runner.main([str(arg) for arg in args])
        return code, output.getvalue(), errors.getvalue()

    def write_playbooks(self, text):
        self.playbooks.write_text(text, encoding="utf-8")

    def plan(self, provider="claude", **extra):
        return {"version": 1, "strategy": "balanced", "workers": [{
            "id": "build", "provider": provider, "cwd": str(self.root),
            "task": "Inspect one file.", **extra,
        }]}

    def resolve(self, raw, policy=None):
        return runner.validate_plan(raw, self.root / "plan.json", policy, policy_path=self.policy)

    def test_active_profile_merges_per_tier_provider_and_field(self):
        self.write_playbooks(self.bundled.replace("profile: base", "profile: savings").replace(
            "claude: { model: haiku, effort: medium }", "claude: { model: haiku }"))
        settings = runner.playbook_settings(self.policy)
        self.assertEqual(settings["tiers"]["standard"]["claude"], {"model": "haiku", "effort": "medium"})
        self.assertEqual(settings["tiers"]["cheap"]["claude"]["model"], "haiku")
        self.assertEqual(settings["tiers"]["strong"]["claude"]["model"], "sonnet")
        self.assertEqual(settings["quota"]["criticalThreshold"], 70)
        self.assertEqual(settings["quota"]["whenLow"], "switch-provider")
        settings["tiers"]["cheap"]["claude"]["model"] = "changed"
        self.assertEqual(runner.playbook_settings(self.policy)["tiers"]["cheap"]["claude"]["model"], "haiku")

    def test_settings_cache_reuses_parse_and_observes_edits_and_user_copy_removal(self):
        self.write_playbooks(self.bundled)
        with mock.patch.object(runner, "parse_playbooks_yaml", wraps=runner.parse_playbooks_yaml) as parse:
            runner.playbook_settings(self.policy)
            runner.playbook_settings(self.policy)
            self.assertEqual(parse.call_count, 1)
            self.write_playbooks(self.bundled.replace("profile: base", "profile: aggressive"))
            self.assertEqual(runner.playbook_settings(self.policy)["profile"], "aggressive")
            self.assertEqual(parse.call_count, 2)
        self.playbooks.unlink()
        self.assertEqual(runner.playbook_settings(self.policy)["profile"], "base")

    def test_profile_provider_order_is_resolved(self):
        self.write_playbooks(self.bundled.replace("    name: Base", "    order: [codex]\n    name: Base"))
        self.assertEqual(runner.playbook_settings(self.policy)["providers"], ["codex", "claude"])

    def test_all_three_tiers_resolve_from_bundled_playbooks(self):
        for tier, model, effort in (("cheap", "haiku", "low"), ("standard", "sonnet", "medium"), ("strong", "opus", "high")):
            with self.subTest(tier=tier):
                worker = self.resolve(self.plan(tier=tier))["workers"][0]
                self.assertEqual((worker["model"], worker["effort"]), (model, effort))

    def test_worker_fields_override_legacy_tiers_which_override_playbook_fields(self):
        policy = {**runner.DEFAULT_POLICY, "modelTiers": {"cheap": {"claude": {"model": "legacy"}}}}
        untouched = copy.deepcopy(policy)
        cheap = self.resolve(self.plan(tier="cheap"), policy)["workers"][0]
        self.assertEqual((cheap["model"], cheap["effort"]), ("legacy", "low"))
        explicit = self.resolve(self.plan(model="pinned", effort="high"), policy)["workers"][0]
        self.assertEqual((explicit["model"], explicit["effort"]), ("pinned", "high"))
        self.assertEqual(policy, untouched)

    def test_explicit_standard_fields_override_only_the_fields_they_pin(self):
        worker = self.resolve(self.plan(effort="high"))["workers"][0]
        self.assertEqual((worker["model"], worker["effort"]), ("sonnet", "high"))
        worker = self.resolve(self.plan(model="pinned"))["workers"][0]
        self.assertEqual((worker["model"], worker["effort"]), ("pinned", "medium"))

    def test_nonstandard_tiers_reject_explicit_model_or_effort(self):
        for tier in ("cheap", "strong"):
            for key in ("model", "effort"):
                with self.subTest(tier=tier, key=key), self.assertRaisesRegex(runner.PlanError, "cannot combine tier"):
                    self.resolve(self.plan(tier=tier, **{key: "high"}))

    def test_economy_defaults_to_cheap_but_respects_an_explicit_standard_tier(self):
        raw = {**self.plan(), "strategy": "economy"}
        original = copy.deepcopy(raw)
        worker = self.resolve(raw)["workers"][0]
        self.assertEqual((worker["tier"], worker["model"], worker["effort"]), ("cheap", "haiku", "low"))
        self.assertEqual(raw, original)
        for extra in ({"tier": "standard"}, {"model": "pinned"}, {"effort": "high"}):
            worker = self.resolve({**self.plan(**extra), "strategy": "economy"})["workers"][0]
            self.assertEqual(worker["tier"], "standard")

    def test_validate_and_dry_run_warn_for_every_unpinned_model_even_with_effort(self):
        raw = self.plan("codex", effort="high")
        raw["workers"].append({**raw["workers"][0], "id": "other", "provider": "agy"})
        path = self.root / "plan.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        for command in ("validate", "dry-run"):
            with self.subTest(command=command):
                code, output, errors = self.call(command, path, "--policy", self.policy)
                self.assertEqual(code, 0, errors)
                warnings = json.loads(output)["warnings"]
                for worker in raw["workers"]:
                    self.assertIn(
                        f"{worker['id']} runs on {worker['provider']}'s default model, which may be its most expensive; set a tier or a model",
                        warnings,
                    )
        self.assertFalse(runner.plan_warnings(self.resolve(self.plan())))

    def test_invalid_user_copy_is_refused_before_validate_dry_run_or_run(self):
        self.write_playbooks(self.bundled.replace("who: lead", "who: robot", 1))
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan()), encoding="utf-8")
        for command in ("validate", "dry-run", "run"):
            with self.subTest(command=command):
                code, _, errors = self.call(command, path, "--policy", self.policy)
                self.assertEqual(code, 2)
                self.assertIn(str(self.playbooks), errors)
                self.assertRegex(errors, r"line \d+.*who")
        self.assertFalse((self.root / ".alethe").exists())

    def test_structure_errors_have_lines_for_unknown_fields_and_enum_values(self):
        for before, after in (
            ("goal: orchestrate-and-save", "typo: value"),
            ("  cheap:\n", "  cheapest:\n"),
            ("        role: planner", "        typo: planner"),
            ("        tier: strong", "        tier: strongest"),
            ("        runner: research", "        runner: unlimited"),
            ("        then: done", "        then: retry"),
            ("profile: base", "profile: missing"),
            ("playbook: direct", "playbook: missing"),
            ("playbooks:\n", "playbooks: []\nunused:\n"),
            ("    phases:\n", "    phases: invalid\nunused:\n"),
        ):
            with self.subTest(after=after):
                self.write_playbooks(self.bundled.replace(before, after, 1))
                with self.assertRaisesRegex(runner.PlanError, r"line \d+"):
                    runner.playbook_settings(self.policy)

    def test_doctor_reports_playbook_errors_and_legacy_tier_migration_hint(self):
        self.write_playbooks(self.bundled.replace("profile: base", "profile: missing"))
        self.policy.write_text(json.dumps({**runner.DEFAULT_POLICY, "modelTiers": {
            "cheap": {"claude": {"model": "legacy"}},
        }}), encoding="utf-8")
        with mock.patch.object(runner, "probe_provider", side_effect=lambda p: {"provider": p, "installed": False}):
            with mock.patch.object(runner, "mode_scope", return_value=self.root.resolve()):
                code, output, errors = self.call("doctor", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        payload = json.loads(output)
        self.assertFalse(payload["playbooks"]["valid"])
        self.assertRegex(payload["playbooks"]["error"], r"line \d+.*profile")
        self.assertTrue(payload["policy"]["valid"])
        self.assertIn("deprecated", " ".join(payload["hints"]))
        self.assertIn(str(self.playbooks), " ".join(payload["hints"]))

    def test_pick_output_is_compact_and_json_has_the_same_selection_data(self):
        code, output, errors = self.call("playbook", "--pick", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        self.assertIn("profile: base", output)
        self.assertIn("providers: claude, codex", output)
        self.assertIn("direct", output)
        self.assertNotIn("phases:", output)
        self.assertLess(len(output.splitlines()), 35)
        code, output, errors = self.call("playbook", "--pick", "--json", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        payload = json.loads(output)
        self.assertEqual(payload["profile"], "base")
        self.assertEqual(payload["providers"], ["claude", "codex"])
        self.assertEqual(len(payload["pickOrder"]), 11)
        self.assertEqual({item["id"] for item in payload["playbooks"]}, set(runner.playbook_settings(self.policy)["playbooks"]))
        self.assertTrue(all(set(item) == {"id", "name", "pickWhen"} for item in payload["playbooks"]))

    def test_selected_playbook_includes_only_its_block_and_active_settings(self):
        self.write_playbooks(self.bundled.replace("profile: base", "profile: savings"))
        code, output, errors = self.call("playbook", "cascade", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        self.assertIn("cascade:\n", output)
        self.assertIn("    name: Cascade", output)
        self.assertNotIn("  direct:\n", output)
        self.assertIn("claude x strong -> model=sonnet effort=high", output)
        self.assertIn("providers: claude, codex", output)
        self.assertIn('"criticalThreshold": 70', output)
        self.assertIn('"warnAt": 80', output)
        code, output, errors = self.call("playbook", "cascade", "--json", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        payload = json.loads(output)
        self.assertEqual(payload["id"], "cascade")
        self.assertEqual(payload["playbook"]["name"], "Cascade")
        self.assertEqual(payload["tiers"]["strong"]["claude"]["model"], "sonnet")
        self.assertEqual(payload["quota"]["criticalThreshold"], 70)
        self.assertEqual(payload["budget"]["warnAt"], 80)

    def test_plain_playbook_and_path_stay_compatible_and_unknown_id_is_clear(self):
        code, output, errors = self.call("playbook", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        self.assertIn(self.bundled, output)
        code, output, errors = self.call("playbook", "--path", "--policy", self.policy)
        self.assertEqual(code, 0, errors)
        self.assertEqual(Path(output.strip()), runner.bundled_playbooks_path())
        code, _, errors = self.call("playbook", "missing", "--policy", self.policy)
        self.assertEqual(code, 2)
        self.assertIn("unknown playbook missing", errors)


if __name__ == "__main__":
    unittest.main()
