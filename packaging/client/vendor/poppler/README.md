# Bundled Poppler tools for client previews

This directory describes a possible future native Poppler bundle. The current
PyInstaller specs use empty explicit `binaries` and `datas` lists; they do not
copy this directory or acquire Poppler. Source checkouts and Python wheels do
not contain these executables.

```text
packaging/client/vendor/poppler/
  windows-x64/bin/pdftotext.exe, pdftoppm.exe, pdfinfo.exe, runtime DLLs
  macos-arm64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
  macos-x64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
  linux-x64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
```

`DocumentToolResolver` first checks `<bundle_root>/<platform>/bin/<tool>` and
then the operating-system `PATH`. Its default root is `vendor/poppler` beside
the client viewer source module, not this packaging directory. A future bundle
must be copied into that runtime location or passed explicitly to the resolver.

Before distributing such a bundle, add pinned acquisition, SHA-256 verification,
the Poppler copyright/license and notices for linked libraries. Those acquisition,
verification and signing steps are not implemented by the current specs.

The server image installs distribution-provided `poppler-utils` independently;
these client preview binaries are never copied into the server image.
