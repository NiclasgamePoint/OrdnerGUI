from __future__ import annotations

import hashlib
import importlib
import io
import json
from pathlib import Path
import tarfile

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    return importlib.import_module("prepare_upstream_sources"), importlib.import_module("release_source_assets")


def test_extracts_only_regular_notice_files_without_executing_paths(tmp_path, modules):
    preparer, _ = modules
    archive = tmp_path / "synthetic.tar.xz"
    with tarfile.open(archive, "w:xz") as stream:
        for name, content in [("src/LICENSE", b"Synthetic copyright"), ("src/main.py", b"never run")]:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
        link = tarfile.TarInfo("LICENSE-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        stream.addfile(link)
    entries = preparer.extract_notices(archive, tmp_path / "notices")
    assert len(entries) == 1
    assert (tmp_path / "notices" / entries[0]["file"]).read_bytes() == b"Synthetic copyright"
    assert entries[0]["source"] == "src/LICENSE"


def test_parent_traversal_in_notice_is_rejected(tmp_path, modules):
    preparer, _ = modules
    archive = tmp_path / "unsafe.tar.xz"
    with tarfile.open(archive, "w:xz") as stream:
        member = tarfile.TarInfo("../LICENSE")
        member.size = 1
        stream.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe"):
        preparer.extract_notices(archive, tmp_path / "notices")


@pytest.mark.parametrize("problem", [None, "missing", "changed", "duplicate"])
def test_publication_requires_exact_pinned_sources(tmp_path, modules, monkeypatch, problem):
    _, publisher = modules
    from papagui_client.updates.feed import UpdateError

    archive = tmp_path / "upstream.tar.xz"
    archive.write_bytes(b"synthetic source")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"archives": [{"name": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}]}))
    monkeypatch.setattr(publisher, "SOURCE_MANIFEST", manifest)
    if problem == "missing":
        archive.unlink()
    elif problem == "changed":
        archive.write_bytes(b"wrong source")
    elif problem == "duplicate":
        (tmp_path / "duplicate").mkdir()
        (tmp_path / "duplicate" / archive.name).write_bytes(archive.read_bytes())
    if problem:
        with pytest.raises(UpdateError):
            publisher.source_assets(tmp_path)
    else:
        assert publisher.source_assets(tmp_path) == [archive]


def test_server_sources_require_both_architectures_and_valid_checksums(tmp_path, modules):
    _, publisher = modules
    from papagui_client.updates.feed import UpdateError

    for architecture in ("amd64", "arm64"):
        archive = tmp_path / f"papagui-server-sources-{architecture}.tar.gz"
        archive.write_bytes(b"synthetic source archive")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    assert len(publisher.server_source_assets(tmp_path)) == 4
    archive.write_bytes(b"corrupt archive")
    with pytest.raises(UpdateError, match="checksum"):
        publisher.server_source_assets(tmp_path)
    archive.unlink()
    with pytest.raises(UpdateError, match="Missing"):
        publisher.server_source_assets(tmp_path)


def test_native_inventory_preserves_optional_package_notices_without_host_paths(tmp_path, modules, monkeypatch):
    from types import SimpleNamespace

    module = importlib.import_module("collect_native_inventory")
    binary = tmp_path / "optional.pyd"
    binary.write_bytes(b"synthetic native library")
    notice = tmp_path / "LICENCE.txt"
    notice.write_text("Synthetic copyright")
    dist = SimpleNamespace(files=[Path("optional.pyd"), Path("LICENCE.txt")],
                           metadata={"Name": "optional-package"}, version="1.0",
                           locate_file=lambda file: tmp_path / file,
                           read_text=lambda _: "Name: optional-package\n")
    monkeypatch.setattr(module.metadata, "distributions", lambda: [dist])
    analysis = tmp_path / "product/Analysis-00.toc"
    analysis.parent.mkdir()
    analysis.write_text(repr([("package/optional.pyd", str(binary), "EXTENSION")]))
    output = tmp_path / "out"
    module.collect([analysis], output)
    inventory = json.loads((output / "native-inventory.json").read_text())
    assert inventory["binaries"][0]["owner"] == "optional-package==1.0"
    assert str(tmp_path) not in (output / "native-inventory.json").read_text()
    assert any(path.read_text() == "Synthetic copyright" for path in (output / "python-native/optional-package").iterdir())
