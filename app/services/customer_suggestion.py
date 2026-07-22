from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import phonenumbers
from docx import Document
from PyPDF2 import PdfReader

from app.core.customer_models import Contact
from app.core.customer_recognition_models import ExtractionEvidence
from app.services.document_converter import DocumentConverter


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+\d{1,3}|00\d{1,3}|0[1-9])"
    r"(?:[\s/().-]*\d){5,14}(?![A-Za-z0-9])"
)
POSTAL_CITY_RE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .-]{2,})")
STREET_RE = re.compile(
    r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .-]{2,}(?:straße|str\.|weg|allee|platz|ring|gasse|chaussee)\s+\d+[a-zA-Z]?)",
    re.IGNORECASE,
)
NAME_HINT_RE = re.compile(r"(?im)^(?:kunde|auftraggeber|angebot an|an:)\s*:?[ \t]*(.+)$")
PERSON_HINT_RE = re.compile(
    r"(?i)\b(?:(?:z\.?\s*hd\.?|ansprechpartner(?:in)?|kontakt)\s*:?\s*)?"
    r"(?:(?:herrn?|frau)\s+)(?:(?:dr\.?|prof\.?)\s+)?"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+){1,3})"
)
CONTACT_LABEL_RE = re.compile(
    r"(?i)^(?:z\.?\s*hd\.?|ansprechpartner(?:in)?|kontakt)\s*:?\s*"
    r"(?:(?:herrn?|frau)\s+)?(?:(?:dr\.?|prof\.?)\s+)?"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+){1,3})$"
)
RECIPIENT_CONTEXT_RE = re.compile(
    r"(?i)\b(?:auftraggeber|kunde|angebot an|empfänger|z\.?\s*hd\.?|"
    r"ansprechpartner(?:in)?|kontakt)\b"
)
SIGNATURE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:mit freundlichen grüßen|freundliche grüße|hochachtungsvoll|"
    r"absender|geschäftsführer|i\.?\s*a\.?|i\.?\s*v\.?)\b"
)
PHONE_CONTEXT_RE = re.compile(r"(?i)\b(?:tel\.?|telefon|mobil|handy|fon|phone|fax)\b")
NOISE_LINE_RE = re.compile(
    r"(?i)\b(?:iban|bic|bank|konto|ust|steuer|rechnung|angebot[- ]?nr|kundennr|"
    r"datum|seite|brh|höhe|breite|gesamt|summe|betrag|zahlbar|messwert)\b"
)
DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")

COMPANY_MARKERS = (
    "gmbh",
    "ag",
    "ug",
    "kg",
    "ohg",
    "e.k",
    "mbh",
)
ORGANIZATION_MARKERS = (
    "gemeinde",
    "stadt",
    "amt",
    "kita",
    "kindergarten",
    "schule",
    "kirche",
    "verein",
    "zweckverband",
    "kreis",
    "landkreis",
)
GENERIC_EMAIL_NAMES = {
    "info",
    "mail",
    "kontakt",
    "office",
    "verwaltung",
    "sekretariat",
    "post",
    "architekturbuero",
    "architekturbüro",
}


