"""Fail-closed Linux boundary for hostile Python, independent of Python name filters.

Supported runtime: Ubuntu 24.04 x86-64, /usr/bin/python3, bubblewrap >= 0.9,
libseccomp.so.2 and unprivileged namespaces. No host workspace is mounted.
The action callback is trusted host code and must implement its own deadline.
"""
from __future__ import annotations

import ctypes
import errno
import json
import math
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any, Callable

MEMORY_BYTES = 256 * 1024 * 1024
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_CODE_BYTES = 64 * 1024
MAX_ACTIONS = 128


class IsolationError(RuntimeError):
    """Missing isolation facilities or a exceeded protocol/resource budget."""


def _encode(value: Any) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_INPUT_BYTES:
        raise IsolationError("Sandbox input exceeds 16 MiB.")
    return data


def _seccomp_file():
    """Export native-architecture BPF; unsupported architectures fail closed."""
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW; other ABIs kill by default.
    if not ctx:
        raise IsolationError("Seccomp setup failed.")
    rules = (
        "clone", "clone3", "fork", "vfork", "unshare", "setns", "ptrace",
        "process_vm_readv", "process_vm_writev", "socket", "socketpair",
        "mount", "umount2", "pivot_root", "chroot", "open_by_handle_at",
        "name_to_handle_at", "bpf", "perf_event_open", "userfaultfd",
        "io_uring_setup", "io_uring_enter", "io_uring_register",
        "keyctl", "add_key", "request_key", "reboot", "kexec_load",
        "kexec_file_load", "init_module", "finit_module", "delete_module",
        "memfd_create", "shmget", "shmat", "shmdt", "shmctl", "semget", "semop",
        "semtimedop", "semctl", "msgget", "msgsnd", "msgrcv", "msgctl",
    )
    policy = tempfile.TemporaryFile()
    try:
        for name in rules:
            number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0 or lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0):
                raise IsolationError(f"Seccomp rule unavailable: {name}.")
        if lib.seccomp_export_bpf(ctx, policy.fileno()):
            raise IsolationError("Seccomp export failed.")
        policy.seek(0)
        return policy
    except BaseException:
        policy.close()
        raise
    finally:
        lib.seccomp_release(ctx)


def _command(bootstrap: str, timeout: float, policy_fd: int) -> list[str]:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise IsolationError("Sandbox requires Ubuntu 24.04 x86-64 Linux/WSL2; no fallback.")
    bwrap = shutil.which(os.environ.get("DUCK_BWRAP", "bwrap"))
    if not bwrap:
        raise IsolationError("bubblewrap is required; ordinary subprocess fallback is disabled.")
    python = Path("/usr/bin/python3").resolve()
    # Fixed distribution runtime, never the agent venv or a user-supplied rootfs.
    if python.name != "python3.12":
        raise IsolationError("This policy requires the Ubuntu Python 3.12 system runtime.")
    libraries = "/usr/lib/x86_64-linux-gnu"
    required = [python, Path("/usr/bin/prlimit"), Path("/usr/lib/python3.12"), Path(libraries)]
    if any(not p.exists() for p in required):
        raise IsolationError("Required system Python/runtime files are missing.")
    args = [
        bwrap, "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
        "--uid", "65534", "--gid", "65534", "--cap-drop", "ALL", "--clearenv",
        "--setenv", "PATH", "/usr/bin", "--setenv", "HOME", "/tmp",
        "--setenv", "TMPDIR", "/tmp", "--setenv", "PYTHONIOENCODING", "utf-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--ro-bind", str(python), "/usr/bin/python3",
        "--ro-bind", "/usr/bin/prlimit", "/usr/bin/prlimit",
        "--ro-bind", "/usr/lib/python3.12", "/usr/lib/python3.12",
        "--ro-bind", libraries, libraries,
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib/x86_64-linux-gnu", "/lib64",
        "--proc", "/proc", "--remount-ro", "/proc",
        "--dev", "/dev", "--remount-ro", "/dev",
        "--dir", "/tmp",
        "--remount-ro", "/", "--chdir", "/tmp",
        "--seccomp", str(policy_fd),
        "/usr/bin/prlimit", f"--as={MEMORY_BYTES}:{MEMORY_BYTES}",
        f"--cpu={math.ceil(timeout) + 1}:{math.ceil(timeout) + 1}",
        "--nproc=1:1", "--nofile=32:32", "--fsize=1048576:1048576", "--core=0:0",
        "--", "/usr/bin/python3", "-I", "-S", "-u", "-c", bootstrap,
    ]
    return args


def _stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


