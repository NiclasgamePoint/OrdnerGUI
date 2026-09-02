"""Image preview fitted to the viewport with Ctrl+wheel zoom."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget


class ImageViewerWidget(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ImageViewerScroll")
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(self._image_label)
        self._source_pixmap: QPixmap | None = None
        self._zoom_multiplier = 1.0

    def set_image(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self._zoom_multiplier = 1.0
        self._update_display_pixmap()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._source_pixmap is not None:
            self._update_display_pixmap()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
            and self._source_pixmap is not None
        ):
            delta = event.angleDelta().y()
            if delta:
                factor = 1.15 if delta > 0 else 1 / 1.15
                self._zoom_multiplier = max(
                    1.0, min(8.0, self._zoom_multiplier * factor)
                )
                self._update_display_pixmap()
            event.accept()
            return
        super().wheelEvent(event)

    def _update_display_pixmap(self) -> None:
        if self._source_pixmap is None:
            return
        width = self._source_pixmap.width()
        height = self._source_pixmap.height()
        if width <= 0 or height <= 0:
            return
        viewport_size = self.viewport().size()
        fit_scale = min(
            max(1, viewport_size.width()) / width,
            max(1, viewport_size.height()) / height,
            1.0,
        )
        scale = fit_scale * self._zoom_multiplier
        scaled = self._source_pixmap.scaled(
            max(1, int(width * scale)),
            max(1, int(height * scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())
