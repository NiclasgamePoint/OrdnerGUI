from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
import unittest

from tests.contracts import CONTRACTS_SOURCE


PACKAGE_ROOT = CONTRACTS_SOURCE / "papagui_contracts"
PROJECT_FILE = CONTRACTS_SOURCE.parent / "pyproject.toml"
ALLOWED_STDLIB_ROOTS = {
    "__future__",
    "dataclasses",
    "enum",
    "json",
    "math",
    "pathlib",
    "re",
    "typing",
    "uuid",
}


class ContractPackageBoundaryTests(unittest.TestCase):
    def test_package_declares_no_runtime_dependencies(self):
        metadata = tomllib.loads(PROJECT_FILE.read_text(encoding="utf-8"))
        project = metadata["project"]

        self.assertEqual(project["name"], "papagui-contracts")
        self.assertEqual(project["dynamic"], ["version"])
        self.assertEqual(
            metadata["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "papagui_contracts.__version__",
        )
        self.assertNotIn("dependencies", project)

    def test_contract_modules_only_import_stdlib_and_relative_contract_modules(self):
        violations: list[str] = []
        for path in sorted(PACKAGE_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.partition(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = [(node.module or "").partition(".")[0]]
                else:
                    continue
                for root in roots:
                    if root not in ALLOWED_STDLIB_ROOTS:
                        violations.append(f"{path.name}:{node.lineno}: {root}")

        self.assertEqual(violations, [])

    def test_import_does_not_load_forbidden_framework_or_io_modules(self):
        script = f"""
import sys
sys.path.insert(0, {str(CONTRACTS_SOURCE)!r})
import papagui_contracts
forbidden = ('PySide6', 'sqlite3', 'requests', 'httpx', 'fastapi')
# Python 3.11 pathlib imports urllib.parse, a pure string parser. Reject the
# network client rather than misclassifying that stdlib dependency as I/O.
loaded = sorted(name for name in sys.modules if name.split('.')[0] in forbidden
                or name == 'urllib.request' or name.startswith('urllib.request.'))
if loaded:
    raise SystemExit(','.join(loaded))
"""
        result = subprocess.run(
            # -S excludes editable-install .pth hooks from this dependency check.
            [sys.executable, "-I", "-S", "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
