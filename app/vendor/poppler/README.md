# Packaged Poppler tools

Platform release artifacts place the Poppler command-line runtime below this
directory so setuptools/freezers can include it as application package data:

```text
app/vendor/poppler/
  windows-x64/bin/pdftotext.exe, pdftoppm.exe, pdfinfo.exe, runtime DLLs
  macos-arm64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
  macos-x64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
  linux-x64/bin/pdftotext, pdftoppm, pdfinfo, runtime libraries
```

`IndexToolResolver` selects the matching directory before consulting `PATH`.
Binary artifacts are produced by the platform release pipeline rather than
committed to Git. Each artifact must use one pinned Poppler release, be verified
by SHA-256, and ship Poppler's copyright/license notices plus notices for all
linked libraries. A release check must fail when an executable or required
runtime library is absent.

Source checkouts remain functional without native artifacts: `pdftotext` falls
back to the isolated PyPDF2 extractor, while OCR is reported unavailable when
`pdftoppm` or Tesseract is missing.
