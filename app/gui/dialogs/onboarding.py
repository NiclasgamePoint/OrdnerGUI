from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from app.gui.widgets import AppButton


class OnboardingDialog(QDialog):
    """First-run setup for a local, mounted or network data source."""

    def __init__(self, initial_path: Path | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("OnboardingDialog")
        self.setWindowTitle("PapaGUI einrichten")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.selected_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Willkommen bei PapaGUI")
        title.setObjectName("PopupTitle")
        layout.addWidget(title)

        description = QLabel(
            "Wählen Sie den Ordner aus, in dem Ihre Dokumente liegen. Das kann "
            "ein lokaler Ordner, ein eingebundenes Netzlaufwerk oder eine NAS-Freigabe sein."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        nas_hint = QLabel(
            "NAS: Verbinden Sie die Freigabe zuerst im Betriebssystem. Unter Windows "
            "können Sie auch einen UNC-Pfad wie \\\\\\server\\freigabe eingeben."
        )
        nas_hint.setObjectName("PopupCaption")
        nas_hint.setWordWrap(True)
        layout.addWidget(nas_hint)

        path_row = QHBoxLayout()
        self.path_input = QLineEdit(str(initial_path or ""))
        self.path_input.setPlaceholderText("Datenordner oder Netzwerkpfad")
        self.path_input.textChanged.connect(lambda _text: self.error_label.clear())
        path_row.addWidget(self.path_input, 1)
        browse_button = AppButton("Durchsuchen", AppButton.SECONDARY)
        browse_button.clicked.connect(self.choose_path)
        path_row.addWidget(browse_button)
        layout.addLayout(path_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("SettingsError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        info = QLabel(
            "Der Dokumentindex wird lokal gespeichert. Ihre Originaldateien werden nicht verändert."
        )
        info.setObjectName("PopupCaption")
        info.setWordWrap(True)
        layout.addWidget(info)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel_button = AppButton("Später", AppButton.SECONDARY)
        cancel_button.clicked.connect(self.reject)
        actions.addWidget(cancel_button)
        start_button = AppButton("Einrichtung abschließen")
        start_button.setDefault(True)
        start_button.clicked.connect(self.accept_path)
        actions.addWidget(start_button)
        layout.addLayout(actions)

    def choose_path(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Datenquelle auswählen",
            self.path_input.text().strip(),
            QFileDialog.ShowDirsOnly,
        )
        if selected:
            self.path_input.setText(selected)

    def accept_path(self):
        raw_path = self.path_input.text().strip()
        if not raw_path:
            self.error_label.setText("Bitte wählen Sie einen Datenordner aus.")
            return
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_dir():
            self.error_label.setText(
                "Der Ordner ist nicht erreichbar. Bitte prüfen Sie die NAS-/Netzwerkverbindung."
            )
            return
        self.selected_path = path.resolve()
        super().accept()
