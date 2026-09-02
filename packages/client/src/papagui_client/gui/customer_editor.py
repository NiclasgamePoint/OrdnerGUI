"""Focused customer editor and merge dialog."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from papagui_contracts import Contact, Customer


class CustomerEditorDialog(QDialog):
    def __init__(
        self,
        customer: Customer | None = None,
        *,
        explanation: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._original = customer or Customer()
        self.setWindowTitle("Kunde bearbeiten" if customer else "Kunde anlegen")
        self.resize(520, 560)
        layout = QVBoxLayout(self)
        if explanation:
            label = QLabel(explanation)
            label.setWordWrap(True)
            layout.addWidget(label)
        form = QFormLayout()
        self.fields = {}
        for key, label_text in (
            ("display_name", "Anzeigename"),
            ("entity_type", "Kundentyp"),
            ("company", "Unternehmen"),
            ("email", "E-Mail"),
            ("phone", "Telefon"),
            ("street", "Straße"),
            ("postal_code", "Postleitzahl"),
            ("city", "Ort"),
        ):
            field = QLineEdit(str(getattr(self._original, key)))
            self.fields[key] = field
            form.addRow(label_text, field)
        portable_projects = "\n".join(
            f"{project.source.source_id}: {project.source.relative_path}"
            for project in self._original.projects
            if project.source is not None
        )
        legacy_paths = "\n".join(self._original.folder_paths)
        self.folder_summary = QLabel(
            portable_projects
            or legacy_paths
            or "Keine Projektzuordnung (wird serverseitig erkannt)"
        )
        self.folder_summary.setWordWrap(True)
        self.folder_summary.setToolTip(
            "Projektpfade sind serververwaltet und können hier nicht überschrieben werden."
        )
        form.addRow("Projekte (nur lesen)", self.folder_summary)
        self.services = QLineEdit(", ".join(self._original.service_types))
        form.addRow("Leistungsarten", self.services)
        self.contacts = QPlainTextEdit(
            "\n".join(
                " | ".join((contact.name, contact.role, contact.email, contact.phone))
                for contact in self._original.contacts
            )
        )
        self.contacts.setPlaceholderText("Name | Rolle | E-Mail | Telefon")
        form.addRow("Kontakte", self.contacts)
        self.notes = QPlainTextEdit("\n".join(self._original.notes))
        form.addRow("Notizen", self.notes)
        self.tags = QLineEdit(", ".join(self._original.tags))
        form.addRow("Tags", self.tags)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def customer(self) -> Customer:
        values = {name: field.text().strip() for name, field in self.fields.items()}
        services = tuple(
            value.strip() for value in self.services.text().split(",") if value.strip()
        )
        contacts = tuple(
            Contact(*((parts := [value.strip() for value in line.split("|", 3)]) + [""] * (4 - len(parts))))
            for line in self.contacts.toPlainText().splitlines()
            if line.strip()
        )
        notes = tuple(line.strip() for line in self.notes.toPlainText().splitlines() if line.strip())
        tags = tuple(value.strip() for value in self.tags.text().split(",") if value.strip())
        return replace(
            self._original,
            **values,
            service_types=services,
            contacts=contacts,
            notes=notes,
            tags=tags,
        )
