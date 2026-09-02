# Bundled Poppler tools for client previews

Native client artifacts may place a pinned Poppler command-line runtime below
this directory. Source checkouts and Python wheels do not contain binaries.

```text
packaging/client/vendor/poppler/
  windows-x64/bin/pdftotext.exe, pdftoppm.exe, pdfinfo.exe, runtime DLLs
  macos-arm64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
  macos-x64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
  linux-x64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
```

`DocumentToolResolver` first checks a platform-matching bundle explicitly
provided by the packaging process and then the operating-system `PATH`.
Every native artifact must verify its binary bundle by SHA-256 and include the
Poppler copyright/license plus notices for all linked libraries. Signing and
binary acquisition remain disabled for the 0.4.2 development checkpoint.

The server image installs distribution-provided `poppler-utils` independently;
these client preview binaries are never copied into the server image.
