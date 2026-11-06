import contextlib
import importlib.util
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills/alethe/scripts/orchestrate.py"
FAKE = Path(__file__).with_name("fake_robust_cli.py")
spec = importlib.util.spec_from_file_location("robust_runner", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


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


class RobustnessTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="robust-"))
        self.addCleanup(self.cleanup)
        previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous_cwd)
        self.policy = self.root / "policy.json"
        patch = mock.patch.dict(os.environ, {"ALETHE_ORCHESTRATE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)

    def cleanup(self):
        def retry(fn, path, error):
            os.chmod(path, stat.S_IWRITE)
            for attempt in range(10):
                try:
                    fn(path)
                    return
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.02)
        self.assertEqual(self.root.resolve().parent, Path(tempfile.gettempdir()).resolve())
        shutil.rmtree(self.root, onerror=retry)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = r.main(list(map(str, args)))
        return code, out.getvalue(), err.getvalue()

    def worker(self, **extra):
        return {"id": "one", "provider": "custom", "command": sys.executable,
                "args": [str(FAKE), "say"], "outputFormat": "text", "mode": "write",
                "cwd": str(self.root), "task": "Complete fixture.", **extra}

    def plan(self, worker=None, **extra):
        path = self.root / "plan.json"
        path.write_text(json.dumps({"version": 1, "workers": [worker or self.worker()], **extra}), encoding="utf-8")
        return path

    def run_plan(self, worker=None, name="run", **extra):
        run = self.root / name
        code, out, err = self.call("run", self.plan(worker, **extra), "--run-dir", run, "--policy", self.policy)
        return code, r.read_json(run / "state.json"), run

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout

    def repo(self):
        repo = self.root / "repo"
        (repo / "folder").mkdir(parents=True)
        (repo / "folder/tracked.txt").write_text("base\n", encoding="utf-8")
        self.git(repo, "init", "-q")
        self.git(repo, "add", ".")
        self.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial")
        return repo

    def until(self, condition, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        self.fail("fixture did not reach expected state")

    def test_seed_paths_reject_globs_and_git_magic(self):
        for path in ("*", "folder/?", "folder/[a]", ":(top)folder", "folder/a:b"):
            with self.subTest(path=path), self.assertRaises(r.PlanError):
                r.validate_relative_path(path, "seedPaths")
        self.assertEqual(r.validate_relative_path("folder/file", "seedPaths"), "folder/file")

    def test_seed_directory_copies_tracked_and_untracked_files_selectively(self):
        repo = self.repo()
        (repo / "folder/tracked.txt").write_text("dirty\n", encoding="utf-8")
        (repo / "folder/new.txt").write_text("new\n", encoding="utf-8")
        (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
        target = r.prepare_worktree(self.worker(cwd=str(repo), seedPaths=["folder"]), "seed")
        self.assertEqual((target / "folder/tracked.txt").read_text(), "dirty\n")
        self.assertEqual((target / "folder/new.txt").read_text(), "new\n")
        self.assertFalse((target / "outside.txt").exists())

    def test_failed_seeding_keeps_worktree_in_report(self):
        repo = self.repo()
        original = r.run_git
        def git(cwd, args, *pos, **kw):
            if args[0] == "diff" and "--" in args:
                raise r.PlanError("fixture seed failure")
            return original(cwd, args, *pos, **kw)
        with mock.patch.object(r, "run_git", side_effect=git):
            code, state, run = self.run_plan(self.worker(cwd=str(repo), mode="write", isolate=True, seedPaths=["folder"]))
        self.assertEqual(code, 1)
        worktree = state["workers"]["one"]["worktree"]
        self.assertTrue(Path(worktree).is_dir())
        self.assertEqual(r.collect_run(run)["workers"][0]["worktree"], worktree)

    def test_atomic_replace_retries_and_propagates_exhaustion(self):
        path = self.root / "atomic.txt"
        with mock.patch.object(r.os, "replace", side_effect=[PermissionError("busy"), None]) as replace:
            r.atomic_write_text(path, "value")
            self.assertEqual(replace.call_count, 2)
        with mock.patch.object(r.os, "replace", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                r.atomic_write_text(path, "value")
        r.atomic_write_text(path, "readable")
        self.assertEqual(path.read_text(), "readable")

    def test_periodic_refresh_failure_preserves_healthy_worker(self):
        original = r.BatchState.update_worker
        def update(state, worker_id, patch):
            if "outputBytes" in patch:
                raise PermissionError("busy")
            return original(state, worker_id, patch)
        with mock.patch.object(r, "STATE_REFRESH_SECONDS", 0), mock.patch.object(r, "POLL_SECONDS", 0.05), mock.patch.object(r.BatchState, "update_worker", new=update):
            code, _, _ = self.run_plan(self.worker(args=[str(FAKE), "quiet", "0.4"], outputFormat="json"))
        self.assertEqual(code, 0)

    def test_raw_git_timeouts_have_clear_errors(self):
        for invoke in (lambda: r.exclude_worktrees(self.root), lambda: r.in_git_worktree(self.root)):
            with mock.patch.object(r.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)) as call:
                with self.assertRaisesRegex(r.PlanError, "git.*timed out"):
                    invoke()
                self.assertGreater(call.call_args.kwargs["timeout"], 0)

    def test_hook_reads_utf8_independently_of_locale(self):
        project = self.root / "projeto-ação"
        project.mkdir()
        self.call("mode", "on", "--cwd", project)
        data = json.dumps({"cwd": str(project)}, ensure_ascii=False).encode("utf-8")
        with mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="cp1252")):
            self.assertIn("Alethe", self.call("hook")[1])

    def test_plain_run_dir_becomes_current(self):
        _, _, run = self.run_plan()
        self.assertEqual(r.current_folder().resolve(), run.resolve())
        folder = Path(json.loads(self.call("new", "fixture")[1])["folder"])
        self.call("run", self.plan(), "--run-dir", folder / "01-test", "--policy", self.policy)
        self.assertEqual(r.current_folder().resolve(), folder.resolve())

    def test_stop_after_admission_prevents_launch(self):
        run = self.root / "run"
        original = r.build_invocation
        def invocation(*args, **kw):
            result = original(*args, **kw)
            (run / r.STOP_FILE).write_text("stop", encoding="utf-8")
            return result
        with mock.patch.object(r, "build_invocation", side_effect=invocation), mock.patch.object(r, "start_process", side_effect=AssertionError("must not launch")) as start:
            _, state, _ = self.run_plan()
        start.assert_not_called()
        self.assertEqual(state["workers"]["one"]["failureKind"], "stopped")

    def test_report_counts_only_dirty_paths_in_worker_diff(self):
        repo = self.repo()
        (repo / "folder/tracked.txt").write_text("dirty\n", encoding="utf-8")
        (repo / "unrelated.txt").write_text("untracked\n", encoding="utf-8")
        _, state, run = self.run_plan(self.worker(cwd=str(repo), mode="write"))
        self.assertEqual(state["workers"]["one"]["dirtyBefore"], ["folder/tracked.txt"])
        self.assertIn("1 file in its diff already had", self.call("report", run)[1])

    def test_agy_idle_timeout_warns_and_is_ignored(self):
        worker = self.worker(provider="agy", idleTimeoutSeconds=5)
        for key in ("command", "args", "outputFormat"):
            worker.pop(key)
        plan = self.plan(worker)
        code, out, err = self.call("validate", plan, "--policy", self.policy)
        self.assertEqual(code, 0, err)
        self.assertTrue(any("idleTimeoutSeconds" in w for w in json.loads(out)["warnings"]))
        def invocation(worker, cwd, folder, *args):
            return [sys.executable, str(FAKE), "quiet", "5.3"], False, "json", folder / "result.md"
        with mock.patch.object(r, "build_invocation", side_effect=invocation):
            self.assertEqual(self.call("run", plan, "--run-dir", self.root / "agy", "--policy", self.policy)[0], 0)

    def test_cost_cap_requires_actual_cli_flag(self):
        worker = self.worker(provider="claude")
        for key in ("command", "args", "outputFormat"):
            worker.pop(key)
        for supported in (False, True):
            original = r.start_process
            def start(args, cwd, stdin, stdout, stderr, env):
                return original([sys.executable, str(FAKE), "say"], cwd, stdin, stdout, stderr, env)
            with mock.patch.object(r, "resolve_executable", return_value=sys.executable), mock.patch.object(r, "command_help", return_value="--max-budget-usd" if supported else "Usage: claude"), mock.patch.object(r, "start_process", side_effect=start):
                _, state, _ = self.run_plan(worker, name=str(supported), budget={"maxReportedCostUsd": 1})
            self.assertEqual(state["workers"]["one"]["costCapUsd"], 1 if supported else None)

    def test_detach_wait_and_incremental_usage(self):
        run = self.root / "detached"
        plan = self.plan(self.worker(args=[str(FAKE), "quiet", "2"], outputFormat="json"))
        started = time.monotonic()
        code, out, err = self.call("run", plan, "--run-dir", run, "--policy", self.policy, "--detach")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "started")
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(json.loads(self.call("wait", run, "--timeout", "0")[1])["done"])
        def launched():
            state = r.read_optional_json(run / "state.json") or {}
            return state if state.get("workers", {}).get("one", {}).get("pid") else None
        state = self.until(launched)
        self.assertEqual(state["runnerPid"], payload["pid"])
        self.assertEqual(state["budgetUsage"]["calls"], 1)
        self.assertEqual(r.orchestration_usage(self.root)["calls"], 1)
        waited = json.loads(self.call("wait", run, "--timeout", "10")[1])
        self.assertTrue(waited.pop("done"))
        status = json.loads(self.call("status", run, "--format", "json")[1])
        waited.pop("generatedAt")
        status.pop("generatedAt")
        self.assertEqual(waited, status)
        self.assertEqual(r.read_json(run / "state.json")["budgetUsage"]["reportedTokens"], 3)
        self.assertIn('"status": "done"', (run / "runner.stdout.log").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX signals require POSIX")
    def test_posix_signals_write_stop_before_stopping_workers(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            run = self.root / str(sig)
            plan = self.plan(self.worker(args=[str(FAKE), "quiet", "120"], outputFormat="json"))
            process = subprocess.Popen([sys.executable, str(SCRIPT), "run", str(plan), "--run-dir", str(run), "--policy", str(self.policy)], stdout=subprocess.DEVNULL)
            try:
                self.until(lambda: (r.read_optional_json(run / "state.json") or {}).get("workers", {}).get("one", {}).get("pid"))
                process.send_signal(sig)
                process.wait(timeout=15)
                self.assertTrue((run / r.STOP_FILE).exists())
                self.assertEqual(r.read_json(run / "state.json")["budgetUsage"]["calls"], 1)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require Windows")
    def test_windows_job_kills_children_even_after_root_exits(self):
        for exit_root in (False, True):
            pidfile = self.root / str(exit_root)
            process = r.start_process([sys.executable, str(FAKE), "tree", str(pidfile), *(["exit"] if exit_root else [])], self.root, subprocess.DEVNULL, subprocess.DEVNULL, subprocess.DEVNULL)
            try:
                self.until(pidfile.exists)
                pid = int(pidfile.read_text())
                if exit_root:
                    process.wait(timeout=10)
                r.stop_process_tree(process)
                self.until(lambda: not r.pid_alive(pid))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    @unittest.skipUnless(os.name == "nt", "Windows process lifetime requires Windows")
    def test_abrupt_runner_death_kills_entire_job_and_preserves_admitted_calls(self):
        run = self.root / "abrupt"
        pidfile = self.root / "descendant.pid"
        plan = self.plan(self.worker(args=[str(FAKE), "tree", str(pidfile)]))
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", str(plan),
                                    "--run-dir", str(run), "--policy", str(self.policy)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.until(pidfile.exists)
            self.until(lambda: (r.read_optional_json(run / "state.json") or {}).get("workers", {}).get("one", {}).get("pid"))
            pid = int(pidfile.read_text())
            process.kill()
            process.wait(timeout=10)
            self.until(lambda: not r.pid_alive(pid))
            self.assertEqual(r.collect_run(run)["status"], "interrupted")
            self.assertEqual(r.orchestration_usage(self.root)["calls"], 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    @unittest.skipUnless(os.name == "nt", "SIGBREAK requires Windows")
    def test_windows_sigbreak_writes_stop_and_settles_workers(self):
        run = self.root / "sigbreak"
        plan = self.plan(self.worker(args=[str(FAKE), "quiet", "120"], outputFormat="json"))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "run", str(plan), "--run-dir", str(run), "--policy", str(self.policy)],
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            startupinfo=startup, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            pid = self.until(lambda: (r.read_optional_json(run / "state.json") or {}).get("workers", {}).get("one", {}).get("pid"))
            sent = subprocess.run([sys.executable, str(FAKE), "break", str(process.pid)], capture_output=True, timeout=10)
            self.assertEqual(sent.returncode, 0, sent.stderr.decode("utf-8", errors="replace"))
            process.wait(timeout=15)
            self.assertTrue((run / r.STOP_FILE).exists())
            self.assertFalse(r.pid_alive(pid))
            self.assertEqual(r.read_json(run / "state.json")["status"], "interrupted")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    @unittest.skipUnless(os.name == "nt", "Windows sharing violations require Windows")
    def test_atomic_write_survives_a_real_windows_reader(self):
        import ctypes
        from ctypes import wintypes

        path = self.root / "held.json"
        path.write_text("old", encoding="utf-8")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        closer = threading.Timer(0.06, kernel.CloseHandle, args=[handle])
        closer.start()
        try:
            r.atomic_write_text(path, "new")
        finally:
            closer.join()
        self.assertEqual(path.read_text(), "new")

    def test_registry_cleans_a_process_registered_after_stop_all(self):
        registry = r.ProcessRegistry()
        registry.stop_all()
        process = r.start_process([sys.executable, str(FAKE), "quiet", "120"], self.root,
                                  subprocess.DEVNULL, subprocess.DEVNULL, subprocess.DEVNULL)
        try:
            registry.add(process)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                r.stop_process_tree(process)

    def test_wait_rejects_out_of_range_timeout(self):
        for timeout in (-1, 541):
            code, _, err = self.call("wait", self.root, "--timeout", timeout)
            self.assertEqual(code, 2)
            self.assertIn("--timeout must be between", err)

    def test_detached_cli_survives_launcher_exit(self):
        run = self.root / "launcher"
        plan = self.plan(self.worker(args=[str(FAKE), "quiet", "1"], outputFormat="json"))
        launched = subprocess.run(
            [sys.executable, str(SCRIPT), "run", str(plan), "--run-dir", str(run),
             "--policy", str(self.policy), "--detach"],
            capture_output=True, timeout=10,
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)
        payload = json.loads(launched.stdout)
        self.assertEqual(payload["status"], "started")
        waited = subprocess.run(
            [sys.executable, str(SCRIPT), "wait", str(run), "--timeout", "10"],
            capture_output=True, timeout=15,
        )
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertTrue(json.loads(waited.stdout)["done"])
        self.assertEqual(r.read_json(run / "state.json")["status"], "done")

    @unittest.skipUnless(os.name == "nt", "Windows native fallback requires Windows")
    def test_recorded_pid_termination_survives_taskkill_timeout(self):
        child = subprocess.Popen([sys.executable, str(FAKE), "quiet", "120"])
        identity = r.process_start_time(child.pid)
        try:
            with mock.patch.object(r.subprocess, "run", side_effect=subprocess.TimeoutExpired("taskkill", 30)):
                r.stop_recorded_pid(child.pid, identity)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def test_detach_rejects_invalid_plan_without_starting_worker(self):
        plan = self.root / "bad.json"
        plan.write_text("{}", encoding="utf-8")
        with mock.patch.object(r, "checkout_root", return_value=None), mock.patch.object(r.subprocess, "Popen") as start:
            code, _, _ = self.call("run", plan, "--detach", "--policy", self.policy)
        self.assertEqual(code, 2)
        start.assert_not_called()

    def test_dead_runner_is_interrupted_and_stop_kills_recorded_pid(self):
        pidfile = self.root / "child.pid"
        child = subprocess.Popen([sys.executable, str(FAKE), "child", str(pidfile)], start_new_session=os.name != "nt")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=10)
        try:
            run = self.root / "orphan"
            r.atomic_write_json(run / "state.json", {"status": "running", "runnerPid": dead.pid, "workers": {"one": {"status": "running", "pid": child.pid, "processStartTime": r.process_start_time(child.pid)}}})
            self.assertEqual(r.collect_run(run)["status"], "interrupted")
            self.assertIn("interrupted", self.call("status", run)[1])
            code, out, err = self.call("stop", run)
            self.assertEqual(code, 0, err + out)
            if r.process_start_time(child.pid) is None:
                self.assertEqual(json.loads(out)["unverifiedPids"], [child.pid])
                child.kill()
            child.wait(timeout=10)
            self.assertTrue((run / r.STOP_FILE).exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


if __name__ == "__main__":
    unittest.main()
