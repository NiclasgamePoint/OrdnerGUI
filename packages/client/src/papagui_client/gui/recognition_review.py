"""Explicit review UI for server-derived recognition cases."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
)


class RecognitionReviewDialog(QDialog):
    def __init__(self, server_control, customers=(), parent=None):
        super().__init__(parent)
        self._control = server_control
        self._customers = tuple(customers)
        self._cases = ()
        self.setWindowTitle("Kundenerkennung prüfen")
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.status = QLabel("Noch nicht geladen")
        header.addWidget(self.status)
        header.addStretch()
        reload_button = QPushButton("Aktualisieren")
        reload_button.clicked.connect(self.reload)
        header.addWidget(reload_button)
        run_button = QPushButton("Erkennung jetzt starten")
        run_button.clicked.connect(self.start_run)
        header.addWidget(run_button)
        layout.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.cases = QListWidget()
        self.cases.currentRowChanged.connect(self._show_case)
        splitter.addWidget(self.cases)
        self.details = QTextBrowser()
        splitter.addWidget(self.details)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.customer = QComboBox()
        self.customer.addItem("Zielkunde wählen …", None)
        for view in self._customers:
            if view.customer.id is not None:
                self.customer.addItem(view.customer.display_name, view)
        actions.addWidget(self.customer)
        for label, action in (
            ("Als neuen Kunden annehmen", "accept"),
            ("Zuweisen", "assign"),
            ("Ablehnen", "reject"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, value=action: self.decide(value)
            )
            actions.addWidget(button)
        layout.addLayout(actions)
        self.reload()

    def reload(self) -> None:
        try:
            self._cases = tuple(self._control.recognition_cases("pending"))
            runs = tuple(self._control.recognition_runs(10))
        except Exception as exc:
            self.status.setText(f"Nicht verfügbar: {exc}")
            return
        self.cases.clear()
        for case in self._cases:
            item = QListWidgetItem(case.display_name)
            item.setToolTip(case.reason)
            self.cases.addItem(item)
        last = runs[0] if runs else None
        suffix = f" · letzter Lauf: {last.started_at}" if last is not None else ""
        self.status.setText(f"{len(self._cases)} offene Fälle{suffix}")
        if self._cases:
            self.cases.setCurrentRow(0)
        else:
            self.details.setText("Keine offenen Erkennungsfälle.")

    def start_run(self) -> None:
        if not self._ensure_admin():
            return
        try:
            summary = self._control.start_recognition()
        except Exception as exc:
            QMessageBox.warning(self, "Erkennung fehlgeschlagen", str(exc))
            return
        self.status.setText(
            f"Erkennung beendet: {summary.detected} erkannt, {summary.pending} offen"
        )
        self.reload()

    def decide(self, action: str) -> None:
        case = self.selected_case()
        if case is None or not self._ensure_admin():
            return
        customer_id = None
        expected_revision = None
        if action == "accept":
            expected_revision = 0
        elif action == "assign":
            view = self.customer.currentData()
            if view is None or view.customer.id is None:
                QMessageBox.warning(self, "Zielkunde fehlt", "Bitte einen Zielkunden wählen.")
                return
            customer_id = view.customer.id
            expected_revision = view.customer.revision
        try:
            self._control.decide_recognition(
                case.signature,
                action,
                customer_id,
                expected_revision,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Entscheidung fehlgeschlagen", str(exc))
            return
        self.reload()

    def selected_case(self):
        row = self.cases.currentRow()
        return self._cases[row] if 0 <= row < len(self._cases) else None

    def _show_case(self, row: int) -> None:
        if not 0 <= row < len(self._cases):
            self.details.clear()
            return
        case = self._cases[row]
        projects = "\n".join(
            f"• {source.source_id}:{source.relative_path}"
            for source in case.project_roots
        )
        evidence = "\n".join(
            f"• {item.field_name}: {item.value} ({item.confidence:.0%})\n"
            f"  {item.source.source_id}:{item.source.relative_path} · {item.rule}\n"
            f"  {item.excerpt}"
            for item in case.evidence
        )
        self.details.setPlainText(
            f"{case.display_name}\n\nGrund: {case.reason}\n\nProjekte:\n{projects}"
            f"\n\nProvenienz/Evidenz:\n{evidence or 'Keine Detailbelege'}"
        )

    def _ensure_admin(self) -> bool:
        if self._control.admin_unlocked:
            return True
        password, accepted = QInputDialog.getText(
            self,
            "Admin entsperren",
            "Adminpasswort",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return False
        try:
            self._control.login_admin(password)
        except Exception as exc:
            QMessageBox.warning(self, "Anmeldung fehlgeschlagen", str(exc))
            return False
        return True