def run_isolated(
    *, bootstrap: str, code: str, timeout_seconds: float,
    initial_state: dict[str, Any], color_chars: str,
    action_handler: Callable[[list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    """Run one bounded interaction; process cleanup completes before returning.

    Model output, including forged protocol frames, is untrusted. Only the host
    callback's action results are returned as executed actions. The callback may
    act on the game, not on arbitrary host filesystem/process requests.
    """
    actions: list[dict[str, Any]] = []
    process = None
    selector = None
    policy = None
    try:
        if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_CODE_BYTES:
            raise IsolationError("Python code exceeds 64 KiB.")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120):
            raise IsolationError("Tool timeout must be between 0 and 120 seconds.")
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise IsolationError("Sandbox requires Ubuntu 24.04 x86-64 Linux/WSL2; no fallback.")
        outgoing = bytearray(_encode({"code": code, "timeout_seconds": timeout_seconds,
            "sandbox_cwd": "/tmp", "state": initial_state, "color_chars": color_chars}))
        policy = _seccomp_file()
        command = _command(bootstrap, timeout_seconds, policy.fileno())
        deadline = time.monotonic() + timeout_seconds
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            start_new_session=True, pass_fds=(policy.fileno(),), close_fds=True)
        selector = selectors.DefaultSelector()
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "input")
        selector.register(process.stdout, selectors.EVENT_READ, "output")
        selector.register(process.stderr, selectors.EVENT_READ, "error")
        pending = bytearray()
        total = error_bytes = action_count = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IsolationError(f"Tool timed out after {timeout_seconds}s.")
            if not selector.get_map():
                raise IsolationError("Isolated process exited without a valid result.")
            for key, _ in selector.select(remaining):
                if key.data == "input":
                    try:
                        written = os.write(key.fd, outgoing[:65536])
                    except BrokenPipeError as exc:
                        raise IsolationError("Isolation startup/input failed; no fallback.") from exc
                    del outgoing[:written]
                    if not outgoing:
                        selector.unregister(key.fileobj)
                    continue
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if key.data == "output" and pending:
                        raise IsolationError("Truncated sandbox protocol frame.")
                    continue
                if key.data == "error":
                    error_bytes += len(chunk)
                    if error_bytes > MAX_STDERR_BYTES:
                        raise IsolationError("Sandbox stderr exceeds 64 KiB.")
                    continue
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise IsolationError("Sandbox output exceeds 2 MiB.")
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if len(line) > MAX_MESSAGE_BYTES:
                        raise IsolationError("Sandbox message exceeds 1 MiB.")
                    try:
                        message = json.loads(line)
                    except (ValueError, RecursionError) as exc:
                        raise IsolationError("Invalid sandbox JSON frame.") from exc
                    if not isinstance(message, dict):
                        raise IsolationError("Sandbox frame must be an object.")
                    kind = message.get("type")
                    if kind in {"final", "error"}:
                        return {"stdout": str(message.get("stdout", "")),
                            "result": message.get("result"), "error": str(message.get("error", "")),
                            "action_results": list(actions)}
                    if kind != "action":
                        raise IsolationError("Unknown sandbox frame type.")
                    requested = message.get("actions")
                    if (not isinstance(requested, list) or not 1 <= len(requested) <= 32
                            or any(not isinstance(a, dict) or not isinstance(a.get("action"), str)
                                   for a in requested)):
                        raise IsolationError("Invalid sandbox action batch.")
                    action_count += len(requested)
                    if action_count > MAX_ACTIONS:
                        raise IsolationError("Sandbox action budget exceeded.")
                    if outgoing:
                        raise IsolationError("Sandbox sent an action before consuming its state.")
                    # This synchronous, trusted callback owns game-side deadlines.
                    try:
                        reply = action_handler(requested)
                    except Exception:
                        reply = None
                    if reply is None:
                        outgoing.extend(_encode({"type": "action_error", "error": "Host action failed."}))
                    else:
                        action_result = reply.get("action_result") or {}
                        if not isinstance(action_result, dict):
                            raise IsolationError("Host action result must be an object.")
                        actions.append(action_result)
                        outgoing.extend(_encode({"type": "action_result", "action_result": action_result,
                                                "state": reply.get("state") or {}}))
                    if time.monotonic() >= deadline:
                        raise IsolationError("Tool deadline elapsed during host action.")
                    selector.register(process.stdin, selectors.EVENT_WRITE, "input")
                if len(pending) > MAX_MESSAGE_BYTES:
                    raise IsolationError("Sandbox message exceeds 1 MiB.")
    except (IsolationError, OSError, ValueError, RecursionError) as exc:
        return {"stdout": "", "result": None, "error": str(exc), "action_results": list(actions)}
    finally:
        if selector is not None:
            selector.close()
        try:
            if process is not None:
                try:
                    _stop(process)
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()
        finally:
            if policy is not None:
                policy.close()
