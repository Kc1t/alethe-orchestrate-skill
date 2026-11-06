import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills/alethe/scripts/orchestrate.py"
FAKE = Path(__file__).with_name("fake_security_cli.py")
spec = importlib.util.spec_from_file_location("security_runner", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class SecurityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="security-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.home = self.root / "orchestrations"
        self.policy = self.root / "policy.json"
        environment = mock.patch.dict(os.environ, {
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
            "ALETHE_ORCHESTRATE_POLICY": str(self.policy), "ALETHE_ORCHESTRATE_HOME": str(self.home),
            "ALETHE_ORCHESTRATE_PLAYBOOKS": str(self.root / "playbooks.yaml"),
            "CLAUDE_CODE_SESSION_ID": "", "ALETHE_SESSION": "",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def worker(self, provider="claude", **fields):
        return {"id": "one", "provider": provider, "mode": "read-only", "cwd": str(self.root),
                "task": "Read the fixture.", **fields}

    def resolve(self, worker, policy=None, directory=None, orchestration=None):
        return r.validate_plan({"version": 1, "workers": [worker]}, (directory or self.root) / "plan.json",
                               policy, policy_path=self.policy, orchestration=orchestration)

    @contextlib.contextmanager
    def fake_cli(self):
        with mock.patch.object(r, "resolve_executable", side_effect=lambda name: name), mock.patch.object(
            r, "launch_command", side_effect=lambda name: [sys.executable, str(FAKE), name]
        ):
            yield

    def invocation(self, worker, cap=None):
        resolved = self.resolve(worker)["workers"][0]
        return r.build_invocation(resolved, self.root, self.root, {}, threading.Lock(), 30, cap)[0]

    def test_policy_trust_defaults_round_trip_and_validation(self):
        self.assertEqual(r.load_policy(self.policy)["trust"], {"allowUnsafe": False, "cwdRoots": [], "workerEnv": []})
        policy = {"trust": {"allowUnsafe": True, "cwdRoots": [str(self.root)], "workerEnv": ["EXTRA_TOKEN"]}}
        r.save_policy(self.policy, policy)
        self.assertEqual(r.load_policy(self.policy)["trust"], policy["trust"])
        for trust in ({"allowUnsafe": 1}, {"cwdRoots": ["relative"]}, {"workerEnv": ["BAD=NAME"]}, {"extra": True}):
            with self.subTest(trust=trust), self.assertRaises(r.PlanError):
                r.validate_policy({"trust": trust})

    def test_unsupported_read_only_providers_refused_and_auto_filtered(self):
        for provider in ("kimi", "custom"):
            with self.subTest(provider=provider), self.assertRaisesRegex(r.PlanError, "mode: write.*isolate: true"):
                self.resolve(self.worker(provider))
        with mock.patch.object(r, "available_builtin_providers", return_value=["kimi", "codex"]):
            plan = self.resolve(self.worker("auto"), {"providerOrder": ["kimi", "codex"]})
        self.assertEqual(plan["workers"][0]["provider"], "codex")
        custom = self.worker("custom", mode="write", isolate=True, command=sys.executable)
        self.assertTrue(self.resolve(custom)["workers"][0]["isolate"])

    def test_custom_unsafe_flags_cannot_grant_themselves_trust(self):
        bypasses = [[flag] for flag in r.UNSAFE_FLAGS] + [
            ["--yolo=true"], ["--dangerously-new-bypass"], ["--sandbox=danger-full-access"],
            ["--sandbox", "danger-full-access"], ["-s", "danger-full-access"], ["-sdanger-full-access"],
            ["--permission-mode", "bypassPermissions"], ["--permission-mode=bypassPermissions"],
            ["-c", 'sandbox_mode="danger-full-access"'], ["--config=sandbox_mode=danger-full-access"],
            ["-c", "approval_policy=never"], ["--config", "sandbox_permissions=require_escalated"],
            ["-c", "permissions.defaultMode=bypassPermissions"], ["-c", "disable_sandbox=true"],
            ["--config", "sandbox_enabled=false"], ["-capproval_policy=never"],
            ["-a", "never"], ["--ask-for-approval=never"],
            ["--config", "approvals.enabled=false"], ["-c", "sandbox.mode=danger-full-access"],
        ]
        for args in bypasses:
            worker = self.worker("custom", mode="write", command=sys.executable, args=args)
            with self.subTest(args=args):
                with self.assertRaisesRegex(r.PlanError, "unsafe flag"):
                    self.resolve(worker)
                with self.assertRaisesRegex(r.PlanError, "trust.allowUnsafe.*saved user policy"):
                    self.resolve({**worker, "allowUnsafe": True})
                allowed = self.resolve({**worker, "allowUnsafe": True}, {"trust": {"allowUnsafe": True}})
                self.assertTrue(allowed["workers"][0]["allowUnsafe"])
        for args in (["--sandbox", "read-only"], ["--config", "model_reasoning_effort=high"],
                     ["--config", "sandbox_mode=workspace-write"], ["--full-auto"]):
            self.assertIsNone(r.unsafe_argument(args))

    def test_substituted_custom_arguments_are_checked_again(self):
        worker = self.resolve(self.worker("custom", mode="write", command=sys.executable, args=["{cwd}"]))["workers"][0]
        with self.assertRaisesRegex(r.PlanError, "unsafe flag"):
            r.build_invocation(worker, Path("--yolo"), self.root, {}, threading.Lock(), 30)
        worker["args"] = ["{result}"]
        with mock.patch.object(r, "check_custom_safety", wraps=r.check_custom_safety) as check:
            r.build_invocation(worker, self.root, self.root, {}, threading.Lock(), 30)
        self.assertEqual(check.call_args.args[0], [str(self.root / "result.md")])

    def test_help_failure_retried_and_not_cached(self):
        cache, lock = {}, threading.Lock()
        for failure in (OSError("unavailable"), subprocess.TimeoutExpired("cli", 1)):
            with self.subTest(failure=failure), mock.patch.object(r.subprocess, "run", side_effect=failure) as run:
                self.assertEqual(r.command_output(["fake", "--help"], cache, lock), "")
                self.assertEqual(run.call_count, 2)
                self.assertEqual(cache, {})
        actual_run = subprocess.run
        with mock.patch.object(r.subprocess, "run", side_effect=[OSError("retry"),
                actual_run([sys.executable, str(FAKE), "claude", "--help"], capture_output=True, text=True)]) as run:
            self.assertIn("--disallowedTools", r.command_output(["fake", "--help"], cache, lock))
            self.assertEqual(run.call_count, 2)
        with mock.patch.object(r.subprocess, "run", side_effect=AssertionError("cached help")):
            self.assertIn("--disallowedTools", r.command_output(["fake", "--help"], cache, lock))
        empty_cache = {}
        with mock.patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.assertEqual(r.command_output(["empty", "--help"], empty_cache, lock), "")
            self.assertEqual(empty_cache, {})
            self.assertEqual(run.call_count, 2)

    def test_unreadable_safety_help_refuses_claude_and_codex(self):
        for provider in ("claude", "codex"):
            for fields in ({}, {"mcp": True}):
                with self.subTest(provider=provider, fields=fields), self.fake_cli(), mock.patch.object(
                    r, "command_help", return_value=""
                ), self.assertRaisesRegex(r.PlanError, provider.capitalize() + ".*help"):
                    self.invocation(self.worker(provider, **fields), cap=0.5)
        with self.fake_cli(), mock.patch.object(r, "command_help", return_value="Usage: older CLI"), mock.patch.object(
            r, "command_output", return_value=""
        ):
            for provider in ("claude", "codex"):
                self.assertTrue(self.invocation(self.worker(provider)))
        with self.fake_cli(), mock.patch.object(r, "command_output", return_value=""), mock.patch.object(
            r, "command_help", return_value="--disable"
        ), self.assertRaisesRegex(r.PlanError, "Codex features list"):
            self.invocation(self.worker("codex"))

    def test_claude_uses_user_settings_and_safety_flags(self):
        with self.fake_cli():
            args = self.invocation(self.worker(), cap=0.5)
        for flag, value in (("--setting-sources", "user"), ("--disallowedTools", "Agent,Workflow"),
                            ("--max-budget-usd", "0.50"), ("--permission-prompts", "none")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--strict-mcp-config", args)

    def test_agy_plan_keeps_slash_expansion_but_write_disables_it(self):
        with self.fake_cli():
            self.assertNotIn("--disable-slash-commands", self.invocation(self.worker("agy")))
            self.assertIn("--disable-slash-commands", self.invocation(self.worker("agy", mode="write")))

    def test_claude_trailing_system_events_preserve_success_and_error_results(self):
        for error in (False, True):
            with self.subTest(error=error), self.fake_cli():
                args = self.invocation(self.worker(task="emit-error" if error else "read"))
                result = subprocess.run(args, input="emit-error" if error else "read", capture_output=True, text=True)
                stdout, final = self.root / "stdout.log", self.root / "result.md"
                stdout.write_text(result.stdout)
                final.unlink(missing_ok=True)
                status, text, values, message = r.classify_result("claude", "jsonl", stdout, final, 0, False)
                self.assertEqual(status, "failed" if error else "succeeded")
                self.assertEqual(values[-1]["type"], "system")
                self.assertEqual(text, "Finished fixture.")
                self.assertEqual(bool(message), error)
                if error:
                    self.assertIn("error_during_execution", message)
        self.assertEqual(r.result_text_from_values([{"type": "system", "text": "internal summary"}]), "")
        self.assertEqual(r.result_text_from_values([{"type": "rate_limit_event", "text": "quota"}]), "")

    def test_agy_denials_propagate_and_classify_permission_failure(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"version": 1, "workers": [self.worker("agy", task="emit-denied")]}))
        output = io.StringIO()
        with self.fake_cli(), contextlib.redirect_stdout(output):
            code = r.run_batch(plan, self.root / "run", self.policy, None)
        self.assertEqual(code, 1)
        worker = json.loads(output.getvalue())["workers"][0]
        self.assertEqual(worker["permissionDenials"], ["run_command"])
        self.assertEqual(worker["failureKind"], "permission_denied")
        self.assertIn("processStartTime", worker)
        self.assertEqual(r.extract_details([{"denied_actions": ["shell"]}])["permissionDenials"], ["shell"])
        stdout, result = self.root / "denied.log", self.root / "denied.md"
        stdout.write_text(json.dumps({"type": "result", "is_error": True, "result": "", "denied_actions": ["shell"]}))
        status, text, _, error = r.classify_result("agy", "json", stdout, result, 0, False)
        self.assertEqual(r.classify_failure(status, error, self.root / "missing-stderr", text, False)[0], "permission_denied")

    def test_environment_filters_secrets_and_keeps_provider_auth_and_user_allowlist(self):
        secrets = {"GITHUB_TOKEN": "github", "AWS_SECRET": "aws", "db_password": "db", "AWS_CREDENTIALS": "aws",
                   "SERVICE_KEY": "key", "ANTHROPIC_API_KEY": "anthropic", "CLAUDE_CODE_TOKEN": "claude",
                   "OPENAI_API_KEY": "openai", "CODEX_TOKEN": "codex", "GEMINI_API_KEY": "gemini",
                   "GOOGLE_TOKEN": "google", "MOONSHOT_API_KEY": "moonshot", "KIMI_TOKEN": "kimi"}
        allowed = {"claude": {"ANTHROPIC_API_KEY", "CLAUDE_CODE_TOKEN"}, "codex": {"OPENAI_API_KEY", "CODEX_TOKEN"},
                   "agy": {"GEMINI_API_KEY", "GOOGLE_TOKEN"}, "kimi": {"MOONSHOT_API_KEY", "KIMI_TOKEN"}, "custom": set()}
        with mock.patch.dict(os.environ, {**secrets, "CLAUDECODE": "1", "NORMAL_SETTING": "ok"}, clear=True):
            for provider, names in allowed.items():
                with self.subTest(provider=provider):
                    env = r.worker_environment(provider, {"workerEnv": ["GITHUB_TOKEN"]})
                    self.assertEqual(set(env) & set(secrets), names | {"GITHUB_TOKEN"})
                    self.assertEqual(env[r.WORKER_ENV_FLAG], "1")
                    self.assertNotIn("CLAUDECODE", env)
                    self.assertEqual(env["NORMAL_SETTING"], "ok")

    def test_task_files_confined_including_symlinks(self):
        plans = self.root / "plans"
        plans.mkdir()
        outside = self.root / "secret.md"
        outside.write_text("private")
        inside = plans / "task.md"
        inside.write_text("public")
        worker = self.worker(task=None, taskFile="task.md")
        worker.pop("task")
        self.assertIn("public", self.resolve(worker, directory=plans)["workers"][0]["task"])
        for name in ("../secret.md", str(outside)):
            with self.subTest(name=name), self.assertRaisesRegex(r.PlanError, "taskFile.*escapes"):
                self.resolve({**worker, "taskFile": name}, directory=plans)
        try:
            (plans / "link.md").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(r.PlanError, "taskFile.*escapes"):
            self.resolve({**worker, "taskFile": "link.md"}, directory=plans)
        orchestration = self.root / "orchestration"
        orchestration.mkdir()
        (orchestration / "brief.md").write_text("orchestration brief")
        self.assertIn("orchestration brief", self.resolve({**worker, "taskFile": str(orchestration / "brief.md")},
                                                        directory=plans, orchestration=orchestration)["workers"][0]["task"])

    def test_cwd_confined_to_invocation_checkout_or_trusted_root(self):
        with tempfile.TemporaryDirectory(prefix="outside-") as temporary:
            outside = Path(temporary).resolve()
            with self.assertRaisesRegex(r.PlanError, "trust.cwdRoots"):
                self.resolve(self.worker(cwd=str(outside)))
            self.assertEqual(self.resolve(self.worker(cwd=str(outside)), {"trust": {"cwdRoots": [str(outside)]}})["workers"][0]["cwd"], str(outside))
            try:
                (self.root / "outside").symlink_to(outside, target_is_directory=True)
            except OSError:
                return
            with self.assertRaisesRegex(r.PlanError, "trust.cwdRoots"):
                self.resolve(self.worker(cwd=str(self.root / "outside")))
        repo = self.root / "repo"
        repo.mkdir()
        r.run_git(repo, ["init", "-q"])
        (repo / "nested").mkdir()
        os.chdir(repo / "nested")
        self.assertEqual(self.resolve(self.worker(cwd=str(repo)))["workers"][0]["cwd"], str(repo))
        with self.assertRaisesRegex(r.PlanError, "trust.cwdRoots"):
            self.resolve(self.worker(cwd=str(self.root)))

    def test_stop_requires_matching_identity_and_skips_final_workers(self):
        identity = r.process_start_time(os.getpid())
        self.assertTrue(identity is None or isinstance(identity, str))
        if os.name != "nt" and not sys.platform.startswith("linux"):
            with mock.patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "Wed Nov  4 09:00:00 2026\n", "")):
                self.assertEqual(r.process_start_time(123), "Wed Nov  4 09:00:00 2026")
        with mock.patch.object(r, "pid_alive", return_value=True), mock.patch.object(r, "process_start_time", return_value="new"), mock.patch.object(r.os, "killpg", create=True) as kill:
            self.assertFalse(r.stop_recorded_pid(987654, "old"))
            self.assertFalse(r.stop_recorded_pid(987654))
            kill.assert_not_called()
            with mock.patch.object(r, "process_start_time", return_value="old"), mock.patch.object(r, "stop_windows_pid") as windows:
                self.assertTrue(r.stop_recorded_pid(987654, "old"))
                self.assertEqual((windows if os.name == "nt" else kill).call_count, 1)
        run = self.root / "run"
        r.atomic_write_json(run / "state.json", {"status": "running", "workers": {
            "final": {"status": "succeeded", "pid": 1}, "late": {"status": "running", "pid": 2},
            "active": {"status": "running", "pid": 3}, "old": {"status": "running", "pid": 4}}})
        r.atomic_write_json(run / "workers/late/outcome.json", {"status": "failed"})
        with mock.patch.object(r, "stop_recorded_pid", side_effect=[True, False]) as stop:
            payload = r.stop_payload(run)
        self.assertEqual(stop.call_args_list, [mock.call(3, None), mock.call(4, None)])
        self.assertEqual(payload["unverifiedPids"], [4])

    def test_process_identity_parses_linux_and_bsd_start_times_and_fails_closed(self):
        stat = "123 (worker (child)) " + " ".join(["S"] + ["0"] * 18 + ["4242"])
        with mock.patch.object(r.sys, "platform", "linux"), mock.patch.object(Path, "read_text", side_effect=[stat, "boot-id\n"]):
            self.assertEqual(r.process_start_time(123), "boot-id:4242")
        with mock.patch.object(r.sys, "platform", "linux"), mock.patch.object(Path, "read_text", return_value="truncated"):
            self.assertIsNone(r.process_start_time(123))
        with mock.patch.object(r.sys, "platform", "darwin"), mock.patch.object(r.os, "name", "posix"):
            with mock.patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "Wed Nov  4 09:00:00 2026\n", "")):
                self.assertEqual(r.process_start_time(123), "Wed Nov  4 09:00:00 2026")
            with mock.patch.object(r.subprocess, "run", side_effect=OSError("ps blocked")):
                self.assertIsNone(r.process_start_time(123))

    def test_worktree_destinations_reject_links_and_escapes_before_git(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.assertEqual(r.safe_destination(repo, repo / "new/deep"), repo / "new/deep")
        with self.assertRaisesRegex(r.PlanError, "escapes"):
            r.safe_destination(repo, self.root / "outside")
        (repo / "inside").mkdir()
        try:
            (repo / ".alethe").symlink_to(repo / "inside", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        with mock.patch.object(r, "run_git", return_value=str(repo).encode()) as git:
            with self.assertRaisesRegex(r.PlanError, "symlink or junction"):
                r.prepare_worktree(self.worker(cwd=str(repo)), "test")
            self.assertEqual(git.call_count, 1)
        target = repo / "target"
        target.mkdir()
        (target / "linked").symlink_to(repo / "inside", target_is_directory=True)
        with mock.patch.object(r, "run_git") as git, self.assertRaisesRegex(r.PlanError, "symlink or junction"):
            r.seed_worktree(repo, target, ["linked/new.txt"])
        git.assert_not_called()
        with mock.patch.object(r, "linked_path", side_effect=lambda path: path.name == "junction"), self.assertRaisesRegex(r.PlanError, "junction"):
            r.safe_destination(repo, repo / "junction/file")

    def test_internal_git_always_disables_hooks_fsmonitor_and_system_config(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_PARAMETERS": "unsafe", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "unsafe"}), mock.patch.object(
            r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ) as run:
            r.run_git(self.root, ["status"])
            r.git_returncode(self.root, ["status"])
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            args = call.args[0]
            self.assertIn("core.fsmonitor=false", args)
            hooks = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("core.hooksPath=")))
            self.assertTrue(hooks.is_dir())
            self.assertEqual(list(hooks.iterdir()), [])
            self.assertNotIn("GIT_CONFIG_NOSYSTEM", call.kwargs["env"])
            self.assertNotIn("GIT_CONFIG_PARAMETERS", call.kwargs["env"])
            self.assertNotIn("GIT_CONFIG_KEY_0", call.kwargs["env"])

    @unittest.skipUnless(os.name == "posix", "git executable hook fixture requires POSIX")
    def test_internal_git_never_executes_repository_hooks_or_fsmonitor(self):
        r.run_git(self.root, ["init", "-q"])
        hooks = self.root / "hooks"
        hooks.mkdir()
        marker = self.root / "unexpected-hook"
        for name in ("pre-commit", "post-checkout", "monitor"):
            script = hooks / name
            script.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 99\n')
            script.chmod(0o755)
        r.run_git(self.root, ["config", "core.hooksPath", str(hooks)])
        r.run_git(self.root, ["config", "core.fsmonitor", str(hooks / "monitor")])
        (self.root / "tracked").write_text("base")
        r.run_git(self.root, ["add", "tracked"])
        r.run_git(self.root, ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"])
        r.run_git(self.root, ["status", "--porcelain"])
        worktree = r.prepare_worktree({"cwd": str(self.root), "id": "safe", "seedPaths": []}, "test")
        self.assertTrue(worktree.is_dir())
        self.assertFalse(marker.exists())

    def test_clean_previews_and_deletes_only_old_finished_unreferenced_folders(self):
        folders = {}
        for name in ("old", "current", "other-session", "running", "worktree", "recent", "legacy", "registered"):
            folder = self.home / ("task-" + name)
            run = folder / "01-wave"
            worker = {"status": "succeeded", "worktree": None}
            if name in ("worktree", "legacy", "registered"):
                worker["worktree"] = str(self.root / (name + "-checkout"))
                if name == "worktree":
                    Path(worker["worktree"]).mkdir()
                if name == "registered":
                    worker["originCwd"] = str(self.root)
            r.atomic_write_json(folder / r.ORCHESTRATION_FILE, {"version": 1})
            r.atomic_write_json(run / "state.json", {"status": "running" if name == "running" else "done", "workers": {"one": worker}})
            folders[name] = folder
        r.set_current(folders["current"])
        with mock.patch.dict(os.environ, {"ALETHE_SESSION": "second"}):
            r.set_current(folders["other-session"])
        old_time = time.time() - 20 * 86400
        for folder in folders.values():
            for path in sorted(folder.rglob("*"), reverse=True) + [folder]:
                os.utime(path, (old_time, old_time))
        (folders["recent"] / "note").write_text("recent change")
        registered = "worktree " + str(self.root / "registered-checkout") + "\n"
        with mock.patch.object(r.tempfile, "gettempdir", return_value=str(self.root)), mock.patch.object(r, "run_git", return_value=registered.encode()):
            preview = r.clean_payload(14, False)
            self.assertEqual(preview["candidates"], [str(folders["old"])])
            self.assertTrue(folders["old"].exists())
            deleted = r.clean_payload(14, True)
        self.assertEqual(deleted["deleted"], [str(folders["old"])])
        for name, folder in folders.items():
            self.assertEqual(folder.exists(), name != "old", name)
        with self.assertRaises(r.PlanError):
            r.clean_payload(float("nan"), True)

    def test_clean_discovers_temp_runs_and_rechecks_current_pointers_before_deleting(self):
        run = self.root / "alethe-orchestrate-old"
        r.atomic_write_json(run / "state.json", {"status": "done", "workers": {}})
        old = time.time() - 20 * 86400
        os.utime(run / "state.json", (old, old))
        os.utime(run, (old, old))
        check = r.cleanup_refusal
        calls = []

        def switched(folder, cutoff, protected):
            result = check(folder, cutoff, protected)
            calls.append(folder)
            if len(calls) == 1:
                r.set_current(folder)
            return result

        with mock.patch.object(r.tempfile, "gettempdir", return_value=str(self.root)):
            self.assertEqual(r.clean_payload(14, False)["candidates"], [str(run)])
            with mock.patch.object(r, "cleanup_refusal", side_effect=switched):
                result = r.clean_payload(14, True)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["skipped"], [{"folder": str(run), "reason": "current orchestration"}])
        self.assertTrue(run.exists())

    def test_clean_keeps_unverified_waves_and_refuses_corrupt_current_pointer(self):
        folder = self.home / "unverified"
        (folder / "01-missing").mkdir(parents=True)
        r.atomic_write_json(folder / r.ORCHESTRATION_FILE, {"version": 1})
        self.assertEqual(r.cleanup_refusal(folder, time.time() + 10, []), "unverified wave")
        r.atomic_write_json(self.home / r.CURRENT_FILE, ["invalid pointer"])
        with self.assertRaisesRegex(r.PlanError, "repair or remove.*before clean"):
            r.clean_payload(14, True)
        self.assertTrue(folder.exists())


if __name__ == "__main__":
    unittest.main()
