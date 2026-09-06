"""Materialize the archived phase2 code + feedback fix + isolation overlay.

Creates a NEW directory outside this repository. Does not install dependencies,
modify an existing deployment, or launch a model/game/API request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FROZEN_COMMIT = "9e104b3df810b81925c414ebb1ebc7a0edade61e"


def verify_frozen() -> None:
    rows = json.loads((ROOT / "runtime/frozen-sha256.json").read_text(encoding="utf-8"))
    expected = {row["path"]: row["sha256"] for row in rows}
    actual = {"MANIFEST.csv"}
    for relative in ("code/frozen-phase1", "code/frozen-phase2", "code/post-experiment",
                     "experiments", "reports", "protocols", "background"):
        for source in (ROOT / relative).rglob("*"):
            if source.is_symlink():
                raise ValueError(f"Symlink in frozen evidence: {source.relative_to(ROOT)}")
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                actual.add(source.relative_to(ROOT).as_posix())
    if actual != set(expected):
        raise ValueError("Frozen evidence file inventory changed.")
    for relative, digest in expected.items():
        source = ROOT / relative
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Frozen evidence changed: {relative}")


def build(destination: Path) -> Path:
    verify_frozen()
    destination = destination.expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError("Runtime destination must be outside the archive repository.")
    if destination.exists():
        raise FileExistsError("Runtime destination must be a new directory.")
    if not destination.parent.is_dir():
        raise ValueError("Runtime destination parent must already exist.")
    with tempfile.TemporaryDirectory(prefix="duck-build-", dir=destination.parent) as temp:
        staged = Path(temp) / "runtime"
        shutil.copytree(ROOT / "code/frozen-phase2", staged,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        app = staged / "ARC3-Inference"
        agent = app / "inference/agent"
        shutil.copy2(ROOT / "code/post-experiment/ARC3-Inference/inference/agent/tool_agent.py", agent)
        for filename in ("python_tool_sandbox.py", "linux_isolation.py"):
            shutil.copy2(ROOT / "runtime" / filename, agent / filename)
        tests = app / "tests"
        tests.mkdir(exist_ok=True)
        shutil.copy2(ROOT / "code/post-experiment/ARC3-Inference/tests/test_prediction_io.py", tests)
        (staged / "RUNTIME_PROVENANCE.json").write_text(json.dumps({
            "frozen_commit": FROZEN_COMMIT,
            "base": "code/frozen-phase2", "feedback_fix": "code/post-experiment",
            "isolation_overlay": {name: hashlib.sha256((ROOT / "runtime" / name).read_bytes()).hexdigest()
                                  for name in ("python_tool_sandbox.py", "linux_isolation.py")},
            "game_performance": "Not re-evaluated; historical scores do not describe this runtime.",
        }, indent=2) + "\n", encoding="utf-8")
        staged.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--verify-frozen", action="store_true")
    args = parser.parse_args()
    if args.verify_frozen:
        verify_frozen()
        print("Frozen evidence: PASS")
    elif args.destination is not None:
        print(build(args.destination))
    else:
        parser.error("provide a destination or --verify-frozen")
