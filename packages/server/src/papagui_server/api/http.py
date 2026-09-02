"""Shared HTTP boundary helpers for versioned routers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse, StreamingResponse
from papagui_contracts import IdempotencyKey


def mutation_response(result: Any) -> JSONResponse:
    headers = {"X-Idempotent-Replay": "true"} if result.replayed else {}
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)


def idempotency_key(value: object) -> str:
    return IdempotencyKey.from_value(value).value


def expected_revision(body_value: object, header_value: str | None) -> int:
    value = body_value if body_value is not None else (header_value or "").strip().strip('"')
    if isinstance(value, bool):
        raise ValueError("Eine gültige erwartete Revision ist erforderlich.")
    try:
        revision = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Eine gültige erwartete Revision ist erforderlich.") from error
    if revision < 0:
        raise ValueError("Eine gültige erwartete Revision ist erforderlich.")
    return revision


def mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} muss ein Objekt sein.")
    return dict(value)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "status": status_code}},
    )


def archive_response(path: Path) -> StreamingResponse:
    async def chunks():
        stream = path.open("rb")
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={
            "Content-Length": str(path.stat().st_size),
            "Content-Disposition": f'attachment; filename="{path.name}"',
        },
    )
