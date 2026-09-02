# PapaGUI client packaging

These definitions prepare unsigned client artifacts only. They never publish a
release. Build each target in its native CI runner so Qt and platform libraries
are collected correctly.

Supported targets:

- Windows x64
- macOS x64 and arm64
- Linux x64

Install `packages/client/requirements-lock.txt` and
`requirements-build-lock.txt`, then install `packages/contracts` and
`packages/client` with `--no-deps`. Invoke PyInstaller with one of the specs in
`pyinstaller/`. Both specs bundle only `papagui_client` and
`papagui_contracts`; the historical root `app` package is intentionally
excluded together with known index-writer module names.

The CI workflow installs the pinned GUI runtime from
`packages/client/requirements-lock.txt` and the pinned PyInstaller frontend
from `requirements-build-lock.txt`. Linux and Windows produce ordinary native
executables. On macOS each spec wraps its executable in a real `.app` bundle;
`PapaGUI Client.app` and `PapaGUI Tray.app` remain independent bundles placed
next to each other. The client locates the tray bundle relative to its own app
bundle and starts its executable in the background. Install or move both apps
together into the same directory; neither bundle contains the server or Docker.

The four prepared, unsigned targets are recorded in `build-matrix.json` and are
validated by `tests/tools/test_client_packaging.py`. Building on one operating
system does not certify another target; PyInstaller builds run natively on the
corresponding CI runner. After each build, `tools/check_frozen_client.py`
recursively inspects the embedded PyInstaller module archive, rejects
server/legacy/index-writer modules, verifies both separate app bundles, and
executes client and tray smoke commands.
