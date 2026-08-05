"""Run each unittest module in an isolated process for stable Qt teardown."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    test_modules = sorted(Path(__file__).parent.glob("test_*.py"))
    failures: list[str] = []

    for test_file in test_modules:
        module = f"tests.{test_file.stem}"
        print(f"\n=== {module} ===", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "unittest", module, "-v"],
            cwd=project_root,
            check=False,
        )
        if result.returncode != 0:
            failures.append(module)

    if failures:
        print(f"\nFailed test modules: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
