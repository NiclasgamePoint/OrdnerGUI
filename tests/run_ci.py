"""Run every test module in an isolated process with optional coverage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def test_modules(test_root: Path) -> list[str]:
    project_root = test_root.parent
    return [
        ".".join(path.relative_to(project_root).with_suffix("").parts)
        for path in sorted(test_root.rglob("test_*.py"))
        if "base" not in path.relative_to(test_root).parts
    ]


def run_module(module: str, project_root: Path, coverage: bool) -> int:
    command = [sys.executable]
    if coverage:
        command += ["-m", "coverage", "run", "--parallel-mode"]
    command += ["-m", "unittest", module, "-v"]
    return subprocess.run(command, cwd=project_root, check=False).returncode


def coverage_command(project_root: Path, *arguments: str) -> int:
    return subprocess.run(
        [sys.executable, "-m", "coverage", *arguments],
        cwd=project_root,
        check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="collect, combine and enforce project coverage",
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    test_root = Path(__file__).parent
    failures: list[str] = []

    if args.coverage and coverage_command(project_root, "erase"):
        return 1

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for module in test_modules(test_root):
        print(f"\n=== {module} ===", flush=True)
        if run_module(module, project_root, args.coverage):
            failures.append(module)

    if failures:
        print(f"\nFailed test modules: {', '.join(failures)}", file=sys.stderr)
        return 1

    if args.coverage:
        if coverage_command(project_root, "combine"):
            return 1
        return coverage_command(project_root, "report", "-m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
