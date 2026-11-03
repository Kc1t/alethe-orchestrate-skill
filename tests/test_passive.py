"""Passive hooks and transcript accounting use local fixtures, never agent CLIs."""
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

SCRIPT = Path(__file__).resolve().parent.parent / "skills/alethe/scripts/orchestrate.py"
spec = importlib.util.spec_from_file_location("passive_runner", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
hook_spec = importlib.util.spec_from_file_location("passive_hook_entry", SCRIPT.with_name("hook.py"))
h = importlib.util.module_from_spec(hook_spec)
hook_spec.loader.exec_module(h)


class PassiveTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="passive-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "orchestrations"
        self.policy = self.root / "config/policy.json"
        self.transcript = self.root / "session.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.now = dt.datetime(2026, 11, 3, 12, tzinfo=dt.timezone.utc).timestamp()
        patcher = mock.patch.dict(os.environ, {
            "ALETHE_ORCHESTRATE_HOME": str(self.home), "ALETHE_ORCHESTRATE_POLICY": str(self.policy),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
            "ALETHE_PASSIVE": "1", "ALETHE_ORCHESTRATE_WORKER": "0",
            "CLAUDE_CODE_SESSION_ID": "", "ALETHE_SESSION": "",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main([str(arg) for arg in args])
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def mode(self, action="show", *args):
        return json.loads(self.call("mode", action, "--cwd", self.project, *args))

    def hook(self, event="UserPromptSubmit", session="fixture", **fields):
        payload = {"cwd": str(self.project), "session_id": session,
                   "transcript_path": str(self.transcript), "hook_event_name": event, **fields}
        with mock.patch.object(r.time, "time", return_value=self.now), mock.patch.object(
            r.sys, "stdin", io.StringIO(json.dumps(payload))
        ):
            return self.call("hook")

    def entry_hook(self, event="PostToolUse", session="fixture", **fields):
        payload = {"cwd": str(self.project), "session_id": session,
                   "transcript_path": str(self.transcript), "hook_event_name": event, **fields}
        with mock.patch.object(h.time, "time", return_value=self.now):
            return h.hook_output(json.dumps(payload))

    def test_hook_entry_skips_full_path_until_timed_check_then_matches_it(self):
        self.hook()
        self.assertEqual(self.cache()["check"]["checkEveryMinutes"], 15)
        # A spend step and a quota change are intentionally deferred on tool calls.
        self.append(self.response(input_tokens=2000000))
        self.save_quota(provider="claude", percent=90)
        with mock.patch.object(h, "full_hook_output", side_effect=AssertionError("must not import runner")) as full:
            self.assertEqual(self.entry_hook(), "")
            full.assert_not_called()
        self.now += 15 * 60
        path = r.passive_state_path("fixture")
        before = path.read_bytes()
        expected = self.hook("PostToolUse")
        path.write_bytes(before)
        with mock.patch.object(h, "full_hook_output", side_effect=r.hook_output) as full:
            self.assertEqual(self.entry_hook(), expected)
            full.assert_called_once()
        self.assertEqual(json.loads(expected)["hookSpecificOutput"]["hookEventName"], "PostToolUse")

    def test_hook_entry_prompts_still_check_spend_immediately(self):
        self.hook()
        self.append(self.response(input_tokens=2000000))
        with mock.patch.object(h, "full_hook_output", side_effect=r.hook_output) as full:
            self.assertIn("this session", self.entry_hook("UserPromptSubmit"))
            full.assert_called_once()

    def test_hook_entry_worker_and_disabled_modes_never_load_runner(self):
        with mock.patch.object(h, "full_hook_output", side_effect=AssertionError("must not import runner")) as full:
            with mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_WORKER": "1"}), mock.patch.object(
                h.sys, "stdin", None
            ):
                self.assertEqual(h.main(), 0)  # Workers do not even read stdin.
                self.assertEqual(self.entry_hook(), "")
            self.mode("off")
            self.assertEqual(self.entry_hook(), "")
            self.assertEqual(self.entry_hook("UserPromptSubmit"), "")
            self.call("mode", "off", "--everywhere")
            self.assertEqual(self.entry_hook(), "")
            r.save_mode_state({"modes": {}, "everywhere": None, "offIn": [], "passive": True})
            with mock.patch.dict(os.environ, {"ALETHE_PASSIVE": "0"}):
                self.assertEqual(self.entry_hook(), "")
            full.assert_not_called()

    def test_hook_entry_respects_explicit_passive_over_disabled_default(self):
        self.mode("on", "--goal", "passive")
        with mock.patch.dict(os.environ, {"ALETHE_PASSIVE": "0"}), mock.patch.object(
            h, "full_hook_output", side_effect=r.hook_output
        ) as full:
            self.assertIn("alethe skill", self.entry_hook("UserPromptSubmit"))
            full.assert_called_once()
        self.mode("on", "--goal", "save")
        with mock.patch.object(h, "full_hook_output", side_effect=AssertionError("no tool reminder")) as full:
            self.assertEqual(self.entry_hook(), "")
            full.assert_not_called()
        with mock.patch.object(h, "full_hook_output", side_effect=r.hook_output):
            self.assertIn("Goal: save", self.entry_hook("UserPromptSubmit"))

    def test_hook_entry_initializes_old_state_and_uses_custom_interval(self):
        settings = {**r.PASSIVE_DEFAULTS, "checkEveryMinutes": 1}
        with mock.patch.object(r, "playbook_settings", return_value={"passive": settings}):
            self.hook()
        self.assertEqual(self.cache()["check"]["checkEveryMinutes"], 1)
        self.now += 59
        with mock.patch.object(h, "full_hook_output", side_effect=AssertionError("not due")) as full:
            self.assertEqual(self.entry_hook(), "")
            full.assert_not_called()
        self.now += 1
        with mock.patch.object(h, "full_hook_output", return_value="due") as full:
            self.assertEqual(self.entry_hook(), "due")
            full.assert_called_once()
        old = self.cache()
        del old["check"]["checkEveryMinutes"]
        r.atomic_write_json(r.passive_state_path("fixture"), old)
        with mock.patch.object(h, "full_hook_output", side_effect=r.hook_output) as full:
            self.entry_hook()
            full.assert_called_once()
        self.assertEqual(self.cache()["check"]["checkEveryMinutes"], 15)

    def test_hook_state_naming_and_home_match_runner(self):
        for session in ("plain-id", "../a/b\\c: d", "a?b", "a/b", "", "日本語", "long" * 100):
            self.assertEqual(h.session_state_filename(session), r.session_state_filename(session))
            self.assertEqual(Path(h.orchestrations_home()) / "passive" / h.session_state_filename(session),
                             r.passive_state_path(session))
        with mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_HOME": ""}):
            self.assertEqual(Path(h.orchestrations_home()), r.orchestrations_home())
            with mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_POLICY": "", "XDG_CONFIG_HOME": str(self.root)}):
                self.assertEqual(Path(h.orchestrations_home()), r.orchestrations_home())

    def test_session_current_pointers_are_independent_and_list_marks_calling_session(self):
        global_folder = Path(json.loads(self.call("new", "global"))["folder"])
        global_pointer = (self.home / "current.json").read_bytes()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session/a"}):
            self.assertIsNone(r.current_folder())
            with self.assertRaises(r.PlanError):
                r.resolve_folder(None)
            folder_a = Path(json.loads(self.call("new", "a"))["folder"])
            self.assertEqual(r.current_folder(), folder_a)
            self.assertEqual(r.current_pointer_path().parent, self.home / "current")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session?a"}):
            self.assertIsNone(r.current_folder())
            folder_b = Path(json.loads(self.call("new", "b"))["folder"])
            self.assertEqual(r.current_folder(), folder_b)
        for session, folder in (("session/a", folder_a), ("session?a", folder_b)):
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": session}):
                self.assertEqual(r.resolve_folder(None), folder)
                self.call("note", session)
                entries = json.loads(self.call("list", "--format", "json"))
                self.assertEqual([entry["folder"] for entry in entries if entry["current"]], [str(folder)])
                self.assertEqual(r.read_journal(folder)[0]["text"], session)
        self.assertEqual(r.current_folder(), global_folder)
        self.assertEqual((self.home / "current.json").read_bytes(), global_pointer)

    def test_alethe_session_use_sets_pointer_without_global_fallback(self):
        folder = Path(json.loads(self.call("new", "global"))["folder"])
        with mock.patch.dict(os.environ, {"ALETHE_SESSION": "alternative"}):
            self.assertIsNone(r.current_folder())
            self.call("use", folder)
            self.assertEqual(r.current_folder(), folder)
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "claude-wins"}):
                self.assertIsNone(r.current_folder())
        self.assertEqual(r.current_folder(), folder)

    def test_spend_prefers_environment_session_and_falls_back_when_missing(self):
        folder = r.claude_project_folder(self.project)
        folder.mkdir(parents=True)
        active, newest = folder / "active.jsonl", folder / "newest.jsonl"
        self.append(self.response("active"), path=active)
        self.append(self.response("newest"), path=newest)
        os.utime(active, (10, 10))
        os.utime(newest, (20, 20))
        with mock.patch.object(r.Path, "cwd", return_value=self.project), mock.patch.dict(
            os.environ, {"CLAUDE_CODE_SESSION_ID": "active"}
        ):
            self.assertEqual(json.loads(self.call("spend", "--json"))["transcript"], str(active))
            self.assertEqual(json.loads(self.call("spend", "--session", "newest", "--json"))["transcript"], str(newest))
            self.assertEqual(json.loads(self.call("spend", "--transcript", self.transcript, "--json"))["transcript"], str(self.transcript))
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "missing"}):
                self.assertEqual(json.loads(self.call("spend", "--json"))["transcript"], str(newest))

    def test_passive_brief_names_skill_and_absolute_command_path(self):
        brief = r.passive_brief(r.PASSIVE_DEFAULTS)
        self.assertIn("alethe skill", brief)
        self.assertIn(str(SCRIPT.resolve()), brief)
        self.assertLessEqual(len(brief.split()), 87 + 25)

    def response(self, identity="a", model="claude-opus-5-5", input_tokens=100,
                 output_tokens=10, cache_read_input_tokens=900, cache_creation_input_tokens=20, **fields):
        return {"type": "assistant", "timestamp": "2026-11-03T12:00:00Z", **fields,
                "message": {"id": identity, "model": model, "usage": {
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read_input_tokens,
                    "cache_creation_input_tokens": cache_creation_input_tokens}}}

    def append(self, *events, path=None):
        with (path or self.transcript).open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def spend(self):
        return json.loads(self.call("spend", "--transcript", self.transcript, "--json"))

    def cache(self, session="fixture"):
        return json.loads(r.passive_state_path(session).read_text(encoding="utf-8"))

    def save_quota(self, percent=4, reset_seconds=7200, provider="codex", observed=None):
        reading = {"provider": provider, "observedAt": dt.datetime.fromtimestamp(
            observed if observed is not None else self.now, dt.timezone.utc).isoformat(),
            "source": "fixture", "windows": {
                name: {"usedPercent": percent if name == "5h" else 12,
                       "resetsAt": dt.datetime.fromtimestamp(self.now + reset_seconds, dt.timezone.utc).isoformat()}
                for name in ("5h", "week")}}
        r.save_usage(self.policy, reading)
        return reading

    def codex_rollout(self, percent=4, reset_seconds=7200):
        path = Path(os.environ["CODEX_HOME"]) / "sessions/2026/11/03/rollout-fixture.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": dt.datetime.fromtimestamp(self.now, dt.timezone.utc).isoformat(),
                 "payload": {"rate_limits": {"primary": {
                     "used_percent": percent, "window_minutes": 300, "resets_at": self.now + reset_seconds},
                     "secondary": {"used_percent": 12, "window_minutes": 10080,
                                   "resets_at": self.now + 86400}}}}
        self.append(event, path=path)
        return path

    def test_default_mode_and_doctor_explain_passive(self):
        mode = self.mode()
        self.assertTrue(mode["on"])
        self.assertTrue(mode["passive"])
        self.assertEqual((mode["goal"], mode["source"], mode["reason"]), ("passive", "default", "default"))
        with mock.patch.object(r, "probe_provider", side_effect=lambda provider: {
            "provider": provider, "installed": False, "launch": None
        }), mock.patch.object(r.Path, "cwd", return_value=self.project):
            self.assertEqual(r.doctor_payload(self.policy)["mode"]["source"], "default")
        # Old modes.json files default to passive too.
        r.save_mode_state({"modes": {}, "everywhere": None, "offIn": []})
        self.assertEqual(self.mode()["source"], "default")

    def test_first_prompt_then_quiet_and_sessions_are_independent(self):
        self.append(self.response())
        first = self.hook()
        self.assertIn("work normally", first)
        self.assertIn("without a playbook", first)
        self.assertIn("Codex first", first)
        self.assertIn("this session ~$", first)
        self.assertLessEqual(len(first.splitlines()[0].split()), 120)
        self.assertEqual(self.hook(), "")
        self.assertIn("work normally", self.hook(session="other"))

    def test_time_check_and_post_tool_json_are_throttled(self):
        self.hook()
        with mock.patch.object(r, "codex_quota", side_effect=AssertionError("no session scan")):
            self.assertEqual(self.hook("PostToolUse"), "")
        self.now += 15 * 60
        with mock.patch.object(r, "codex_quota", wraps=r.codex_quota) as quota:
            value = json.loads(self.hook("PostToolUse"))
            self.assertEqual(quota.call_count, 1)
        context = value["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "PostToolUse")
        self.assertIn("Alethe passive: this session", context["additionalContext"])
        self.assertNotIn("work normally", context["additionalContext"])
        self.assertEqual(self.hook("PostToolUse"), "")
        self.now += 15 * 60
        line = self.hook()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("this session", line)

    def test_quota_line_gives_short_reset_time_and_long_reset_date(self):
        self.save_quota(percent=4, reset_seconds=3 * 86400)
        usage = r.usage_payload(self.policy, refresh=False, now=dt.datetime.fromtimestamp(self.now, dt.timezone.utc))
        text = r.passive_quota_text(usage, r.load_prices(self.policy), r.playbook_settings(self.policy)["passive"])
        reset = dt.datetime.fromtimestamp(self.now + 3 * 86400, dt.timezone.utc).astimezone()
        self.assertIn("5h 4% " + reset.strftime("(resets %H:%M)"), text)
        # A weekday alone is ambiguous when the reset falls on the same weekday as today.
        self.assertIn("week 12% " + reset.strftime("(resets %a %b %d %H:%M)"), text)

    def test_post_tool_does_not_print_first_prompt_brief(self):
        self.assertEqual(self.hook("PostToolUse"), "")
        self.now += 15 * 60
        output = self.hook("PostToolUse")
        self.assertIn("hookSpecificOutput", json.loads(output))
        self.assertNotIn("work normally", output)
        self.assertIn("work normally", self.hook())

    def test_spend_step_fires_immediately(self):
        self.append(self.response(input_tokens=1000000, output_tokens=0,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
        self.assertIn("~$4.00 list", self.hook())
        self.append(self.response("b", input_tokens=500000, output_tokens=0,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
        line = self.hook()
        self.assertIn("~$6.00 list", line)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(self.hook(), "")
        self.append(self.response("c", input_tokens=1000000, output_tokens=0,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
        self.assertIn("~$10.00 list", json.loads(self.hook("PostToolUse"))[
            "hookSpecificOutput"]["additionalContext"])

    def test_off_project_and_everywhere_persist_and_can_reenable(self):
        self.mode("off")
        self.assertIn(str(self.project), r.read_mode_state(self.now)["offIn"])
        self.assertEqual(self.mode()["reason"], "project off")
        self.assertEqual(self.hook(), "")
        self.assertEqual(self.hook("PostToolUse"), "")
        self.mode("on", "--goal", "passive")
        self.assertEqual(self.mode()["source"], "project")
        self.assertIn("work normally", self.hook())
        self.mode("off")
        self.call("mode", "off", "--everywhere")
        state = r.read_mode_state(self.now)
        self.assertFalse(state["passive"])
        self.assertIsNone(state["everywhere"])
        self.assertEqual(self.mode()["reason"], "everywhere off")
        self.assertEqual(self.hook(session="new-session"), "")
        for goal in ("save", "passive"):
            self.call("mode", "on", "--everywhere", "--goal", goal)
            self.assertTrue(r.read_mode_state(self.now)["passive"])
            self.assertEqual(self.mode()["source"], "everywhere")
            self.call("mode", "off", "--everywhere")

    def test_environment_only_disables_default_and_worker_always_silent(self):
        with mock.patch.dict(os.environ, {"ALETHE_PASSIVE": "0"}):
            self.assertEqual(self.hook(), "")
            self.assertEqual(self.mode()["reason"], "ALETHE_PASSIVE=0")
            for goal in ("passive", "save", "balanced", "max", "freestyle"):
                self.mode("on", "--goal", goal)
                self.assertIn("Alethe", self.hook(session=goal))
                with mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_WORKER": "1"}), mock.patch.object(
                    r, "read_mode_state", side_effect=AssertionError("worker must return before reads")
                ):
                    self.assertEqual(self.hook(), "")
                    self.assertEqual(self.hook("PostToolUse"), "")
        self.mode("off")

    def test_explicit_modes_remind_only_on_prompts(self):
        for goal in ("save", "balanced", "max", "freestyle"):
            self.mode("on", "--goal", goal)
            self.assertIn("Goal: " + goal, self.hook())
            self.assertEqual(self.hook("PostToolUse"), "")
            self.assertIn("Goal: " + goal, self.hook())

    def test_parse_dedup_malformed_sidechain_subagents_prices_and_timestamps(self):
        lead = self.response()
        fallback = self.response(None, model="claude-sonnet-99-1", requestId="request", timestamp="2026-11-03T13:00:00Z")
        side = self.response("side", isSidechain=True, agentId="agent-review")
        self.append(lead, lead, fallback, fallback, side, {"type": "user"}, [1, 2],
                    {"type": "assistant", "message": "broken"})
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write("{malformed}\n")
        sub = self.transcript.parent / self.transcript.stem / "subagents/agent-review.jsonl"
        sub.parent.mkdir(parents=True)
        # A response repeated in a subagent file is still counted once.
        self.append(side, self.response("sub", model="unpublished-model"), path=sub)
        payload = self.spend()
        lead_total = payload["lead"]["total"]
        self.assertEqual(lead_total["responses"], 2)
        self.assertEqual(lead_total["inputTokens"], 200)
        self.assertEqual(lead_total["cacheReadTokens"], 1800)
        self.assertEqual(lead_total["outputTokens"], 20)
        self.assertEqual(lead_total["cacheWriteTokens"], 40)
        self.assertAlmostEqual(lead_total["cacheReadShare"], 900 / 1020)
        self.assertEqual(lead_total["firstTimestamp"], "2026-11-03T12:00:00+00:00")
        self.assertEqual(lead_total["lastTimestamp"], "2026-11-03T13:00:00+00:00")
        self.assertAlmostEqual(payload["lead"]["models"]["claude-opus-5-5"]["listPriceEstimateUsd"], 0.00088)
        self.assertEqual(payload["lead"]["models"]["claude-sonnet-99-1"]["priceKey"], "claude/sonnet")
        self.assertEqual(payload["delegated"]["total"]["responses"], 2)
        self.assertEqual(payload["delegatedRuns"], 1)
        self.assertIsNone(payload["delegated"]["models"]["unpublished-model"]["listPriceEstimateUsd"])
        self.assertIsNone(payload["total"]["total"]["listPriceEstimateUsd"])
        self.assertEqual(payload["total"]["total"]["responses"], 4)
        self.assertIn("cost unknown", self.call("spend", "--transcript", self.transcript))

    def test_exact_user_prices_and_unknown_cache_prices_are_not_guessed(self):
        self.policy.parent.mkdir(parents=True)
        prices = {"models": {"claude/claude-opus-5-5": {"input": 10},
                             "claude/custom": {"input": 1, "output": 2, "asOf": "2026-11-03",
                                               "source": "https://example.test/prices"}}}
        self.policy.with_name(r.USER_PRICES_FILE).write_text(json.dumps(prices), encoding="utf-8")
        self.append(self.response(input_tokens=1000000, output_tokens=0,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    self.response("custom", model="custom"))
        payload = self.spend()
        self.assertEqual(payload["lead"]["models"]["claude-opus-5-5"]["listPriceEstimateUsd"], 10)
        self.assertIsNone(payload["lead"]["models"]["custom"]["listPriceEstimateUsd"])

    def test_incremental_append_partial_line_and_truncation_match_full_parse(self):
        self.append(self.response())
        first = self.spend()
        cache = self.cache("session")["spend"]
        self.assertEqual(cache["files"][str(self.transcript)]["offset"], self.transcript.stat().st_size)
        partial = json.dumps(self.response("b"))
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(partial[:50])
        self.assertEqual(self.spend(), first)
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(partial[50:] + "\n")
        self.append(self.response(), self.response("c"))
        appended = self.spend()
        self.assertEqual(appended["lead"]["total"]["responses"], 3)
        full = {}
        r.update_session_spend(self.transcript, full)
        self.assertEqual(appended, r.session_spend_payload(full, self.policy))
        # No transcript open at all on an unchanged second read.
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path == self.transcript:
                raise AssertionError("unchanged transcript should not be opened")
            return original_open(path, *args, **kwargs)
        with mock.patch.object(Path, "open", guarded_open):
            self.assertEqual(self.spend(), appended)
        self.transcript.write_text("", encoding="utf-8")
        self.append(self.response("new", model="claude-haiku-5-5"))
        truncated = self.spend()
        self.assertEqual(truncated["lead"]["total"]["responses"], 1)
        self.assertEqual(list(truncated["lead"]["models"]), ["claude-haiku-5-5"])

    def test_rotation_and_new_or_removed_subagents(self):
        self.append(self.response())
        self.spend()
        replacement = self.root / "replacement"
        replacement.write_text(json.dumps(self.response("replacement")) + "\n", encoding="utf-8")
        replacement.replace(self.transcript)
        self.assertEqual(self.spend()["lead"]["total"]["responses"], 1)
        sub = self.root / "session/subagents/agent-b.jsonl"
        sub.parent.mkdir(parents=True)
        self.append(self.response("delegated"), path=sub)
        self.assertEqual(self.spend()["delegated"]["total"]["responses"], 1)
        sub.unlink()
        self.assertEqual(self.spend()["delegated"]["total"]["responses"], 0)
        # Rewrite in place to a larger file: the old boundary detects replacement.
        self.transcript.write_text(json.dumps(self.response("longer-new-identity")) + "\n" +
                                   json.dumps(self.response("also-new")) + "\n", encoding="utf-8")
        self.assertEqual(self.spend()["lead"]["total"]["responses"], 2)

    def test_default_transcript_selection_and_explicit_session(self):
        folder = r.claude_project_folder(self.project)
        self.assertEqual(folder.name, "".join(char if char.isalnum() else "-" for char in str(self.project)))
        # Claude Code names a project folder after the absolute path with every non-alphanumeric
        # character replaced by "-", so the drive letter stays on Windows.
        if os.name == "nt":
            self.assertEqual(r.claude_project_folder(Path("C:/Users/kc/Developer/alethe/poc")).name,
                             "C--Users-kc-Developer-alethe-poc")
        else:
            self.assertEqual(r.claude_project_folder(Path("/Users/kauamiguel/Developer/alethe/poc")).name,
                             "-Users-kauamiguel-Developer-alethe-poc")
        folder.mkdir(parents=True)
        old = folder / "old.jsonl"
        newest = folder / "new.jsonl"
        self.append(self.response("old"), path=old)
        self.append(self.response("new"), path=newest)
        os.utime(old, (10, 10))
        os.utime(newest, (20, 20))
        with mock.patch.object(r.Path, "cwd", return_value=self.project):
            self.assertEqual(json.loads(self.call("spend", "--json"))["transcript"], str(newest))
            self.assertEqual(json.loads(self.call("spend", "--session", "old", "--json"))["transcript"], str(old))
        with self.assertRaises(r.PlanError):
            r.spend_payload(session="../escape", cwd=self.project)

    def test_quota_low_blocked_reset_changes_and_stale_unknown(self):
        self.codex_rollout()
        self.save_quota(provider="claude", observed=self.now - 3600)
        first = self.hook()
        self.assertRegex(first, r"Codex 5h 4% \(resets \d\d:\d\d\), week 12% \(resets \w{3} \w{3} \d\d \d\d:\d\d\)")
        self.assertIn("room to spare", first)
        self.assertIn("Claude 5h unknown", first)
        self.codex_rollout(percent=85)
        original_glob = Path.glob
        def guarded_glob(path, pattern):
            if path == Path(os.environ["CODEX_HOME"]) / "sessions":
                raise AssertionError("no session scan between checks")
            return original_glob(path, pattern)
        with mock.patch.object(Path, "glob", guarded_glob):
            self.assertIn("low, ask", self.hook())
            self.assertEqual(self.hook(), "")
        self.codex_rollout(percent=97)
        self.assertIn("blocked, ask", json.loads(self.hook("PostToolUse"))["hookSpecificOutput"]["additionalContext"])
        self.codex_rollout(percent=2, reset_seconds=60)
        self.assertIn("this session", self.hook())
        self.now += 61
        self.assertIn("5h unknown", self.hook())
        self.assertEqual(self.hook(), "")

    def test_new_low_saved_provider_is_reported_without_codex_scan(self):
        self.hook()
        self.save_quota(provider="claude", percent=90)
        with mock.patch.object(r, "codex_quota", side_effect=AssertionError("no scan")):
            self.assertIn("low, ask", self.hook())

    def test_billing_preference_and_custom_check_settings(self):
        self.codex_rollout()
        self.policy.parent.mkdir(parents=True)
        self.policy.with_name(r.USER_PRICES_FILE).write_text(json.dumps({"billing": {"codex": "api"}}), encoding="utf-8")
        self.assertNotIn("room to spare", self.hook())
        settings = {"checkEveryMinutes": 1, "spendStepUsd": 1, "prefer": ["claude", "codex"], "askAboveWorkers": 1}
        with mock.patch.object(r, "playbook_settings", return_value={"passive": settings}):
            self.now += 60
            self.assertIn("this session", self.hook())

    def test_antigravity_appears_only_with_a_reading_and_spare_room_follows_prefer(self):
        now = dt.datetime.fromtimestamp(self.now, dt.timezone.utc)
        settings = dict(r.PASSIVE_DEFAULTS)
        prices = r.load_prices(self.policy)
        usage = r.usage_payload(self.policy, refresh=False, now=now)
        self.assertNotIn("Antigravity", r.passive_quota_text(usage, prices, settings))
        self.save_quota(percent=10, provider="agy")
        usage = r.usage_payload(self.policy, refresh=False, now=now)
        text = r.passive_quota_text(usage, prices, settings)
        self.assertIn("Antigravity 5h 10%", text)
        # Not in the default prefer list, so its spare room is not advertised.
        self.assertNotRegex(text, r"Antigravity[^.]*room to spare")
        text = r.passive_quota_text(usage, prices, {**settings, "prefer": ["codex", "agy", "claude"]})
        self.assertRegex(text, r"Antigravity[^.]*: room to spare")
        self.save_quota(percent=10, provider="claude")
        usage = r.usage_payload(self.policy, refresh=False, now=now)
        text = r.passive_quota_text(usage, prices, {**settings, "prefer": ["claude", "codex"]})
        self.assertNotRegex(text, r"Claude[^.]*room to spare")

    def test_passive_config_defaults_overrides_and_validation(self):
        data = r.playbook_settings(self.policy)
        self.assertEqual(data["passive"], r.PASSIVE_DEFAULTS)
        source = r.bundled_playbooks_path().read_text(encoding="utf-8")
        parsed = r.parse_playbooks_yaml(source)
        del parsed["passive"]
        with mock.patch.object(r, "loaded_playbooks", return_value=(source, parsed)):
            self.assertEqual(r.playbook_settings(self.policy)["passive"], r.PASSIVE_DEFAULTS)
            parsed["passive"] = {"checkEveryMinutes": 2}
            self.assertEqual(r.playbook_settings(self.policy)["passive"]["checkEveryMinutes"], 2)
        for field, invalid in (("checkEveryMinutes", 0), ("spendStepUsd", float("inf")),
                               ("askAboveWorkers", True), ("prefer", ["codex", "codex"]),
                               ("prefer", ["unknown"]), ("prefer", []), ("typo", 2)):
            parsed["passive"] = {field: invalid}
            self.assertTrue(any("passive." + field in error for error in r.playbook_structure_errors(parsed)), field)
        parsed["passive"] = {"checkEveryMinutes": 0.5, "prefer": ["claude"]}
        self.assertEqual(r.playbook_structure_errors(parsed), [])

    def test_hook_errors_are_silent_and_missing_transcript_is_unknown(self):
        self.assertIn("this session has no lead responses yet", self.hook(transcript_path=str(self.root / "missing")))
        self.append(self.response(model="claude-unpriced-9"))
        self.now += 15 * 60
        self.assertIn("list-price estimate unknown on unpriced-9", self.hook(session="unpriced"))
        with mock.patch.object(r, "update_session_spend", side_effect=OSError("busy file")):
            self.assertEqual(self.hook(session="error"), "")
        self.assertEqual(r.hook_output("not-json"), "")
        self.assertEqual(r.hook_output("[]"), "")
        with mock.patch.object(r, "hook_stdin", side_effect=OSError("unreadable stdin")):
            self.assertEqual(self.call("hook"), "")
        self.assertEqual(self.hook("Stop"), "")


if __name__ == "__main__":
    unittest.main()
