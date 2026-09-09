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
python -m pip install -r packages/client/requirements-lock.txt -r packaging/client/requirements-build-lock.txt -r requirements-test.txt
python -m pip install --no-deps -e packages/contracts -e packages/client
python -m PyInstaller --clean --noconfirm packaging/client/pyinstaller/papagui-client.spec
python -m PyInstaller --clean --noconfirm packaging/client/pyinstaller/papagui-tray.spec
python tools/check_frozen_client.py dist
```

Both specs include `papagui_client`, `papagui_contracts`, Qt and the required
client dependencies. They exclude the server, the historical root `app`
package and known index-writer modules. No source data, index or customer
database is packaged by these definitions.

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
manually and runs its own source tests before building. It is separate from
the Python wheel jobs in `quality.yml`; inspect the actual run results when
assessing a build. After each build, `tools/check_frozen_client.py`
recursively inspects the embedded PyInstaller module archive, rejects
server/legacy/index-writer modules, verifies both separate app bundles, and
executes `--help` for both products and `--sync-only --offline` for the client,
using a temporary client data/configuration directory. These checks do not
exercise an interactive Windows desktop, document converters or a live server.

The current specs do not bundle Poppler. See [the optional tool layout](vendor/poppler/README.md)
for the distinction between the prepared directory structure and implemented
binary packaging. Signing, installers, registry publishing and application
updates are not implemented by these build definitions.
