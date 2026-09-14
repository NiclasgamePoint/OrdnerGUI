"""Build an unsigned native installer from already validated frozen products."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from collect_licenses import collect
from release_metadata import ROOT, check, version


def run(*args: str | Path) -> None:
    subprocess.run([str(arg) for arg in args], check=True)


def build(dist: Path, output: Path, *, iscc: str = "ISCC.exe") -> Path:
    check()
    release = version("client")
    output.mkdir(parents=True, exist_ok=True)
    machine = platform.machine().lower()
    if machine not in {"amd64", "x86_64", "arm64", "aarch64"}:
        raise RuntimeError(f"Unsupported architecture: {machine}")
    if sys.platform != "darwin" and machine not in {"amd64", "x86_64"}:
        raise RuntimeError("Windows and Linux installers currently support x64 only")
    with TemporaryDirectory(prefix="papagui-installer-") as temporary:
        stage = Path(temporary)
        if sys.platform == "win32":
            for name in ("papagui-client.exe", "papagui-tray.exe"):
                shutil.copy2(dist / name, stage / name)
            collect("client", stage / "licenses")
            compiler = Path(shutil.which(iscc) or iscc).resolve()
            shutil.copy2(compiler.parent / "license.txt", stage / "licenses/INNO-SETUP-LICENSE.txt")
            artifact = output / f"papagui-client-{release}-windows-x64-setup-unsigned.exe"
            if artifact.exists():
                raise FileExistsError(artifact)
            run(iscc, f"/DAppVersion={release}", f"/DPayloadDir={stage}",
                f"/DOutputDir={output}", ROOT / "packaging/client/windows/papagui.iss")
        elif sys.platform == "darwin":
            payload = stage / "root/Applications/PapaGUI"
            payload.mkdir(parents=True)
            for name in ("PapaGUI Client.app", "PapaGUI Tray.app"):
                shutil.copytree(dist / name, payload / name, symlinks=True)
            collect("client", payload / "licenses")
            arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
            artifact = output / f"papagui-client-{release}-macos-{arch}-unsigned.pkg"
            if artifact.exists():
                raise FileExistsError(artifact)
            # Component metadata disables relocation so upgrades preserve the sibling apps.
            component_plist = stage / "components.plist"
            run("pkgbuild", "--analyze", "--root", stage / "root", component_plist)
            import plistlib

            entries = plistlib.loads(component_plist.read_bytes())
            for entry in entries:
                entry["BundleIsRelocatable"] = False
            component_plist.write_bytes(plistlib.dumps(entries))
            run("pkgbuild", "--root", stage / "root", "--component-plist", component_plist,
                "--identifier", "de.papagui.client", "--version", release,
                "--install-location", "/", artifact)
        elif sys.platform == "linux":
            root = stage / "root"
            payload = root / "opt/papagui"
            payload.mkdir(parents=True)
            for name in ("papagui-client", "papagui-tray"):
                shutil.copy2(dist / name, payload / name)
                (payload / name).chmod(0o755)
            collect("client", payload / "licenses")
            documentation = root / "usr/share/doc/papagui-client"
            documentation.mkdir(parents=True)
            shutil.copy2(ROOT / "LICENSE", documentation / "copyright")
            applications = root / "usr/share/applications"
            applications.mkdir(parents=True)
            (applications / "papagui.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=PapaGUI\n"
                "Comment=Document and customer catalog\nExec=/opt/papagui/papagui-client\n"
                "Icon=papagui\nTerminal=false\nCategories=Office;\n", encoding="utf-8")
            icons = root / "usr/share/icons/hicolor/256x256/apps"
            icons.mkdir(parents=True)
            shutil.copy2(ROOT / "packages/client/src/papagui_client/resources/icons/papagui-client.png",
                         icons / "papagui.png")
            control = root / "DEBIAN"
            control.mkdir()
            (control / "control").write_text(
                f"Package: papagui-client\nVersion: {release}\nArchitecture: amd64\n"
                "Maintainer: PapaGUI contributors\nSection: office\nPriority: optional\n"
                "Depends: libc6 (>= 2.39), libstdc++6, libgl1, libegl1, libglib2.0-0, "
                "libdbus-1-3, libfontconfig1, libx11-6, libx11-xcb1, libxcb1, "
                "libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, "
                "libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, "
                "libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0\n"
                "Homepage: https://github.com/NiclasgamePoint/OrdnerGUI\n"
                "Description: PapaGUI desktop client and index server control tray\n"
                " Offline-capable document and customer catalog. Requires a separate index server.\n",
                encoding="utf-8")
            # Windows bind mounts can report every source file as mode 0777.
            # Never propagate those permissions into a system-wide installation.
            for file in root.rglob("*"):
                if file.is_dir():
                    file.chmod(0o755)
                elif file.is_file():
                    executables = (payload / "papagui-client", payload / "papagui-tray")
                    file.chmod(0o755 if file in executables else 0o644)
            artifact = output / f"papagui-client-{release}-linux-x64.deb"
            if artifact.exists():
                raise FileExistsError(artifact)
            run("dpkg-deb", "--root-owner-group", "--build", root, artifact)
        else:
            raise RuntimeError(f"Unsupported platform: {sys.platform}")
    if not artifact.is_file():
        raise RuntimeError(f"Installer was not created: {artifact}")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(
        f"{digest}  {artifact.name}\n", encoding="ascii")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/installers")
    parser.add_argument("--iscc", default=os.environ.get("ISCC", "ISCC.exe"))
    args = parser.parse_args()
    print(build(args.dist.resolve(), args.output.resolve(), iscc=args.iscc))
