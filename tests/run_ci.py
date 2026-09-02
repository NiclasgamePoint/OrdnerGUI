"""Run every test module in an isolated process with optional coverage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


DEFAULT_MODULE_TIMEOUT_SECONDS = 300.0
MODULE_TIMEOUT_EXIT_CODE = 124


def test_modules(test_root: Path) -> list[str]:
    project_root = test_root.parent
    return [
        ".".join(path.relative_to(project_root).with_suffix("").parts)
        for path in sorted(test_root.rglob("test_*.py"))
        if "base" not in path.relative_to(test_root).parts
    ]


def _module_environment(test_root: Path, project_root: Path | None = None) -> dict[str, str]:
    directories = {
        "data": test_root / "data",
        "source": test_root / "source",
        "config": test_root / "config",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    root = project_root or Path(__file__).resolve().parent.parent
    package_sources = [
        root / "packages" / "contracts" / "src",
        root / "packages" / "server" / "src",
        root / "packages" / "client" / "src",
    ]
    existing_pythonpath = environment.get("PYTHONPATH", "")
    pythonpath_parts = [str(path) for path in package_sources if path.is_dir()]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    environment.update(
        {
            "PAPAGUI_TEST_ROOT": str(test_root),
            "PAPAGUI_DATA_DIR": str(directories["data"]),
            "PAPAGUI_SOURCE_DIR": str(directories["source"]),
            "PAPAGUI_SETTINGS_DIR": str(directories["config"]),
            "XDG_CONFIG_HOME": str(directories["config"]),
            "APPDATA": str(directories["config"]),
            "LOCALAPPDATA": str(directories["config"]),
            "PYTHONPATH": os.pathsep.join(pythonpath_parts),
        }
    )
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    return environment


def run_module(
    module: str,
    project_root: Path,
    coverage: bool,
    timeout_seconds: float = DEFAULT_MODULE_TIMEOUT_SECONDS,
) -> int:
    command = [sys.executable]
    if coverage:
        command += ["-m", "coverage", "run", "--parallel-mode"]
    module_path = Path(*module.split(".")).with_suffix(".py")
    command += ["-m", "pytest", str(module_path), "-q"]

    safe_name = module.replace(".", "-")
    try:
        with TemporaryDirectory(prefix=f"papagui-{safe_name}-") as directory:
            test_root = Path(directory).resolve()
            environment = _module_environment(test_root, project_root)
            try:
                return subprocess.run(
                    command,
                    cwd=project_root,
                    check=False,
                    env=environment,
                    timeout=timeout_seconds,
                ).returncode
            except subprocess.TimeoutExpired:
                print(
                    f"TIMEOUT: Test module '{module}' exceeded "
                    f"{timeout_seconds:g} seconds and was terminated. "
                    "Its isolated test data will be removed.",
                    file=sys.stderr,
                    flush=True,
                )
                return MODULE_TIMEOUT_EXIT_CODE
    except OSError as error:
        print(
            f"ERROR: Test module '{module}' could not be executed in its "
            f"isolated environment: {error}",
            file=sys.stderr,
            flush=True,
        )
        return 1


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
    parser.add_argument(
        "--module-timeout",
        type=float,
        default=DEFAULT_MODULE_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "maximum runtime for each test module "
            f"(default: {DEFAULT_MODULE_TIMEOUT_SECONDS:g} seconds)"
        ),
    )
    args = parser.parse_args(argv)
    if args.module_timeout <= 0:
        parser.error("--module-timeout must be greater than zero")

    project_root = Path(__file__).resolve().parent.parent
    test_root = Path(__file__).parent
    failures: list[str] = []

    if args.coverage and coverage_command(project_root, "erase"):
        return 1

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for module in test_modules(test_root):
        print(f"\n=== {module} ===", flush=True)
        if run_module(
            module,
            project_root,
            args.coverage,
            args.module_timeout,
        ):
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
