from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from app.core.index_job_state import ACTIVE_STATUSES, read_state
from app.core.logging_config import LOG_FILE


class IndexTrayWindow(QDialog):
    """Small themed status window opened from the system tray."""

    cancelRequested = Signal()

    def __init__(
        self,
        state_dir: Path,
        log_file: Path = LOG_FILE,
        parent=None,
        content_state_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.state_dir = state_dir
        self.content_state_dir = content_state_dir
        self.log_file = log_file
        self.setWindowTitle("PapaGUI · Indexierung")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setMinimumSize(620, 430)
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Katalogindex")
        title.setObjectName("PopupSectionTitle")
        layout.addWidget(title)

        self.status_label = QLabel("Noch keine Indexierung gestartet")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("IndexStatusText")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.detail_label)

        content_title = QLabel("Dateiindizierung im Hintergrund")
        content_title.setObjectName("PopupSectionTitle")
        layout.addWidget(content_title)

        self.content_status_label = QLabel("Noch keine Dateiindizierung gestartet")
        self.content_status_label.setWordWrap(True)
        self.content_status_label.setObjectName("IndexStatusText")
        layout.addWidget(self.content_status_label)

        self.content_progress_bar = QProgressBar()
        self.content_progress_bar.setRange(0, 100)
        self.content_progress_bar.setValue(0)
        layout.addWidget(self.content_progress_bar)

        self.content_detail_label = QLabel("")
        self.content_detail_label.setWordWrap(True)
        self.content_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.content_detail_label)

        log_title = QLabel("Letzte 5 Logzeilen")
        log_title.setObjectName("PopupSectionTitle")
        layout.addWidget(log_title)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_output.setPlaceholderText("Noch keine Logeinträge vorhanden.")
        layout.addWidget(self.log_output, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Indexierung abbrechen")
        self.cancel_button.setProperty("buttonRole", "secondary")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        buttons.addWidget(self.cancel_button)
        close_button = QPushButton("Schließen")
        close_button.setProperty("buttonRole", "primary")
        close_button.clicked.connect(self.hide)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)

    def show_status(self):
        self.refresh()
        self.timer.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        state = read_state(self.state_dir)
        status = str(state.get("status") or "")
        active = status in ACTIVE_STATUSES
        processed = int(
            state.get("processed_count") or state.get("indexed_count") or 0
        )
        labels = {
            "starting": "Indexierung wird gestartet …",
            "running": "Indexierung läuft im Hintergrund",
            "ready": "Index ist fertig und wird aktiviert …",
            "completed": "Indexierung erfolgreich abgeschlossen",
            "no_changes": "Index ist aktuell – keine Änderungen",
            "cancelled": "Indexierung wurde abgebrochen",
            "error": "Indexierung ist fehlgeschlagen",
        }
        self.status_label.setText(labels.get(status, "Noch keine Indexierung gestartet"))
        content_active = self._refresh_content_state()
        self.cancel_button.setVisible(active or content_active)
        if active:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100 if status in {"completed", "no_changes"} else 0)

        details = []
        if processed:
            details.append(f"{processed} Dateien geprüft")
        current_path = str(state.get("current_path") or "")
        if current_path:
            details.append(f"Aktuell: {current_path}")
        error = str(state.get("error") or "")
        if error:
            details.append(f"Fehler: {error}")
        self.detail_label.setText("\n".join(details))
        self._refresh_log()

    def _refresh_content_state(self) -> bool:
        state = read_state(self.content_state_dir) if self.content_state_dir else {}
        status = str(state.get("status") or "")
        active = status in ACTIVE_STATUSES
        labels = {
            "starting": "Dateiindizierung wird gestartet …",
            "running": "Dokumentinhalte werden indexiert",
            "ready": "Dateiindex wird fertiggestellt …",
            "completed": "Dateiindizierung erfolgreich abgeschlossen",
            "cancelled": "Dateiindizierung wurde abgebrochen",
            "error": "Dateiindizierung ist fehlgeschlagen",
        }
        self.content_status_label.setText(
            labels.get(status, "Noch keine Dateiindizierung gestartet")
        )

        completed = int(state.get("completed_documents") or 0)
        total = int(state.get("total_documents") or 0)
        if active and not total:
            self.content_progress_bar.setRange(0, 0)
        else:
            self.content_progress_bar.setRange(0, max(1, total or 100))
            self.content_progress_bar.setValue(
                min(completed, total) if total else (100 if status == "completed" else 0)
            )
        self.content_progress_bar.setFormat(
            f"{completed} / {total} Dokumente (%p %)" if total else "%p %"
        )

        details = []
        if total:
            pending = int(state.get("pending_documents") or 0)
            details.append(f"{completed} von {total} Dokumenten · {pending} ausstehend")
        failed = int(state.get("failed_documents") or state.get("failed_count") or 0)
        if failed:
            details.append(f"{failed} Dokumente mit Fehlern")
        current_path = str(state.get("current_path") or "")
        if current_path:
            details.append(f"Aktuell: {current_path}")
        error = str(state.get("error") or "")
        if error:
            details.append(f"Fehler: {error}")
        self.content_detail_label.setText("\n".join(details))
        return active

    def _refresh_log(self):
        try:
            with self.log_file.open("r", encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=5)
        except OSError:
            lines = deque()
        text = "".join(lines).rstrip()
        if text != self.log_output.toPlainText():
            self.log_output.setPlainText(text)
            scrollbar = self.log_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event: QCloseEvent):
        self.timer.stop()
        event.accept()
