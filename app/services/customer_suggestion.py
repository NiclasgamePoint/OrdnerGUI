from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import openpyxl
import xlrd
from docx import Document
from PyPDF2 import PdfReader

from app.core.customer_models import Contact
from app.services.document_converter import DocumentConverter


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+49|0)[0-9][0-9\s/().-]{6,}[0-9]")
POSTAL_CITY_RE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .-]{2,})")
STREET_RE = re.compile(
    r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .-]{2,}(?:straße|str\.|weg|allee|platz|ring|gasse|chaussee)\s+\d+[a-zA-Z]?)",
    re.IGNORECASE,
)
NAME_HINT_RE = re.compile(r"(?im)^(?:kunde|auftraggeber|angebot an|an:)\s*:?[ \t]*(.+)$")

COMPANY_MARKERS = (
    "gmbh",
    "ag",
    "ug",
    "kg",
    "ohg",
    "e.k",
    "mbh",
    "gemeinde",
    "stadt",
    "amt",
)


@dataclass
class CustomerSuggestion:
    display_name: str = ""
    company: str = ""
    entity_type: str = "Unternehmen"
    city: str = ""
    postal_code: str = ""
    street: str = ""
    email: str = ""
    phone: str = ""
    service_types: list[str] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def has_values(self) -> bool:
        return any(
            [
                self.display_name,
                self.company,
                self.city,
                self.postal_code,
                self.street,
                self.email,
                self.phone,
                self.service_types,
                self.contacts,
            ]
        )


