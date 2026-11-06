import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "alethe" / "scripts" / "orchestrate.py"
TEMPLATE = ROOT / "skills" / "alethe" / "assets" / "report.html"
FAKE = Path(__file__).with_name("fake_usage_cli.py")
spec = importlib.util.spec_from_file_location("report_page_orchestrate", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ReportPageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="report-page-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        patcher = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / "codex"),
            "ALETHE_ORCHESTRATE_POLICY": str(self.root / "policy.json"),
            "ALETHE_ORCHESTRATE_HOME": str(self.root / "orchestrations"),
            "ALETHE_ORCHESTRATE_PLAYBOOKS": str(self.root / "alethe-playbooks.yaml"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def orchestrate(self):
        def invocation(worker, cwd, worker_dir, *args):
            result = worker_dir / "result.md"
            return [sys.executable, str(FAKE), worker["provider"], str(result)], True, "jsonl", result
        plan = self.root / "wave.json"
        plan.write_text(json.dumps({"version": 1, "strategy": "research", "maxConcurrent": 2, "phase": "scout", "workers": [
            {"id": "scout-claude", "provider": "claude", "model": "haiku", "mode": "read-only", "cwd": str(self.root),
             "why": "reads </script> safely", "task": "Read."},
            {"id": "scout-codex", "provider": "codex", "model": "gpt-5.3-codex", "mode": "read-only", "cwd": str(self.root),
             "task": "Read."}]}), encoding="utf-8")
        with mock.patch.object(runner, "build_invocation", side_effect=invocation):
            self.assertEqual(self.call("new", "Page check")[0], 0)
            code, _, err = self.call("run", plan, "--in", "current")
        self.assertEqual(code, 0, err)
        return runner.current_folder()

    def embedded(self, page):
        return json.loads(re.search(r'<script type="application/json" id="report-data">\n(.*?)\n</script>', page, re.S).group(1))

    def test_template_has_one_report_slot_and_no_external_resources(self):
        page = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(page.count(runner.REPORT_SLOT), 1)
        self.assertNotRegex(page, r'<(?:script|link|img)[^>]+(?:src|href)="https?:')
        self.assertFalse(any(chr(code) in page for code in (0x2028, 0x2029)))

    def test_html_report_embeds_the_json_report_in_the_folder_and_opens_it(self):
        folder = self.orchestrate()
        with mock.patch.object(runner, "open_page", return_value=True) as browser:
            code, out, err = self.call("report", "--format", "html")
        self.assertEqual(code, 0, err)
        page_path = folder / runner.REPORT_PAGE_FILE
        self.assertEqual(json.loads(out), {"path": str(page_path.resolve()), "opened": True})
        browser.assert_called_once_with(page_path.resolve())
        page = page_path.read_text(encoding="utf-8")
        self.assertNotIn("reads </script>", page)
        data = self.embedded(page)
        _, expected, _ = self.call("report", "--format", "json")
        expected = json.loads(expected)
        data.pop("generatedAt")
        expected.pop("generatedAt")
        self.assertEqual(data, expected)
        self.assertEqual(sorted(data["byProvider"]), ["claude", "codex"])
        self.assertEqual(len(runner.wave_dirs(folder)), 1)

    def test_html_report_writes_where_asked_without_opening(self):
        self.orchestrate()
        target = self.root / "out" / "page.html"
        with mock.patch.object(runner, "open_page") as browser:
            code, out, err = self.call("report", "--format", "html", "--no-open", "--write", target)
        self.assertEqual(code, 0, err)
        browser.assert_not_called()
        self.assertFalse(json.loads(out)["opened"])
        self.assertEqual(self.embedded(target.read_text(encoding="utf-8"))["title"], "Page check")

    def test_lead_comes_from_the_flag_or_from_claude_code(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_EFFORT": "high"}):
            self.assertEqual(runner.lead_identity("Codex:gpt-5.5"),
                             {"provider": "codex", "model": "gpt-5.5", "effort": None, "source": "flag"})
            self.assertEqual(runner.lead_identity(None),
                             {"provider": "claude", "model": None, "effort": "high", "source": "environment"})
        with mock.patch.dict(os.environ, {"CLAUDECODE": ""}):
            self.assertIsNone(runner.lead_identity(None))
        with self.assertRaises(runner.PlanError):
            runner.lead_identity("claude:opus; rm")

    def test_report_and_status_name_the_lead(self):
        def invocation(worker, cwd, worker_dir, *args):
            result = worker_dir / "result.md"
            return [sys.executable, str(FAKE), worker["provider"], str(result)], True, "jsonl", result
        plan = self.root / "lead.json"
        plan.write_text(json.dumps({"version": 1, "strategy": "research", "workers": [
            {"id": "one", "provider": "codex", "mode": "read-only", "cwd": str(self.root), "task": "Read."}]}), encoding="utf-8")
        with mock.patch.object(runner, "build_invocation", side_effect=invocation):
            self.assertEqual(self.call("new", "Led", "--lead", "claude:opus")[0], 0)
            self.assertEqual(self.call("run", plan, "--in", "current")[0], 0)
        _, report, _ = self.call("report")
        self.assertIn("Lead: claude (opus) planned the waves and wrote the prompts", report)
        _, status, _ = self.call("status")
        self.assertIn("| lead claude (opus)", status.splitlines()[0])
        _, data, _ = self.call("report", "--format", "json")
        self.assertEqual(json.loads(data)["lead"]["model"], "opus")

    def test_no_open_is_refused_outside_html(self):
        self.orchestrate()
        code, _, err = self.call("report", "--no-open")
        self.assertEqual(code, 2)
        self.assertIn("--no-open only applies to --format html", err)


if __name__ == "__main__":
    unittest.main()
