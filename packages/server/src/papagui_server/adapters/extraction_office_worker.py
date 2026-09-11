"""Disposable Office parser process with bounded memory and sanitized output."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from papagui_server.adapters import extraction_office


def main() -> None:
    try:
        try:
            import resource

            memory_mb = max(128, min(2048, int(sys.argv[4]) if len(sys.argv) > 4 else 768))
            resource.setrlimit(
                resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024)
            )
        except (ImportError, OSError, ValueError):
            pass
        path = Path(sys.argv[1])
        parser = getattr(extraction_office, path.suffix.casefold().lstrip("."))
        result = parser(path, int(sys.argv[2]), lambda: False, time.monotonic() + int(sys.argv[3]))
        payload = result.to_dict()
    except ModuleNotFoundError:
        payload = {"status": "tool_missing", "reason": "office_tool_missing"}
    except (OverflowError, MemoryError):
        payload = {"status": "too_large", "reason": "office_resource_budget"}
    except Exception as exc:
        # xlrd signals encrypted BIFF workbooks with this generic exception.
        encrypted = "encrypt" in str(exc).casefold() or "password" in type(exc).__name__.casefold()
        payload = {
            "status": "encrypted" if encrypted else "error",
            "reason": "encrypted_document" if encrypted else "invalid_office_document",
        }
    # ASCII JSON escapes preserve all Unicode through Windows legacy code pages.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))


if __name__ == "__main__":
    main()