@dataclass
class CustomerSuggestion:
    display_name: str = ""
    company: str = ""
    entity_type: str = ""
    city: str = ""
    postal_code: str = ""
    street: str = ""
    email: str = ""
    phone: str = ""
    service_types: list[str] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: list[ExtractionEvidence] = field(default_factory=list)

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

    TEXT_EXTENSIONS = {"txt"}
    CONTACT_EXTENSIONS = {"pdf", "doc", "docx", "txt"}

    def __init__(self):
        self._converter = DocumentConverter()

    def suggest_for_folder(self, folder_path: Path, suggested_name: str = "") -> CustomerSuggestion:
        folder_path = folder_path.resolve()
        documents = self._collect_documents(folder_path)
        return self.suggest_from_documents(folder_path, suggested_name, documents)

    def suggest_from_text(
        self,
        folder_path: Path,
        suggested_name: str = "",
        indexed_text: str = "",
    ) -> CustomerSuggestion:
        """Build a suggestion from already extracted index text."""
        documents = ([{
            "path": str(folder_path / "indexed-text.txt"),
            "filename": "indexed-text.txt",
            "file_type": "txt",
            "content": indexed_text,
        }] if indexed_text else [])
        return self.suggest_from_documents(folder_path, suggested_name, documents)

    def suggest_from_documents(
        self,
        folder_path: Path,
        suggested_name: str,
        documents: list[dict],
        preferred_patterns: list[str] | None = None,
    ) -> CustomerSuggestion:
        suggestion = CustomerSuggestion()
        self._extract_from_path(folder_path, suggested_name, suggestion)
        patterns = preferred_patterns or [
            "anschreiben", "angebot", "auftrag", "auftragsbestätigung",
            "brief", "vertrag",
        ]
        eligible = [
            item for item in documents
            if str(item.get("file_type") or "").casefold() in self.CONTACT_EXTENSIONS
        ]
        preferred = [
            item for item in eligible
            if any(pattern in str(item.get("filename") or "").casefold() for pattern in patterns)
        ]
        evidence: list[ExtractionEvidence] = []
        contacts: list[Contact] = []
        for document in preferred:
            found, document_contacts = self._extract_document_evidence(document, True)
            evidence.extend(found)
            contacts.extend(document_contacts)
        if not self._has_automatic_evidence(evidence):
            for document in eligible:
                if document in preferred:
                    continue
                found, document_contacts = self._extract_document_evidence(document, False)
                evidence.extend(found)
                contacts.extend(document_contacts)
        suggestion.evidence = self._mark_automatic_evidence(evidence)
        self._apply_resolved_evidence(suggestion)
        suggestion.contacts = self._deduplicate_contacts(contacts, suggestion.evidence)
        if suggestion.contacts:
            primary = suggestion.contacts[0]
            suggestion.email = suggestion.email or primary.email
            suggestion.phone = suggestion.phone or primary.phone
        suggestion.company = suggestion.display_name
        suggestion.entity_type = self._infer_entity_type(
            suggestion.display_name,
            folder_path.name,
            "\n".join(str(item.get("content") or "")[:4_000] for item in eligible),
        )
        identity_source = str(
            (preferred or eligible or [{"path": str(folder_path)}])[0].get("path")
        )
        suggestion.evidence.extend([
            self._evidence(
                "entity_type", suggestion.entity_type, identity_source,
                folder_path.name, 0, "Kundentyp-Regeln", 0.95,
            ),
            self._evidence(
                "company", suggestion.company, str(folder_path),
                folder_path.name, 0, "Projektordner", 0.95,
            ),
        ])
        suggestion.evidence[-2].automatic = True
        suggestion.evidence[-1].automatic = True
        return suggestion

    def _collect_documents(self, folder_path: Path) -> list[dict]:
        documents = []
        total = 0
        for file_path in sorted(folder_path.rglob("*")):
            if not file_path.is_file():
                continue
            extension = file_path.suffix.lower().lstrip(".")
            if extension not in self.CONTACT_EXTENSIONS:
                continue
            text = self._extract_file_text(file_path, extension)
            if not text:
                continue
            documents.append({
                "path": str(file_path),
                "filename": file_path.name,
                "file_type": extension,
                "content": text,
            })
            total += len(text)
            if len(documents) >= 24 or total >= 500_000:
                break
        return documents

    def _extract_document_evidence(
        self, document: dict, preferred: bool
    ) -> tuple[list[ExtractionEvidence], list[Contact]]:
        text = str(document.get("content") or "")
        source_path = str(document.get("path") or "")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        evidence: list[ExtractionEvidence] = []
        contacts: list[Contact] = []
        for index, line in enumerate(lines):
            if NOISE_LINE_RE.search(line):
                continue
            context = " ".join(lines[max(0, index - 6):index + 3])
            if SIGNATURE_CONTEXT_RE.search(context):
                continue
            strong_context = bool(
                RECIPIENT_CONTEXT_RE.search(context)
                or PERSON_HINT_RE.search(context)
            )
            name_match = PERSON_HINT_RE.search(line)
            name = self._clean_name_candidate(name_match.group(1)) if name_match else ""
            if not name:
                labeled_name = CONTACT_LABEL_RE.match(line)
                if labeled_name:
                    name = self._clean_name_candidate(labeled_name.group(1))
            if not name:
                hinted = NAME_HINT_RE.match(line)
                if hinted:
                    name = self._clean_name_candidate(hinted.group(1))
            if not name and RECIPIENT_CONTEXT_RE.search(line) and index + 1 < len(lines):
                name = self._clean_name_candidate(lines[index + 1])
            email_match = EMAIL_RE.search(line)
            phone = ""
            phone_match = PHONE_RE.search(line)
            if phone_match:
                phone = self._clean_phone_candidate(phone_match.group(0), line)
            confidence = 0.94 if strong_context else (0.80 if preferred else 0.72)
            if name:
                evidence.append(self._evidence(
                    "contact_name", name, source_path, context, index,
                    "Anrede/Ansprechpartner", confidence,
                ))
            if email_match:
                email = email_match.group(0).casefold()
                evidence.append(self._evidence(
                    "email", email, source_path, context, index,
                    "E-Mail im Empfängerblock" if strong_context else "E-Mail-Fund",
                    confidence,
                ))
            if phone:
                evidence.append(self._evidence(
                    "phone", phone, source_path, context, index,
                    "Telefon im Empfängerblock" if strong_context else "Telefon-Fund",
                    confidence,
                ))
            if name:
                nearby = " ".join(lines[index:min(len(lines), index + 4)])
                nearby_email = EMAIL_RE.search(nearby)
                nearby_phone_match = PHONE_RE.search(nearby)
                nearby_phone = (
                    self._clean_phone_candidate(nearby_phone_match.group(0), nearby)
                    if nearby_phone_match else ""
                )
                if nearby_email or nearby_phone:
                    contacts.append(Contact(
                        name=name,
                        email=(
                            nearby_email.group(0).casefold()
                            if nearby_email
                            and not self._is_generic_email(nearby_email.group(0))
                            else ""
                        ),
                        phone=nearby_phone,
                    ))

        for index, line in enumerate(lines[:60]):
            street = STREET_RE.search(line)
            postal_line = next((
                candidate for candidate in lines[index:index + 4]
                if POSTAL_CITY_RE.search(candidate)
            ), "")
            postal_city = POSTAL_CITY_RE.search(postal_line)
            if street is None or postal_city is None:
                continue
            block = " ".join(lines[index:index + 4])
            context_start = " ".join(lines[max(0, index - 6):index + 4])
            strong = bool(
                RECIPIENT_CONTEXT_RE.search(context_start)
                or PERSON_HINT_RE.search(context_start)
            )
            confidence = 0.95 if strong else 0.76
            for field_name, value in (
                ("street", street.group(1).strip()),
                ("postal_code", postal_city.group(1)),
                ("city", postal_city.group(2).strip()),
            ):
                evidence.append(self._evidence(
                    field_name, value, source_path, block, index,
                    "vollständiger Adressblock", confidence,
                ))
            break
        return evidence, contacts

    @staticmethod
    def _evidence(
        field_name: str, value: str, source_path: str, excerpt: str,
        position: int, rule: str, confidence: float,
    ) -> ExtractionEvidence:
        normalized = re.sub(r"\s+", " ", value.strip()).casefold()
        if field_name == "phone":
            normalized = "".join(character for character in value if character.isdigit())
        return ExtractionEvidence(
            field_name=field_name,
            value=value.strip(),
            normalized_value=normalized,
            source_path=source_path,
            excerpt=excerpt[:300],
            position=position,
            rule=rule,
            confidence=confidence,
        )

    @staticmethod
    def _has_automatic_evidence(evidence: list[ExtractionEvidence]) -> bool:
        return any(item.confidence >= 0.9 for item in evidence)

    @staticmethod
    def _mark_automatic_evidence(
        evidence: list[ExtractionEvidence],
    ) -> list[ExtractionEvidence]:
        sources: dict[tuple[str, str], set[str]] = {}
        for item in evidence:
            sources.setdefault((item.field_name, item.normalized_value), set()).add(
                item.source_path
            )
        for item in evidence:
            item.automatic = item.confidence >= 0.9 or (
                item.confidence >= 0.7
                and len(sources[(item.field_name, item.normalized_value)]) >= 2
            )
        return evidence

    @staticmethod
    def _best_evidence(
        evidence: list[ExtractionEvidence], field_name: str,
    ) -> ExtractionEvidence | None:
        matches = [
            item for item in evidence
            if item.field_name == field_name and item.automatic
        ]
        return max(matches, key=lambda item: item.confidence, default=None)

    def _apply_resolved_evidence(self, suggestion: CustomerSuggestion):
        for field_name in ("email", "phone", "street", "postal_code", "city"):
            item = self._best_evidence(suggestion.evidence, field_name)
            if item is not None:
                setattr(suggestion, field_name, item.value)

    def _deduplicate_contacts(
        self,
        contacts: list[Contact],
        evidence: list[ExtractionEvidence],
    ) -> list[Contact]:
        automatic_names = {
            item.normalized_value for item in evidence
            if item.field_name == "contact_name" and item.automatic
        }
        unique: dict[str, Contact] = {}
        for contact in contacts:
            key = re.sub(r"\s+", " ", contact.name.strip()).casefold()
            if key not in automatic_names:
                continue
            existing = unique.setdefault(key, Contact(name=contact.name))
            existing.email = existing.email or contact.email
            existing.phone = existing.phone or contact.phone
        return [item for item in unique.values() if item.email or item.phone]

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

        suggestion.entity_type = self._infer_entity_type(
            suggestion.display_name,
            project_label,
        )

        parts = list(folder_path.parts)
        year_index = next(
            (index for index, part in enumerate(parts) if re.fullmatch(r"(?:19|20)\d{2}", part)),
            None,
        )
        if year_index is not None and year_index > 0:
            service = parts[year_index - 1].strip()
            if service:
                suggestion.service_types.append(service)

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
        except Exception:
            return ""
        return ""

    def _infer_entity_type(
        self,
        display_name: str,
        project_label: str = "",
        document_text: str = "",
    ) -> str:
        label_name = project_label.split(",", 1)[0].strip() if project_label else ""
        name_scope = label_name or display_name
        lowered = name_scope.casefold()
        if any(marker in lowered for marker in COMPANY_MARKERS):
            return "Unternehmen"
        if any(marker in lowered for marker in ORGANIZATION_MARKERS):
            return "Organisation"
        hinted_name = NAME_HINT_RE.search(document_text)
        if PERSON_HINT_RE.search(document_text) or (
            hinted_name is not None
            and bool(self._clean_name_candidate(hinted_name.group(1)))
        ):
            return "Privatperson"
        return "Unternehmen"

    def _clean_phone_candidate(self, value: str, line: str) -> str:
        if (
            NOISE_LINE_RE.search(line)
            or DATE_RE.search(line)
            or "," in value
            or re.search(r"(?i)\bfax\b", line)
        ):
            return ""
        raw = re.sub(r"\s+", " ", value).strip()
        compact = re.sub(r"\D", "", raw)
        international = raw.startswith("+") or raw.startswith("00")
        if international and raw.startswith("00"):
            raw = "+" + raw[2:]
        if not international and not raw.startswith("0"):
            return ""
        if not (7 <= len(compact) <= 15):
            return ""
        try:
            parsed = phonenumbers.parse(raw, None if international else "DE")
        except phonenumbers.NumberParseException:
            return ""
        if not (
            phonenumbers.is_possible_number(parsed)
            and phonenumbers.is_valid_number(parsed)
        ):
            return ""
        if not international and phonenumbers.region_code_for_number(parsed) != "DE":
            return ""
        phone_format = (
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
            if international
            else phonenumbers.PhoneNumberFormat.NATIONAL
        )
        return phonenumbers.format_number(parsed, phone_format)

    def _clean_name_candidate(self, value: str) -> str:
        candidate = re.split(r"\s{2,}|\t|\|", value.strip(), maxsplit=1)[0].strip(" :-")
        if not candidate or len(candidate) > 60:
            return ""
        if EMAIL_RE.search(candidate) or PHONE_RE.search(candidate):
            return ""
        if NOISE_LINE_RE.search(candidate) or DATE_RE.search(candidate):
            return ""
        if any(character.isdigit() for character in candidate):
            return ""
        words = candidate.split()
        if len(words) < 2:
            return ""
        lowered = candidate.casefold()
        if any(marker in lowered for marker in (*COMPANY_MARKERS, *ORGANIZATION_MARKERS)):
            return ""
        alpha_count = sum(character.isalpha() for character in candidate)
        if alpha_count < 5:
            return ""
        return candidate

    @staticmethod
    def _is_generic_email(email: str) -> bool:
        local = email.split("@", 1)[0].casefold()
        normalized = (
            local.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
        )
        return normalized in GENERIC_EMAIL_NAMES
