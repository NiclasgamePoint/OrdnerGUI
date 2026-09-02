from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"


def _python_files(package: str) -> list[Path]:
    return sorted((PACKAGES / package / "src").rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    return imported


def _assert_forbidden_imports(package: str, forbidden_roots: set[str]) -> None:
    violations: list[str] = []
    for path in _python_files(package):
        for imported in _imports(path):
            root = imported.split(".", 1)[0]
            if root in forbidden_roots:
                relative = path.relative_to(ROOT)
                violations.append(f"{relative}: {imported}")
    assert violations == [], "Verbotene Paketimporte:\n" + "\n".join(violations)


def _assert_forbidden_package_prefixes(
    package: str,
    source_subdirectory: str,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    source_root = PACKAGES / package / "src" / f"papagui_{package}" / source_subdirectory
    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                relative = path.relative_to(ROOT)
                violations.append(f"{relative}: {imported}")
    assert violations == [], "Verbotene Schichtimporte:\n" + "\n".join(violations)


def test_contracts_have_no_runtime_or_product_dependencies() -> None:
    _assert_forbidden_imports(
        "contracts",
        {
            "app",
            "fastapi",
            "httpx",
            "papagui_client",
            "papagui_server",
            "PySide6",
            "pydantic",
            "requests",
            "sqlite3",
            "uvicorn",
        },
    )


def test_server_never_imports_client_qt_or_legacy_app() -> None:
    _assert_forbidden_imports(
        "server",
        {"app", "papagui_client", "PyQt5", "PyQt6", "PySide2", "PySide6"},
    )


def test_client_never_imports_server_or_legacy_app() -> None:
    _assert_forbidden_imports("client", {"app", "papagui_server"})


def test_server_domain_and_application_do_not_depend_on_outer_layers() -> None:
    _assert_forbidden_package_prefixes(
        "server",
        "domain",
        (
            "papagui_server.adapters",
            "papagui_server.api",
            "papagui_server.application",
            "papagui_server.composition",
        ),
    )
    _assert_forbidden_package_prefixes(
        "server",
        "application",
        (
            "papagui_server.adapters",
            "papagui_server.api",
            "papagui_server.composition",
        ),
    )


def test_client_application_does_not_depend_on_outer_layers() -> None:
    _assert_forbidden_package_prefixes(
        "client",
        "application",
        (
            "papagui_client.adapters",
            "papagui_client.composition",
            "papagui_client.entrypoints",
            "papagui_client.gui",
            "papagui_client.presentation",
        ),
    )


def test_packages_have_independent_metadata_and_versions() -> None:
    expected_sources = {
        "contracts": "papagui_contracts.__version__",
        "server": "papagui_server.__version__",
        "client": "papagui_client.__version__",
    }
    for package, version_source in expected_sources.items():
        with (PACKAGES / package / "pyproject.toml").open("rb") as stream:
            metadata = tomllib.load(stream)
        assert metadata["project"]["name"] == f"papagui-{package}"
        assert metadata["project"]["dynamic"] == ["version"]
        assert metadata["tool"]["setuptools"]["dynamic"]["version"] == {
            "attr": version_source
        }


def test_server_container_does_not_copy_legacy_or_client_sources() -> None:
    dockerfile = ROOT / "deploy" / "server" / "Dockerfile"
    if not dockerfile.exists():
        return
    contents = dockerfile.read_text(encoding="utf-8")
    assert "COPY app" not in contents
    assert "packages/client" not in contents
    assert "PySide" not in contents


def test_production_sources_contain_no_legacy_compatibility_imports() -> None:
    production_files = {
        path.relative_to(ROOT).as_posix()
        for package in ("contracts", "server", "client")
        for path in _python_files(package)
    }
    assert not any("legacy_gui" in path for path in production_files)


def test_legacy_monolith_and_test_trees_are_removed() -> None:
    assert not (ROOT / "app").exists()
    for directory in (
        "base",
        "builders",
        "e2e",
        "fakes",
        "integration",
        "platform",
        "ui",
        "unit",
    ):
        assert not (ROOT / "tests" / directory).exists()
    for filename in (
        "requirements.txt",
        "requirements-lock.txt",
        "requirements-indexer-lock.txt",
    ):
        assert not (ROOT / filename).exists()
