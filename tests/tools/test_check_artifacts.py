from __future__ import annotations

import io
from pathlib import Path
import tarfile
import zipfile

import pytest

from tools.check_artifacts import ArtifactError, _load, artifact_paths, validate


def _wheel(
    root: Path,
    name: str,
    members: list[str],
    *,
    distribution: str | None = None,
    requirements: tuple[str, ...] = (),
) -> Path:
    path = root / name
    distribution = distribution or name.split("-", 1)[0]
    metadata_name = distribution.replace("-", "_")
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {distribution}",
        "Version: 0.4.2",
    ]
    metadata.extend(f"Requires-Dist: {requirement}" for requirement in requirements)
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            archive.writestr(member, "# test\n")
        archive.writestr(
            f"{metadata_name}-0.4.2.dist-info/METADATA",
            "\n".join(metadata) + "\n",
        )
    return path


def _sdist(
    root: Path,
    *,
    duplicate_requirement: str | None = None,
) -> Path:
    path = root / "papagui_server-0.4.2.tar.gz"
    base_metadata = (
        "Metadata-Version: 2.4\n"
        "Name: papagui-server\n"
        "Version: 0.4.2\n"
        "Requires-Dist: papagui-contracts==0.4.2\n"
    )
    duplicate_metadata = base_metadata
    if duplicate_requirement:
        duplicate_metadata += f"Requires-Dist: {duplicate_requirement}\n"
    members = {
        "papagui_server-0.4.2/PKG-INFO": base_metadata,
        "papagui_server-0.4.2/src/papagui_server/__init__.py": "# test\n",
        "papagui_server-0.4.2/src/papagui_server.egg-info/PKG-INFO": (
            duplicate_metadata
        ),
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, contents in members.items():
            encoded = contents.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(encoded)
            archive.addfile(info, io.BytesIO(encoded))
    return path


def test_accepts_each_isolated_distribution(tmp_path: Path) -> None:
    paths = [
        _wheel(
            tmp_path,
            "papagui_contracts-0.4.2-py3-none-any.whl",
            ["papagui_contracts/__init__.py"],
            distribution="papagui-contracts",
        ),
        _wheel(
            tmp_path,
            "papagui_server-0.4.2-py3-none-any.whl",
            ["papagui_server/__init__.py"],
            distribution="papagui-server",
            requirements=("papagui-contracts==0.4.2", "fastapi>=0.115"),
        ),
        _wheel(
            tmp_path,
            "papagui_client-0.4.2-py3-none-any.whl",
            ["papagui_client/__init__.py"],
            distribution="papagui-client",
            requirements=("papagui-contracts==0.4.2", "PySide6; extra == 'gui'"),
        ),
    ]

    for path in paths:
        validate(_load(path))


def test_accepts_consistent_duplicate_sdist_metadata(tmp_path: Path) -> None:
    validate(_load(_sdist(tmp_path)))


def test_rejects_conflicting_duplicate_sdist_metadata(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="widersprüchliche"):
        validate(_load(_sdist(tmp_path, duplicate_requirement="papagui-client==0.4.2")))


def test_rejects_server_code_in_client_wheel(tmp_path: Path) -> None:
    path = _wheel(
        tmp_path,
        "papagui_client-0.4.2-py3-none-any.whl",
        ["papagui_client/__init__.py", "papagui_server/indexing.py"],
        distribution="papagui-client",
        requirements=("papagui-contracts==0.4.2",),
    )

    with pytest.raises(ArtifactError, match="papagui_server"):
        validate(_load(path))


def test_rejects_index_writer_in_client_wheel(tmp_path: Path) -> None:
    path = _wheel(
        tmp_path,
        "papagui_client-0.4.2-py3-none-any.whl",
        ["papagui_client/__init__.py", "papagui_client/index_manager.py"],
        distribution="papagui-client",
        requirements=("papagui-contracts==0.4.2",),
    )

    with pytest.raises(ArtifactError, match="Index-Writer"):
        validate(_load(path))


def test_artifact_paths_ignore_missing_directories(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        "papagui_contracts-0.4.2-py3-none-any.whl",
        ["papagui_contracts/__init__.py"],
        distribution="papagui-contracts",
    )

    assert artifact_paths([tmp_path / "missing", tmp_path]) == [wheel]


@pytest.mark.parametrize(
    ("distribution", "package", "requirement"),
    [
        ("papagui-contracts", "papagui_contracts", "requests>=2"),
        ("papagui-server", "papagui_server", "PySide6>=6"),
        ("papagui-server", "papagui_server", "PySide6-Addons>=6"),
        ("papagui-server", "papagui_server", "papagui-client==0.4.2"),
        ("papagui-client", "papagui_client", "papagui-server==0.4.2"),
        ("papagui-client", "papagui_client", "app==0.4.1"),
    ],
)
def test_rejects_cross_product_metadata_dependencies(
    tmp_path: Path,
    distribution: str,
    package: str,
    requirement: str,
) -> None:
    baseline = () if distribution == "papagui-contracts" else ("papagui-contracts==0.4.2",)
    path = _wheel(
        tmp_path,
        f"{package}-0.4.2-py3-none-any.whl",
        [f"{package}/__init__.py"],
        distribution=distribution,
        requirements=(*baseline, requirement),
    )

    with pytest.raises(ArtifactError, match="Runtime-Abhängigkeiten|verbotene Abhängigkeiten"):
        validate(_load(path))


def test_rejects_missing_or_wrong_distribution_metadata(tmp_path: Path) -> None:
    missing = tmp_path / "papagui_contracts-0.4.2-py3-none-any.whl"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("papagui_contracts/__init__.py", "# test\n")
    with pytest.raises(ArtifactError, match="METADATA"):
        validate(_load(missing))

    wrong = _wheel(
        tmp_path,
        "papagui_contracts-0.4.2-wrong-py3-none-any.whl",
        ["papagui_contracts/__init__.py"],
        distribution="not-papagui-contracts",
    )
    with pytest.raises(ArtifactError, match="Paketnamen"):
        validate(_load(wrong))
