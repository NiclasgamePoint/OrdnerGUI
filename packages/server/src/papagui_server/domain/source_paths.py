"""Pure mapping between SourcePath values and transitional source:// strings."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit
import re

from papagui_contracts import SourcePath


def source_uri(source: SourcePath) -> str:
    return f"source://{source.source_id}/{quote(source.relative_path, safe='/')}"


def source_path_from_uri(value: str) -> SourcePath:
    parsed = urlsplit(value)
    if parsed.scheme != "source" or not parsed.netloc:
        raise ValueError("Ungültige portable Quellreferenz.")
    return SourcePath(parsed.netloc, unquote(parsed.path).strip("/"))


def coerce_source_path(value: str, *, default_source_id: str = "primary") -> SourcePath:
    try:
        return source_path_from_uri(value)
    except ValueError:
        raw = str(value or "").replace("\\", "/")
        drive = re.match(r"^([A-Za-z]):/(.*)$", raw)
        if drive:
            return SourcePath(f"legacy-{drive.group(1).casefold()}", drive.group(2).strip("/") or "root")
        if raw.startswith("//"):
            parts = [part for part in raw.split("/") if part]
            host = "-".join(parts[:2]) or "share"
            source_id = "unc-" + re.sub(r"[^A-Za-z0-9._-]+", "-", host)[:59]
            return SourcePath(source_id, "/".join(parts[2:]) or "root")
        normalized = raw.strip("/")
        if normalized.casefold().startswith("source/"):
            normalized = normalized.partition("/")[2]
        elif raw.startswith("/"):
            default_source_id = "legacy-posix"
        return SourcePath(default_source_id, normalized or "legacy/unknown")
