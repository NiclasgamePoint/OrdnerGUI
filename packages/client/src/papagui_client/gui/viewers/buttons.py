"""Small local button factory keeping viewer widgets self-contained."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton


def viewer_button(text: str, *, compact: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("viewerAction", True)
    if compact:
        button.setMinimumWidth(34)
        button.setMaximumWidth(44)
    return button
