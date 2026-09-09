"""Bounded, structured document extraction adapters for the headless indexer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, TemporaryFile
import time

from papagui_server.adapters.extraction_ocr import tsv_blocks
from papagui_server.domain.document_extraction import (
    PARSER_VERSION,
    ExtractionBlock,
    ExtractionPage,
    ExtractionResult,
    bounded_result,
)
from papagui_server.domain.models import ServerSettings

TEXT_EXTENSIONS = {"txt", "csv", "md", "log", "json", "xml", "yaml", "yml", "ini"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | {"pdf", "doc", "docx", "xls", "xlsx"}


@dataclass(frozen=True, slots=True)
class ExtractionResourcePolicy:
    pdf_timeout_seconds: int
    ocr_timeout_seconds: int
    ocr_pages: int
    render_dpi: int
    pause_seconds: float

    @classmethod
    def for_document(cls, path: Path, settings: ServerSettings) -> "ExtractionResourcePolicy":
        preferred = {
            value.strip().casefold()
            for value in settings.preferred_document_patterns.split(",")
            if value.strip()
        }
        extended = any(value in path.stem.casefold() for value in preferred)
        pages = settings.ocr_extended_max_pages if extended else settings.ocr_max_pages
        profile = {"gentle": (150, 0.05), "balanced": (200, 0.01), "fast": (250, 0.0)}[
            settings.resource_profile
        ]
        return cls(
            settings.pdf_text_timeout_seconds,
            settings.ocr_timeout_seconds,
            pages,
            profile[0],
            profile[1],
        )


class ExternalCommandRunner:
    """Run argv without a shell; retain sanitized failure categories only."""

    def __init__(self) -> None:
        self.last_status = "ok"

    def run(self, command: list[str], *, timeout: int) -> bytes:
        self.last_status = "ok"
        try:
            # Disk-backed stdout prevents a malformed tool from filling server RAM.
            # Tool stderr may contain customer paths/content and is never retained.
            with TemporaryFile() as output:
                result = subprocess.run(
                    command,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                    check=False,
                    env={
                        **os.environ,
                        "OMP_THREAD_LIMIT": "1",
                        "PYTHONPATH": os.pathsep.join(sys.path),
                    },
                )
                if result.returncode:
                    self.last_status = "error"
                    return b""
                output.seek(0)
                data = (
                    result.stdout
                    if isinstance(result.stdout, bytes)
                    else output.read(16 * 1024 * 1024 + 1)
                )
                if len(data) > 16 * 1024 * 1024:
                    self.last_status = "too_large"
                    return b""
                return data
        except subprocess.TimeoutExpired:
            self.last_status = "timeout"
            return b""
        except OSError:
            self.last_status = "tool_missing"
            return b""


def _ocr_language_version() -> tuple:
    candidates = [
        Path(value) / "deu.traineddata"
        for value in (
            os.environ.get("TESSDATA_PREFIX", ""),
            "/usr/share/tesseract-ocr/5/tessdata",
            "/usr/share/tesseract-ocr/4.00/tessdata",
            "/usr/share/tessdata",
            "/usr/local/share/tessdata",
        )
        if value
    ]
    result = []
    for path in candidates:
        try:
            stat = path.stat()
            result.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return tuple(result)


def extraction_fingerprint(settings: ServerSettings, extension: str | None = None) -> str:
    relevant = {
        key: value
        for key, value in settings.to_dict().items()
        if key.startswith(("ocr_", "pdf_", "image_", "extraction_"))
        or key
        in {
            "max_file_size_mb",
            "max_extracted_characters",
            "resource_profile",
            "preferred_document_patterns",
            "content_indexing_enabled",
            "content_extensions",
        }
    }
    # Policy for persistence/retries changes cache handling, not parsed content.
    for key in (
        "extraction_retry_attempts",
        "extraction_retry_delay_seconds",
        "extraction_store_max_mb",
        "extraction_retention_days",
    ):
        relevant.pop(key, None)
    packages = {"pdfplumber", "openpyxl", "xlrd", "Pillow"}
    programs = {"pdftotext", "pdftoppm", "tesseract", "antiword"}
    if extension is not None:
        keys = {
            "max_file_size_mb",
            "max_extracted_characters",
            "extraction_timeout_seconds",
            "extraction_memory_mb",
        }
        packages, programs = set(), set()
        if extension == "pdf" or extension in IMAGE_EXTENSIONS:
            keys.update(key for key in relevant if key.startswith("ocr_"))
            keys.update({"image_max_pixels", "resource_profile", "preferred_document_patterns"})
            packages.add("Pillow")
            programs.add("tesseract")
        if extension == "pdf":
            keys.update({"pdf_text_timeout_seconds", "pdf_max_pages"})
            packages.add("pdfplumber")
            programs.update({"pdftotext", "pdftoppm"})
        elif extension == "xlsx":
            packages.add("openpyxl")
        elif extension == "xls":
            packages.add("xlrd")
        elif extension == "doc":
            programs.add("antiword")
        relevant = {key: value for key, value in relevant.items() if key in keys}
    versions: dict[str, object] = {"parser": PARSER_VERSION}
    for package in sorted(packages):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "missing"
    for program in sorted(programs):
        path = shutil.which(program)
        try:
            stat = Path(path).stat() if path else None
            versions[program] = (stat.st_mtime_ns, stat.st_size) if stat else "missing"
        except OSError:
            versions[program] = "missing"
    if "tesseract" in programs:
        versions["tesseract_deu"] = _ocr_language_version()
    return hashlib.sha256(json.dumps([relevant, versions], sort_keys=True).encode()).hexdigest()


class DocumentTextExtractor:
    """Structured Office/PDF extraction with page-local optional Tesseract OCR."""

    def __init__(self, runner: ExternalCommandRunner | None = None) -> None:
        self._runner = runner or ExternalCommandRunner()
        self._worker_runner = ExternalCommandRunner()

    def fingerprint(self, settings: ServerSettings, extension: str | None = None) -> str:
        return extraction_fingerprint(settings, extension)

    def extract(
        self, path: Path, settings: ServerSettings, cancelled: Callable[[], bool] = lambda: False
    ) -> str:
        return self.extract_document(path, settings, cancelled).text

    def extract_document(
        self, path: Path, settings: ServerSettings, cancelled: Callable[[], bool] = lambda: False
    ) -> ExtractionResult:
        started = time.monotonic()
        deadline = started + getattr(settings, "extraction_timeout_seconds", 90)
        extension = path.suffix.casefold().lstrip(".")
        maximum = settings.max_extracted_characters
        try:
            if cancelled():
                result = ExtractionResult(status="partial", reason="cancelled")
            elif extension not in SUPPORTED_EXTENSIONS:
                result = ExtractionResult(status="unsupported", reason="unsupported_extension")
            elif path.exists() and path.stat().st_size > settings.max_file_size_mb * 1024 * 1024:
                result = ExtractionResult(status="too_large", reason="file_size_budget")
            elif extension in TEXT_EXTENSIONS:
                with path.open(encoding="utf-8", errors="replace") as stream:
                    text = stream.read(maximum + 1)
                result = bounded_result([ExtractionBlock(text)], maximum)
            elif extension in {"docx", "xlsx", "xls"}:
                result = self._worker("extraction_office_worker", path, settings, deadline)
            elif extension == "pdf":
                result = self._pdf(path, settings, cancelled, deadline)
            elif extension == "doc":
                value = self._runner.run(
                    ["antiword", str(path)], timeout=self._remaining(deadline, 45)
                )
                result = bounded_result(
                    [ExtractionBlock(value.decode("utf-8", errors="replace"), method="antiword")],
                    maximum,
                )
                if not value:
                    result = replace(
                        result,
                        status=getattr(self._runner, "last_status", "no_text"),
                        reason="legacy_reader_failed",
                    )
            else:
                result = self._image(path, settings, cancelled, deadline)
        except TimeoutError:
            result = ExtractionResult(status="timeout", reason="document_time_budget")
        except Exception:
            result = ExtractionResult(status="error", reason="document_read_failed")
        return replace(result, duration_ms=round((time.monotonic() - started) * 1000))

    @staticmethod
    def _remaining(deadline: float, timeout: int) -> int:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return max(1, min(timeout, int(remaining)))

    def _worker(
        self, module: str, path: Path, settings: ServerSettings, deadline: float
    ) -> ExtractionResult:
        timeout = self._remaining(
            deadline,
            settings.pdf_text_timeout_seconds
            if module == "extraction_pdf_worker"
            else getattr(settings, "extraction_timeout_seconds", 90),
        )
        extra = (
            getattr(settings, "pdf_max_pages", 200)
            if module == "extraction_pdf_worker"
            else timeout
        )
        raw = self._worker_runner.run(
            [
                sys.executable,
                "-m",
                "papagui_server.adapters." + module,
                str(path),
                str(settings.max_extracted_characters),
                str(extra),
                str(getattr(settings, "extraction_memory_mb", 768)),
            ],
            timeout=timeout,
        )
        if not raw:
            status = self._worker_runner.last_status
            return ExtractionResult(
                status=status if status != "ok" else "error", reason="parser_" + status
            )
        try:
            return ExtractionResult.from_dict(json.loads(raw))
        except (ValueError, TypeError, KeyError):
            return ExtractionResult(status="error", reason="invalid_parser_result")

    def _pdf(
        self, path: Path, settings: ServerSettings, cancelled: Callable[[], bool], deadline: float
    ) -> ExtractionResult:
        # Old injected command runners remain useful for deterministic integration
        # tests; production layout readers always run in a killable worker.
        if not isinstance(self._runner, ExternalCommandRunner):
            return self._pdf_fallback(path, settings, cancelled, deadline)
        native = self._worker("extraction_pdf_worker", path, settings, deadline)
        if native.status in {"encrypted", "too_large", "timeout"}:
            return native
        if native.status in {"error", "tool_missing"}:
            return self._pdf_fallback(path, settings, cancelled, deadline)
        if not settings.ocr_enabled:
            if any(page.status == "no_text" for page in native.pages):
                return replace(
                    native, status="partial" if native.text else "no_text", reason="ocr_disabled"
                )
            return native
        blocks = list(native.blocks)
        pages = list(native.pages)
        policy = ExtractionResourcePolicy.for_document(path, settings)
        attempts = 0
        reason = native.reason
        with TemporaryDirectory(prefix="papagui-ocr-") as temporary:
            for index, page in enumerate(pages):
                page_text = "\n".join(block.text for block in blocks if block.page == page.number)
                if len(page_text.strip()) >= settings.ocr_extension_threshold:
                    continue
                if cancelled() or attempts >= policy.ocr_pages:
                    pages[index] = replace(
                        page,
                        status="partial",
                        reason="cancelled" if cancelled() else "ocr_page_budget",
                    )
                    reason = pages[index].reason
                    continue
                attempts += 1
                prefix = Path(temporary) / "page"
                output = prefix.with_suffix(".png")
                try:
                    self._runner.run(
                        [
                            "pdftoppm",
                            "-png",
                            "-singlefile",
                            "-f",
                            str(page.number),
                            "-l",
                            str(page.number),
                            "-r",
                            str(policy.render_dpi),
                            "-scale-to",
                            str(int(getattr(settings, "image_max_pixels", 25_000_000) ** 0.5)),
                            str(path),
                            str(prefix),
                        ],
                        timeout=self._remaining(deadline, policy.pdf_timeout_seconds),
                    )
                    if not output.is_file():
                        status = getattr(self._runner, "last_status", "error")
                        pages[index] = replace(
                            page,
                            status=status if status != "ok" else "error",
                            reason="render_failed",
                        )
                        reason = pages[index].reason
                        continue
                    # Scale from the actual render, including Poppler's pixel cap.
                    from PIL import Image

                    with Image.open(output) as rendered:
                        scale = (page.width or rendered.width) / rendered.width
                    extracted = self._ocr(output, page.number, settings, deadline, scale=scale)
                    if extracted:
                        # Replace deficient page text; never duplicate good native text.
                        if len(" ".join(block.text for block in extracted)) >= len(page_text):
                            blocks = [
                                block for block in blocks if block.page != page.number
                            ] + extracted
                        pages[index] = replace(page, status="ok", reason="", method="tesseract_tsv")
                    else:
                        status = getattr(self._runner, "last_status", "no_text")
                        pages[index] = replace(
                            page,
                            status=status if status != "ok" else "no_text",
                            reason="ocr_no_text",
                        )
                        reason = pages[index].reason
                except TimeoutError:
                    pages[index] = replace(page, status="timeout", reason="document_time_budget")
                    reason = "document_time_budget"
                    break
                finally:
                    output.unlink(missing_ok=True)
                if sum(len(block.text) for block in blocks) >= settings.max_extracted_characters:
                    reason = "character_budget"
                    break
                if policy.pause_seconds:
                    time.sleep(policy.pause_seconds)
        blocks.sort(key=lambda block: block.page or 0)
        complete = (
            native.status != "partial"
            and len(pages) == native.pages_total
            and all(page.status == "ok" for page in pages)
        )
        status = (
            "ok"
            if complete
            else "partial"
            if blocks
            else next(
                (page.status for page in pages if page.status not in {"ok", "no_text"}), "no_text"
            )
        )
        return bounded_result(
            blocks,
            settings.max_extracted_characters,
            status=status,
            reason="" if complete else reason,
            pages_total=native.pages_total,
            pages_processed=len(pages),
            pages=tuple(pages),
        )

    def _pdf_fallback(
        self, path: Path, settings: ServerSettings, cancelled: Callable[[], bool], deadline: float
    ) -> ExtractionResult:
        policy = ExtractionResourcePolicy.for_document(path, settings)
        data = self._runner.run(
            [
                "pdftotext",
                "-layout",
                "-f",
                "1",
                "-l",
                str(getattr(settings, "pdf_max_pages", 200)),
                str(path),
                "-",
            ],
            timeout=self._remaining(deadline, policy.pdf_timeout_seconds),
        )
        text = data.decode("utf-8", errors="replace")
        page_texts = text.split("\f")
        if len(page_texts) > 1 and not page_texts[-1].strip():
            page_texts.pop()
        blocks = [
            ExtractionBlock(value.strip(), page=number, method="pdftotext")
            for number, value in enumerate(page_texts, 1)
            if value.strip()
        ]
        if (
            not settings.ocr_enabled
            or len(text.strip()) >= settings.ocr_extension_threshold
            or cancelled()
        ):
            status = "partial" if blocks else getattr(self._runner, "last_status", "no_text")
            if status == "ok":
                status = "no_text"
            return bounded_result(
                blocks,
                settings.max_extracted_characters,
                status=status,
                reason="layout_unavailable",
                pages_total=None,
                pages_processed=len(page_texts) if text else 0,
            )
        with TemporaryDirectory(prefix="papagui-ocr-") as temporary:
            prefix = Path(temporary) / "page"
            self._runner.run(
                [
                    "pdftoppm",
                    "-png",
                    "-f",
                    "1",
                    "-l",
                    str(policy.ocr_pages),
                    "-r",
                    str(policy.render_dpi),
                    "-scale-to",
                    str(int(getattr(settings, "image_max_pixels", 25_000_000) ** 0.5)),
                    str(path),
                    str(prefix),
                ],
                timeout=self._remaining(deadline, policy.pdf_timeout_seconds),
            )
            for number, image in enumerate(
                sorted(Path(temporary).glob("page-*.png"))[: policy.ocr_pages], 1
            ):
                if cancelled():
                    break
                values = self._ocr(image, number, settings, deadline, scale=72 / policy.render_dpi)
                if values:
                    # Text-only injected fixtures retain their historical behavior.
                    if isinstance(self._runner, ExternalCommandRunner):
                        blocks = [block for block in blocks if block.page != number]
                    blocks.extend(values)
                image.unlink(missing_ok=True)
                if sum(len(block.text) for block in blocks) >= settings.max_extracted_characters:
                    break
                if policy.pause_seconds:
                    time.sleep(policy.pause_seconds)
        status = "partial" if blocks else getattr(self._runner, "last_status", "no_text")
        return bounded_result(
            blocks,
            settings.max_extracted_characters,
            status=status if status != "ok" else "no_text",
            reason="layout_unavailable",
            pages_processed=max((block.page or 0 for block in blocks), default=0),
        )

    def _ocr(
        self,
        image: Path,
        page: int,
        settings: ServerSettings,
        deadline: float,
        *,
        scale: float = 1.0,
    ) -> list[ExtractionBlock]:
        if type(self._runner) is ExternalCommandRunner:
            languages = self._runner.run(
                ["tesseract", "--list-langs"], timeout=self._remaining(deadline, 5)
            )
            if b"deu" not in languages.splitlines():
                if self._runner.last_status == "ok":
                    self._runner.last_status = "tool_missing"
                return []
        data = self._runner.run(
            ["tesseract", str(image), "stdout", "-l", "deu", "--psm", "1", "tsv"],
            timeout=self._remaining(deadline, settings.ocr_timeout_seconds),
        )
        return tsv_blocks(data, page=page, scale=scale)

    def _image(
        self, path: Path, settings: ServerSettings, cancelled: Callable[[], bool], deadline: float
    ) -> ExtractionResult:
        if not settings.ocr_enabled:
            return ExtractionResult(status="no_text", reason="ocr_disabled", pages_total=1)
        try:
            from PIL import Image, ImageOps, ImageStat

            with Image.open(path) as original:
                if original.width * original.height > getattr(
                    settings, "image_max_pixels", 25_000_000
                ):
                    return ExtractionResult(
                        status="too_large",
                        reason="image_pixel_budget",
                        pages_total=getattr(original, "n_frames", 1),
                    )
                total = getattr(original, "n_frames", 1)
                policy = ExtractionResourcePolicy.for_document(path, settings)
                blocks: list[ExtractionBlock] = []
                pages: list[ExtractionPage] = []
                with TemporaryDirectory(prefix="papagui-image-") as temporary:
                    for number in range(1, min(total, policy.ocr_pages) + 1):
                        if cancelled():
                            break
                        original.seek(number - 1)
                        frame = ImageOps.exif_transpose(original).convert("RGB")
                        probe = frame.copy()
                        probe.thumbnail((256, 256))
                        gray = ImageOps.grayscale(probe)
                        histogram = gray.histogram()
                        light_fraction = sum(histogram[190:]) / max(1, sum(histogram))
                        low_chroma = (
                            max(ImageStat.Stat(probe).mean) - min(ImageStat.Stat(probe).mean) < 18
                        )
                        preferred = any(
                            word in path.stem.casefold()
                            for word in (
                                "scan",
                                "kontakt",
                                "anschreiben",
                                "auftrag",
                                "angebot",
                                "vertrag",
                                "dokument",
                            )
                        )
                        if not preferred and not (light_fraction > 0.45 and low_chroma):
                            pages.append(
                                ExtractionPage(
                                    number, "partial", "image_not_document", method="image_probe"
                                )
                            )
                            continue
                        rendered = Path(temporary) / "page.png"
                        frame.thumbnail((5000, 5000))
                        frame.save(rendered)
                        try:
                            values = self._ocr(rendered, number, settings, deadline)
                        except TimeoutError:
                            pages.append(
                                ExtractionPage(
                                    number,
                                    "timeout",
                                    "document_time_budget",
                                    method="tesseract_tsv",
                                )
                            )
                            break
                        blocks.extend(values)
                        status = "ok" if values else getattr(self._runner, "last_status", "no_text")
                        pages.append(
                            ExtractionPage(
                                number,
                                status if status != "ok" or values else "no_text",
                                "" if values else "ocr_no_text",
                                frame.width,
                                frame.height,
                                "tesseract_tsv",
                            )
                        )
                        rendered.unlink(missing_ok=True)
                        if (
                            sum(len(block.text) for block in blocks)
                            >= settings.max_extracted_characters
                        ):
                            break
                complete = len(pages) == total and all(page.status == "ok" for page in pages)
                reason = (
                    ""
                    if complete
                    else "image_page_budget"
                    if len(pages) < total
                    else next((page.reason for page in pages if page.reason), "ocr_no_text")
                )
                status = (
                    "ok"
                    if complete
                    else "partial"
                    if blocks or any(page.status == "partial" for page in pages)
                    else next((page.status for page in pages), "partial")
                )
                return bounded_result(
                    blocks,
                    settings.max_extracted_characters,
                    status=status,
                    reason=reason,
                    pages_total=total,
                    pages_processed=len(pages),
                    pages=tuple(pages),
                )
        except ImportError:
            return ExtractionResult(status="tool_missing", reason="image_tool_missing")
        except (OSError, ValueError):
            return ExtractionResult(status="error", reason="invalid_image")
