# Third-party notices

This file records data and native-tool notices that are not fully represented by
Python package metadata. PapaGUI itself has no final distribution license yet;
the repository's 0.4.2 workflows produce unsigned development artifacts.

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
[`packaging/client/vendor/poppler/README.md`](packaging/client/vendor/poppler/README.md).
Native Poppler binaries are not committed to this repository. A packaging run
that supplies them must include the exact upstream licenses and linked-library
notices belonging to the selected native builds.

Python dependencies retain their respective upstream licenses. A complete,
version-specific SBOM and license report is required before any public release.

## Local recognition libraries

The server additionally uses phonenumbers, email-validator, python-docx, openpyxl, xlrd, pdfplumber and Pillow. Exact runtime and transitive versions are pinned in `packages/server/requirements-lock.txt`; package metadata retains the upstream license notices. Tesseract and its German language data are installed by the server Dockerfile. Test-only reportlab and xlwt generate synthetic format fixtures and are not runtime server dependencies. No document model weights or external model service are bundled.
