# Copyright and third-party notices

PapaGUI original code and project-authored documentation are licensed under
**GPL-3.0-or-later**. Copyright (C) 2026 PapaGUI contributors. See `LICENSE`.
You may use, study, modify and redistribute the program, including commercially,
under that license. It comes without warranty. Third-party works retain their
own licenses; this notice does not relicense them.

Source repository: https://github.com/NiclasgamePoint/OrdnerGUI
Release source must identify the exact commit and include build scripts.

## Python and desktop dependencies

Exact versions are pinned in `packages/client/requirements-lock.txt` and
`packages/server/requirements-lock.txt`. The build command
`python tools/collect_licenses.py --component client --output <new-directory>`
collects installed package metadata, available license files and a JSON inventory
for the target platform. Installers include these notices. The inventory is
not a complete native-library SBOM and does not supply corresponding source.

| Work | Purpose | Upstream / license family |
| --- | --- | --- |
| CPython | Bundled interpreter | https://www.python.org/psf/license/ — PSF and included third-party notices |
| PySide6, Shiboken6, Qt | Desktop, tray, PDF preview | https://doc.qt.io/qtforpython-6/licenses.html — LGPL/GPL/commercial alternatives, module-specific terms |
| Qt PDF / PDFium | PDF rendering | https://doc.qt.io/qt-6/qtpdf-licensing.html — Qt terms plus PDFium/Chromium and embedded libraries |
| openpyxl, et_xmlfile | XLSX reading | https://openpyxl.readthedocs.io/ — MIT |
| xlrd | XLS reading | https://github.com/python-excel/xlrd — BSD |
| python-docx | DOCX reading | https://github.com/python-openxml/python-docx — MIT |
| lxml | XML parsing | https://lxml.de/ — BSD, with libxml2/libxslt notices |
| olefile | Legacy Office containers | https://github.com/decalage2/olefile — BSD |
| FastAPI, Starlette, Uvicorn, Pydantic | Server/API | Respective upstream metadata; predominantly MIT/BSD |
| phonenumbers | Telephone parsing | https://github.com/daviddrysdale/python-phonenumbers — Apache-2.0 |
| email-validator | Email validation | https://github.com/JoshData/python-email-validator — Unlicense |
| pdfplumber, pdfminer.six, pypdfium2 | Server PDF extraction | Respective upstream MIT/BSD/Apache terms and PDFium notices |
| Pillow | Image processing | https://python-pillow.github.io/ — MIT-CMU and embedded-library notices |

This table credits major components. The platform inventory includes transitive
Python packages. Do not infer every Qt module's license from the PySide6 umbrella
metadata: Qt PDF includes PDFium and additional notices. Audit the exact shipped
Qt 6.11.1 binaries, retain their license texts, and make corresponding sources
available as required by the selected open-source licenses before public release.
Linking only to an upstream homepage is not a replacement for those obligations.

## First-name data

`packages/server/src/papagui_server/resources/vornamen.txt` is derived from
`vornamen-grouped-sorted.dat` by [fxnn/vornamen](https://github.com/fxnn/vornamen),
based on data published by Stadt Köln on 19 January 2018. The data is licensed
under [CC BY 3.0 DE](https://creativecommons.org/licenses/by/3.0/de/). PapaGUI's
derived file removes frequency values but retains names and ordering. The notice
is shipped alongside the resource as `VORNAMEN-LIZENZ.md`.

## Poppler and document tools

The Docker server installs `poppler-utils`, `antiword` and `catdoc` from its base
distribution. Native client packaging may later bundle Poppler for document
previews as described in
`packaging/client/vendor/poppler/README.md` in the source repository.
Native Poppler binaries are not committed to this repository. A packaging run
that supplies them must include the exact upstream licenses and linked-library
notices belonging to the selected native builds.

The Debian image retains upstream notices under `/usr/share/doc/`. Before
publishing, record the image digest and Debian package versions and provide
corresponding source where required. Optional LibreOffice/Microsoft Office
preview integrations use an existing installation; neither suite is redistributed.

## Local recognition libraries

The server additionally uses phonenumbers, email-validator, python-docx, openpyxl, xlrd, pdfplumber and Pillow. Exact runtime and transitive versions are pinned in `packages/server/requirements-lock.txt`; package metadata retains the upstream license notices. Tesseract and its German language data are installed by the server Dockerfile. Test-only reportlab and xlwt generate synthetic format fixtures and are not runtime server dependencies. No document model weights or external model service are bundled.

## Build tools and artwork

PyInstaller includes a bootloader with its own license exception:
https://pyinstaller.org/en/stable/license.html. Windows installers use Inno Setup:
https://jrsoftware.org/files/is/license.txt. Retain notices for shipped components.
pytest, coverage, Ruff and Graphify are development tools, not runtime dependencies.

Application icons were generated/edited with Imagegen from a user-provided logo;
provenance is recorded in `docs/development/app-icons.md`. Confirm the right to
redistribute that original logo before publication. No rights to third-party
trademarks or supplied artwork are asserted here.

## Outstanding release checks

- Full platform-specific SBOM, including Python, Qt/PDFium and native libraries.
- Complete upstream license/copyright texts for every shipped native component.
- Corresponding source archives and build instructions for the exact binaries,
  available alongside downloads as required by GPL/LGPL licenses.
- Rights to supplied artwork and code contributed before this license was added.

Package-local copies of `LICENSE` and this file are generated from the root files
by `tools/release_metadata.py --sync` and checked for drift in CI.
