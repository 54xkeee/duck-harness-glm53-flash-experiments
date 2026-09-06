"""Real OS tests. Missing Linux facilities fail the suite, never silently skip.

Only synthetic files and bounded allocations are used; no model/API requests.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_runtime", ROOT / "runtime/build_runtime.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="duck-isolation-tests-")
        cls.live = builder.build(Path(cls.temp.name) / "live")
        sys.path.insert(0, str(cls.live / "ARC3-Inference"))
        from inference.agent import python_tool_sandbox, linux_isolation
        cls.runner = staticmethod(python_tool_sandbox.run_sandboxed_python)
        cls.linux = linux_isolation
        probe = cls.runner(code="result = 1 + 1", timeout_seconds=5,
                           initial_state={}, action_handler=lambda items: {})
        if probe.get("result") != 2 or probe.get("error"):
            raise RuntimeError(f"Real OS isolation is a required test prerequisite: {probe}")

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.live / "ARC3-Inference"))
        cls.temp.cleanup()

    def run_code(self, code, *, timeout=5, handler=None, state=None):
        return self.runner(code=code, timeout_seconds=timeout,
                           initial_state=state or {}, action_handler=handler or (lambda items: {}))

    def test_math_and_output(self):
        r = self.run_code("import math\nprint('hello')\nresult = math.sqrt(81)")
        self.assertEqual(r["result"], 9)
        self.assertEqual(r["stdout"], "hello\n")
        self.assertFalse(r["error"])

    def test_action_state_roundtrip(self):
        def action(items):
            self.assertEqual(items, [{"action": "RIGHT"}])
            return {"action_result": {"moved": True}, "state": {"valid_actions": ["LEFT"]}}
        r = self.run_code("action('RIGHT')\nresult = valid_actions", handler=action)
        self.assertEqual(r["result"], ["LEFT"])
        self.assertEqual(r["action_results"], [{"moved": True}])

    def test_host_file_read_and_write_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "host-only.txt"
            marker.write_text("SYNTHETIC_HOST_SENTINEL", encoding="utf-8")
            for mode in ("r", "w"):
                with self.subTest(mode=mode):
                    r = self.run_code(f'result = action.__globals__["builtins"].open({str(marker)!r}, {mode!r}).read()')
                    self.assertIn("FileNotFoundError", r["error"])
            self.assertEqual(marker.read_text(), "SYNTHETIC_HOST_SENTINEL")

    def test_environment_secret_not_inherited(self):
        with patch.dict(os.environ, {"AUDIT_SYNTHETIC_SECRET": "host-canary"}):
            r = self.run_code('result = action.__globals__["os"].environ.get("AUDIT_SYNTHETIC_SECRET")')
        self.assertIsNone(r["result"])
        self.assertFalse(r["error"])

    def test_host_pid_hidden(self):
        r = self.run_code(f'result = action.__globals__["builtins"].open("/proc/{os.getpid()}/environ", "rb").read()')
        self.assertIn("FileNotFoundError", r["error"])

    def test_inheritable_host_descriptor_closed(self):
        import fcntl
        with tempfile.TemporaryFile() as marker:
            marker.write(b"SYNTHETIC_FD_CANARY")
            marker.seek(0)
            fd = fcntl.fcntl(marker.fileno(), fcntl.F_DUPFD, 200)
            try:
                os.set_inheritable(fd, True)
                r = self.run_code(f'result = action.__globals__["builtins"].open("/proc/self/fd/{fd}", "rb").read()')
                self.assertIn("FileNotFoundError", r["error"])
            finally:
                os.close(fd)

    def test_internet_and_unix_sockets_blocked(self):
        for family in (1, 2, 10):
            with self.subTest(family=family):
                r = self.run_code(f's = action.__globals__["builtins"].__import__("socket")\nresult = s.socket({family}, 1)')
                self.assertIn("Operation not permitted", r["error"])

    def test_fork_blocked_by_kernel(self):
        r = self.run_code('result = action.__globals__["os"].fork()')
        self.assertIn("Operation not permitted", r["error"])

    def test_user_namespace_creation_blocked(self):
        r = self.run_code('c = action.__globals__["builtins"].__import__("ctypes")\n'
                          'lib = c.CDLL("libc.so.6", use_errno=True)\n'
                          'result = [lib.unshare(0x10000000), c.get_errno()]')
        self.assertEqual(r["result"], [-1, 1])

    def test_system_runtime_is_read_only(self):
        r = self.run_code('result = action.__globals__["builtins"].open("/usr/lib/python3.12/duck-audit-new-file", "w")')
        self.assertIn("Read-only file system", r["error"])

    def test_temp_directory_is_read_only(self):
        r = self.run_code('b = action.__globals__["builtins"]\n'
                          'with b.open("/tmp/bounded", "wb") as f:\n'
                          '    f.write(b"x" * (2 * 1024 * 1024))')
        self.assertIn("Read-only file system", r["error"])

    def test_anonymous_file_allocation_blocked(self):
        r = self.run_code('result = action.__globals__["os"].memfd_create("audit")')
        self.assertIn("Operation not permitted", r["error"])

    def test_shared_memory_allocation_blocked(self):
        r = self.run_code('c = action.__globals__["builtins"].__import__("ctypes")\n'
                          'lib = c.CDLL("libc.so.6", use_errno=True)\n'
                          'result = [lib.shmget(0, 4096, 0o600), c.get_errno()]')
        self.assertEqual(r["result"], [-1, 1])

    def test_memory_limit(self):
        r = self.run_code('result = bytearray(512 * 1024 * 1024)')
        self.assertIn("MemoryError", r["error"])

    def test_hard_limits_cannot_be_raised(self):
        r = self.run_code('r = action.__globals__["builtins"].__import__("resource")\n'
                          'r.setrlimit(r.RLIMIT_AS, (536870912, 536870912))')
        self.assertIn("not allowed", r["error"])

    def test_print_limit(self):
        r = self.run_code('print("x" * 300000)')
        self.assertIn("256 KiB", r["error"])

    def test_raw_output_limit(self):
        r = self.run_code('action.__globals__["os"].write(1, b"x" * 1200000)\nwhile True: pass')
        self.assertIn("message exceeds", r["error"])

    def test_stderr_flood_limit(self):
        r = self.run_code('action.__globals__["os"].write(2, b"x" * 200000)\nwhile True: pass')
        self.assertIn("stderr exceeds", r["error"])

    def test_invalid_json_does_not_block_stderr_read(self):
        start = time.monotonic()
        r = self.run_code('action.__globals__["os"].write(1, b"invalid-json\\n")\nwhile True: pass')
        self.assertIn("Invalid sandbox JSON", r["error"])
        self.assertLess(time.monotonic() - start, 4)

    def test_nonobject_protocol_frame(self):
        r = self.run_code('action.__globals__["os"].write(1, b"[]\\n")\nwhile True: pass')
        self.assertIn("must be an object", r["error"])

    def test_forged_action_results_are_ignored(self):
        r = self.run_code('action.__globals__["_send"]({"type":"final", "result":7, "action_results":[{"forged":True}]})')
        self.assertEqual(r["result"], 7)
        self.assertEqual(r["action_results"], [])

    def test_oversized_action_batch_rejected(self):
        seen = []
        r = self.run_code('action(["RIGHT"] * 33)', handler=lambda items: seen.append(items))
        self.assertIn("Invalid sandbox action batch", r["error"])
        self.assertEqual(seen, [])

    def test_action_budget(self):
        r = self.run_code('for i in range(129): action("RIGHT")')
        self.assertIn("action budget exceeded", r["error"])
        self.assertEqual(len(r["action_results"]), 128)

    def test_completed_host_action_retained_at_deadline(self):
        def slow_action(items):
            time.sleep(1.1)
            return {"action_result": {"moved": True}, "state": {}}
        r = self.run_code('action("RIGHT")', timeout=1, handler=slow_action)
        self.assertIn("deadline elapsed during host action", r["error"])
        self.assertEqual(r["action_results"], [{"moved": True}])

    def test_wall_timeout_and_child_reaped(self):
        children = []
        original = subprocess.Popen
        def record(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        start = time.monotonic()
        with patch.object(self.linux.subprocess, "Popen", side_effect=record):
            r = self.run_code('while True: pass', timeout=0.5)
        self.assertIn("timed out", r["error"])
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(children[0].pid, 0)

    def test_missing_bwrap_is_fail_closed(self):
        with patch.object(self.linux.shutil, "which", return_value=None), patch.object(self.linux.subprocess, "Popen") as spawn:
            r = self.run_code('result = 5')
        self.assertIn("bubblewrap is required", r["error"])
        spawn.assert_not_called()

    def test_windows_is_fail_closed(self):
        with patch.object(self.linux.platform, "system", return_value="Windows"), patch.object(self.linux.subprocess, "Popen") as spawn:
            r = self.run_code('result = 5')
        self.assertIn("no fallback", r["error"])
        spawn.assert_not_called()

    def test_missing_seccomp_is_fail_closed(self):
        with patch.object(self.linux.ctypes, "CDLL", side_effect=OSError("seccomp missing")), patch.object(self.linux.subprocess, "Popen") as spawn:
            r = self.run_code('result = 5')
        self.assertIn("seccomp missing", r["error"])
        spawn.assert_not_called()

    def test_broken_isolation_backend_never_runs_plain_python(self):
        with patch.dict(os.environ, {"DUCK_BWRAP": "/usr/bin/false"}):
            r = self.run_code('result = 5')
        self.assertTrue(r["error"])
        self.assertIsNone(r["result"])

    def test_input_budgets(self):
        for args in ({"code": "x" * 65537}, {"code": "result=1", "timeout": 121},
                     {"code": "result=1", "state": {"extra": "x" * (16 * 1024 * 1024)}}):
            with self.subTest(args=list(args)):
                with patch.object(self.linux.subprocess, "Popen") as spawn:
                    r = self.run_code(**args)
                self.assertTrue(r["error"])
                spawn.assert_not_called()

    def test_frozen_hashes_and_build_guards(self):
        builder.verify_frozen()
        with self.assertRaises(ValueError):
            builder.build(ROOT / "code/frozen-phase2/new-runtime")
        with self.assertRaises(FileExistsError):
            builder.build(self.live)
        provenance = json.loads((self.live / "RUNTIME_PROVENANCE.json").read_text())
        self.assertEqual(provenance["frozen_commit"], builder.FROZEN_COMMIT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
