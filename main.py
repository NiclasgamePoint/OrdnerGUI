"""Thin development entry point for the independent client product."""

from __future__ import annotations

from papagui_client.entrypoints.client import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
