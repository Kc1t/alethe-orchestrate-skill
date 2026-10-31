import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "alethe" / "scripts" / "orchestrate.py"
TEMPLATE = ROOT / "skills" / "alethe" / "assets" / "editor.html"
spec = importlib.util.spec_from_file_location("editor_orchestrate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
NODE = shutil.which("node")


class EditorTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="editor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / "policy.json"
        patcher = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"),
            "ALETHE_ORCHESTRATE_POLICY": str(self.policy),
            "ALETHE_ORCHESTRATE_PLAYBOOKS": str(self.root / "alethe-playbooks.yaml"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def blocks(self, page):
        yaml = re.search(r'<script type="text/yaml" id="playbooks-yaml">\n(.*?)\n</script>', page, re.S).group(1)
        usage = re.search(r'<script type="application/json" id="live-usage">(.*?)</script>', page).group(1)
        prices = re.search(r'<script type="application/json" id="model-prices">(.*?)</script>', page).group(1)
        return yaml, json.loads(usage), json.loads(prices)

    def test_template_has_one_slot_for_each_block_and_no_external_resources(self):
        page = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(page.count(runner.EDITOR_PLAYBOOKS_SLOT), 1)
        self.assertEqual(page.count(runner.EDITOR_USAGE_SLOT), 1)
        self.assertEqual(page.count(runner.EDITOR_PRICES_SLOT), 1)
        self.assertNotRegex(page, r'<(?:script|link|img)[^>]+(?:src|href)="https?:')

    def test_editor_embeds_the_effective_playbooks_and_live_quota_without_opening(self):
        now = dt.datetime.now(dt.timezone.utc)
        runner.save_usage(self.policy, {"provider": "claude", "source": "fixture", "observedAt": now.isoformat(), "windows": {
            "5h": {"usedPercent": 26.4, "resetsAt": (now + dt.timedelta(hours=2)).isoformat()},
            "week": {"usedPercent": 69, "resetsAt": (now + dt.timedelta(hours=48, minutes=30)).isoformat()}}})
        target = self.root / "out" / "page.html"
        with mock.patch.object(runner, "open_page") as browser:
            code, out, err = self.call("editor", "--no-open", "--out", target, "--policy", self.policy)
        self.assertEqual(code, 0, err)
        browser.assert_not_called()
        payload = json.loads(out)
        self.assertEqual(Path(payload["path"]), target.resolve())
        self.assertFalse(payload["opened"])
        self.assertEqual(payload["liveUsage"], ["claude"])
        yaml, usage, _ = self.blocks(target.read_text(encoding="utf-8"))
        self.assertEqual(yaml, runner.bundled_playbooks_path().read_text(encoding="utf-8").rstrip("\n"))
        self.assertEqual(usage, {"claude": {"short": 26, "week": 69, "opus": 0, "reset": 49, "expires": None}})

    def test_editor_prefers_the_user_copy_escapes_script_ends_and_opens_the_page(self):
        copy = Path(os.environ["ALETHE_ORCHESTRATE_PLAYBOOKS"])
        text = runner.bundled_playbooks_path().read_text(encoding="utf-8")
        copy.write_text(text.replace("version: 1\n", "version: 1\n# ends with </SCRIPT> on purpose\n", 1), encoding="utf-8")
        with mock.patch.object(runner, "open_page", return_value=True) as browser:
            code, out, err = self.call("editor", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        page_path = self.policy.with_name(runner.EDITOR_FILE)
        browser.assert_called_once_with(page_path.resolve())
        self.assertTrue(json.loads(out)["opened"])
        page = page_path.read_text(encoding="utf-8")
        self.assertIn("<\\/SCRIPT>", page)
        self.assertNotIn("</SCRIPT>", page)

    def test_editor_carries_the_input_and_output_price_of_every_model_in_the_price_table(self):
        self.policy.with_name(runner.USER_PRICES_FILE).write_text(json.dumps({"models": {
            "claude/sonnet": {"input": 7},
            "acme/big": {"input": 12, "output": 40, "source": "https://example.test/prices", "asOf": "2026-10-01"},
        }}), encoding="utf-8")
        code, _, err = self.call("editor", "--no-open", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        _, _, prices = self.blocks(self.policy.with_name(runner.EDITOR_FILE).read_text(encoding="utf-8"))
        bundled = json.loads((ROOT / "skills" / "alethe" / runner.PRICES_FILE).read_text(encoding="utf-8"))["models"]
        self.assertEqual(set(prices), set(bundled) | {"acme/big"})
        self.assertEqual(prices["claude/sonnet"], {"input": 7, "output": bundled["claude/sonnet"]["output"]})
        self.assertEqual(prices["acme/big"], {"input": 12, "output": 40})
        self.assertTrue(all(set(entry) == {"input", "output"} for entry in prices.values()))

    def test_a_broken_price_table_leaves_the_prices_block_empty(self):
        self.policy.with_name(runner.USER_PRICES_FILE).write_text("{oops", encoding="utf-8")
        code, _, err = self.call("editor", "--no-open", "--policy", self.policy)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.blocks(self.policy.with_name(runner.EDITOR_FILE).read_text(encoding="utf-8"))[2], {})

    def test_editor_refuses_a_broken_playbooks_copy(self):
        Path(os.environ["ALETHE_ORCHESTRATE_PLAYBOOKS"]).write_text("version: 1\nplaybooks: [oops\n", encoding="utf-8")
        code, _, err = self.call("editor", "--no-open", "--policy", self.policy)
        self.assertEqual(code, 2)
        self.assertIn("invalid playbooks", err)
        self.assertFalse(self.policy.with_name(runner.EDITOR_FILE).exists())

    @unittest.skipUnless(NODE, "node is not installed")
    def test_page_reads_yaml_like_the_runner(self):
        page = TEMPLATE.read_text(encoding="utf-8")
        reader = page[page.index("const YAML_MAX_DEPTH"):page.index("function loadPlaybooks()")]
        cases = [
            runner.bundled_playbooks_path().read_text(encoding="utf-8"),
            "a:\n  - b: 1\n    c: {d: e, f: [1, 2.5, 'x # y']}\n  - plain # note\n",
            "- a\n- 'k: v': \"q\\u00e9\"\n  d: -1.5e3\n", "a: yes", "a: 0x10", "a: 1\na: 2", "a: {b: 1,}",
            "a:\n  b: 1\n   c: 2", "a: &x 1", "a: 'x'y", "  a: 1", "k: 1\n- x",
        ]
        script = self.root / "read.js"
        script.write_text(reader + "\nconst cases = JSON.parse(require('fs').readFileSync(0, 'utf8'))\n"
            "process.stdout.write(JSON.stringify(cases.map((c) => { try { return { ok: parsePlaybooksYaml(c) } } "
            "catch (e) { return { error: e.message } } })))\n", encoding="utf-8")
        completed = subprocess.run([NODE, str(script)], input=json.dumps(cases), capture_output=True, text=True,
                                   encoding="utf-8", check=True)
        for case, result in zip(cases, json.loads(completed.stdout)):
            try:
                expected = {"ok": json.loads(json.dumps(runner.parse_playbooks_yaml(case)))}
            except runner.PlanError as error:
                expected = {"error": str(error)}
            self.assertEqual(result, expected, case[:40])


if __name__ == "__main__":
    unittest.main()
