"""Run a fixed, non-model startup probe and expose infrastructure diagnostics."""
import json
import subprocess

from linux_isolation import IsolationError, _command, _seccomp_file


def main():
    try:
        with _seccomp_file() as policy:
            process = subprocess.run(
                _command('print("DUCK_ISOLATION_READY")', 5, policy.fileno()),
                pass_fds=(policy.fileno(),), capture_output=True, text=True, timeout=10,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
        ready = process.returncode == 0 and process.stdout.strip() == "DUCK_ISOLATION_READY"
        print(json.dumps({"ready": ready, "exit_code": process.returncode,
                          "startup_diagnostic": process.stderr[:4096]}, indent=2))
        return 0 if ready else 1
    except (IsolationError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ready": False, "startup_diagnostic": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