class CustomerSuggestionService:
    """Create best-effort customer suggestions from folder structure and document text."""

    TEXT_EXTENSIONS = {"txt", "csv", "log", "md", "json", "xml", "yaml", "yml", "ini"}
    SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {"pdf", "docx", "doc", "xlsx", "xls"}

    def __init__(self):
        self._converter = DocumentConverter()

    def suggest_for_folder(self, folder_path: Path, suggested_name: str = "") -> CustomerSuggestion:
        folder_path = folder_path.resolve()
        suggestion = CustomerSuggestion()

        self._extract_from_path(folder_path, suggested_name, suggestion)
        combined_text = self._collect_text_from_documents(folder_path)
        if combined_text:
            self._extract_from_text(combined_text, suggestion)
            self._extract_contacts_from_text(combined_text, suggestion)

        if suggestion.display_name and not suggestion.company:
            suggestion.company = suggestion.display_name
        if suggestion.display_name and not suggestion.entity_type:
            suggestion.entity_type = self._infer_entity_type(suggestion.display_name)
        if not suggestion.entity_type:
            suggestion.entity_type = "Unternehmen"

        return suggestion

    def _extract_from_path(
        self,
        folder_path: Path,
        suggested_name: str,
        suggestion: CustomerSuggestion,
    ):
        project_label = folder_path.name.strip()
        if "," in project_label:
            name_part, city_part = project_label.split(",", 1)
            suggestion.display_name = name_part.strip() or suggested_name.strip()
            suggestion.city = city_part.strip()
        else:
            suggestion.display_name = suggested_name.strip() or project_label

        suggestion.entity_type = self._infer_entity_type(suggestion.display_name)

        parts = list(folder_path.parts)
        year_index = next(
            (index for index, part in enumerate(parts) if re.fullmatch(r"(?:19|20)\d{2}", part)),
            None,
        )
        if year_index is not None and year_index > 0:
            service = parts[year_index - 1].strip()
            if service:
                suggestion.service_types.append(service)

    def _collect_text_from_documents(self, folder_path: Path) -> str:
        if not folder_path.exists() or not folder_path.is_dir():
            return ""

        collected: list[str] = []
        total = 0
        for file_path in sorted(folder_path.rglob("*")):
            if not file_path.is_file():
                continue
            extension = file_path.suffix.lower().lstrip(".")
            if extension not in self.SUPPORTED_EXTENSIONS:
                continue
            text = self._extract_file_text(file_path, extension)
            if not text:
                continue
            collected.append(text)
            total += len(text)
            if len(collected) >= 12 or total >= 250_000:
                break
        return "\n".join(collected)

    def _extract_contacts_from_text(self, text: str, suggestion: CustomerSuggestion):
        contacts_by_name: dict[str, Contact] = {}

        # Heuristic: lines with person-like names from addressing context.
        for match in NAME_HINT_RE.finditer(text):
            candidate_name = match.group(1).strip()
            if not candidate_name:
                continue
            key = candidate_name.casefold()
            contacts_by_name.setdefault(key, Contact(name=candidate_name))

        # Build contact entries from emails and nearest line names.
        text_lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line_index, line in enumerate(text_lines):
            for email_match in EMAIL_RE.finditer(line):
                email = email_match.group(0)
                name = ""
                for back in range(max(0, line_index - 2), line_index + 1):
                    candidate = text_lines[back]
                    if EMAIL_RE.search(candidate) or PHONE_RE.search(candidate):
                        continue
                    if len(candidate.split()) >= 2:
                        name = candidate
                if not name:
                    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
                    name = local.title() if local else suggestion.display_name or "Kontakt"
                key = name.casefold()
                contact = contacts_by_name.setdefault(key, Contact(name=name))
                if not contact.email:
                    contact.email = email

        # Attach phones to nearest suitable contact or create a generic one.
        phone_matches = [match.group(0) for match in PHONE_RE.finditer(text)]
        for phone in phone_matches:
            assigned = False
            for contact in contacts_by_name.values():
                if not contact.phone:
                    contact.phone = re.sub(r"\s+", " ", phone).strip()
                    assigned = True
                    break
            if not assigned:
                fallback_name = suggestion.display_name or "Kontakt"
                key = fallback_name.casefold()
                contact = contacts_by_name.setdefault(key, Contact(name=fallback_name))
                if not contact.phone:
                    contact.phone = re.sub(r"\s+", " ", phone).strip()

        suggestion.contacts = [
            contact
            for contact in contacts_by_name.values()
            if contact.name.strip()
        ]

        # Keep top-level shortcuts aligned with first extracted contact.
        if suggestion.contacts:
            primary = suggestion.contacts[0]
            if not suggestion.email:
                suggestion.email = primary.email
            if not suggestion.phone:
                suggestion.phone = primary.phone

    def _extract_file_text(self, file_path: Path, extension: str) -> str:
        try:
            if extension in self.TEXT_EXTENSIONS:
                return file_path.read_text(encoding="utf-8", errors="replace")[:80_000]
            if extension == "pdf":
                reader = PdfReader(str(file_path))
                pages = []
                for page in reader.pages[:3]:
                    pages.append(page.extract_text() or "")
                return "\n".join(pages)[:80_000]
            if extension == "docx":
                document = Document(str(file_path))
                lines = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
                return "\n".join(lines)[:80_000]
            if extension == "doc":
                return self._converter.extract_legacy_doc(file_path)[:80_000]
            if extension == "xlsx":
                workbook = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
                try:
                    rows = []
                    for worksheet in workbook.worksheets:
                        rows.append(worksheet.title)
                        for row in worksheet.iter_rows(max_row=20, values_only=True):
                            values = [str(value) for value in row if value is not None]
                            if values:
                                rows.append("\t".join(values))
                    return "\n".join(rows)[:80_000]
                finally:
                    workbook.close()
            if extension == "xls":
                workbook = xlrd.open_workbook(str(file_path), on_demand=True)
                try:
                    rows = []
                    for sheet in workbook.sheets():
                        rows.append(sheet.name)
                        for row_index in range(min(sheet.nrows, 20)):
                            values = [
                                str(sheet.cell_value(row_index, column_index))
                                for column_index in range(sheet.ncols)
                                if sheet.cell_value(row_index, column_index) != ""
                            ]
                            if values:
                                rows.append("\t".join(values))
                    return "\n".join(rows)[:80_000]
                finally:
                    workbook.release_resources()
        except Exception:
            return ""
        return ""

    def _extract_from_text(self, text: str, suggestion: CustomerSuggestion):
        if not suggestion.display_name:
            name_match = NAME_HINT_RE.search(text)
            if name_match:
                suggestion.display_name = name_match.group(1).strip()

        if not suggestion.email:
            email = EMAIL_RE.search(text)
            if email:
                suggestion.email = email.group(0)

        if not suggestion.phone:
            phone = PHONE_RE.search(text)
            if phone:
                suggestion.phone = re.sub(r"\s+", " ", phone.group(0)).strip()

        if not suggestion.postal_code or not suggestion.city:
            postal_city = POSTAL_CITY_RE.search(text)
            if postal_city:
                suggestion.postal_code = suggestion.postal_code or postal_city.group(1)
                suggestion.city = suggestion.city or postal_city.group(2).strip()

        if not suggestion.street:
            street = STREET_RE.search(text)
            if street:
                suggestion.street = street.group(1).strip()

        if suggestion.display_name and not suggestion.entity_type:
            suggestion.entity_type = self._infer_entity_type(suggestion.display_name)

    def _infer_entity_type(self, display_name: str) -> str:
        lowered = display_name.casefold()
        if any(marker in lowered for marker in COMPANY_MARKERS):
            return "Unternehmen"
        if "," in display_name:
            return "Privatperson"
        return "Unternehmen"
