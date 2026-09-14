# PapaGUI client packaging

These definitions prepare unsigned client artifacts only. They never publish a
release. Build each target in its native CI runner so Qt and platform libraries
are collected correctly.

Prepared build targets (see `build-matrix.json`):

- Windows x64
- macOS x64 and arm64
- Linux x64

From the repository root, use a fresh Python 3.11 environment on the target
operating system. Do not copy a Linux virtual environment to Windows. The
[development setup](../../docs/development.md) shows each platform's interpreter
path; `python` below means that environment's interpreter.

```text
python -m pip install -r packages/client/requirements-lock.txt -r packaging/client/requirements-build-lock.txt -r requirements-client-test.txt
python -m pip install --no-deps -e packages/contracts -e packages/client
python -m PyInstaller --clean --noconfirm packaging/client/pyinstaller/papagui-client.spec
python -m PyInstaller --clean --noconfirm packaging/client/pyinstaller/papagui-tray.spec
python tools/check_frozen_client.py dist
```

Both specs include `papagui_client`, `papagui_contracts`, Qt and the required
client dependencies. They exclude the server, the historical root `app`
package and known index-writer modules. No source data, index or customer
database is packaged by these definitions.

Application artwork lives in
`packages/client/src/papagui_client/resources/icons/` and is included in both
wheels and frozen products. Each Windows executable embeds its own multi-size
ICO, and each macOS bundle embeds its corresponding ICNS. Qt uses the packaged
ICO/PNG for windows, dialogs and trays, including source launches. Windows app
identities are `de.papagui.client` and `de.papagui.tray`. Asset provenance,
generation prompts and the export command are documented in
[Application icons](../../docs/development/app-icons.md).

The CI workflow installs the pinned GUI runtime from
`packages/client/requirements-lock.txt` and the pinned PyInstaller frontend
from `packaging/client/requirements-build-lock.txt`. Linux and Windows produce ordinary native
executables. On macOS each spec wraps its executable in a real `.app` bundle;
`PapaGUI Client.app` and `PapaGUI Tray.app` remain independent bundles placed
next to each other. The client locates the tray bundle relative to its own app
bundle and starts its executable in the background. Install or move both apps
together into the same directory; neither bundle contains the server or Docker.
The same sibling rule applies to `papagui-client.exe` and `papagui-tray.exe`
on Windows, and to the corresponding executable pair on Linux.

The four prepared, unsigned targets are recorded in `build-matrix.json` and are
validated by `tests/tools/test_client_packaging.py`. Building on one operating
system does not certify another target; PyInstaller builds run natively on the
corresponding CI runner. `.github/workflows/client-artifacts.yml` is triggered
manually or for pull requests touching packaging inputs, and runs its own source tests before building. It is separate from
the Python wheel jobs in `quality.yml`; inspect the actual run results when
assessing a build. After each build, `tools/check_frozen_client.py`
recursively inspects the embedded PyInstaller module archive, rejects
server/legacy/index-writer modules, verifies both separate app bundles, and
executes `--help` for both products and `--sync-only --offline` for the client,
using a temporary client data/configuration directory. These checks do not
exercise an interactive Windows desktop, document converters or a live server.

The current specs do not bundle Poppler. See [the optional tool layout](vendor/poppler/README.md)
for the distinction between the prepared directory structure and implemented
binary packaging. Signing, registry publishing and application updates are
not implemented by these build definitions.

## Native installers

After building and checking the frozen products, run:

```text
python tools/release_metadata.py
python tools/build_installers.py
```

On Windows, install Inno Setup 6 and pass `--iscc <path-to-ISCC.exe>` if needed.
Linux uses `dpkg-deb` on Ubuntu 24.04, macOS uses Apple's `pkgbuild`. The builder
reads versions from the package sources and produces `.exe`, `.pkg` or `.deb`
under `dist/installers/`, each with a SHA-256 sidecar. Existing output files are
not overwritten; choose a new `--output` directory for a repeat build.

Installers contain client and tray, project licenses, available dependency
notices and a platform-specific Python dependency inventory. This inventory is
not a complete native SBOM; see `THIRD_PARTY_NOTICES.md` for remaining release
obligations. The Windows installer includes the Inno Setup compiler's notice.

CI installs the package and launches the installed programs. Windows additionally
tests reinstall and uninstall with `tools/check_installer.py`; that script refuses
to run over a registered existing PapaGUI installation. Mac/Linux end-user upgrade,
uninstall and interactive GUI acceptance still need native manual verification.

See [installation](../../docs/installation.md) for target limitations and
[release checklist](../../docs/releasing.md) for public distribution gates.
