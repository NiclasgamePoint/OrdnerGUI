from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
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
SALUTATION_RE = re.compile(
    r"(?i)^\s*sehr\s+geehrt(?:e|er|en)\s+(?:herrn?|frau)\s+"
    r"(?:(?:dr\.?|prof\.?)\s+)?"
    r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+){1,3})"
)
CUSTOMER_ROLE_RE = re.compile(
    r"(?i)^(?:auftraggeber|kunde|besteller|bauherr|"
    r"vertragspartner\s+auf\s+kundenseite)\s*:?[ \t]*(.*)$"
)
EXCLUDED_ROLE_RE = re.compile(
    r"(?i)\b(?:auftragnehmer|leistungserbringer|dienstleister|"
    r"geschäftsführer|absender)\b"
)
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
ROLE_LABEL_RE = re.compile(
    r"(?i)^(?:(?:funktion|position|rolle|tätigkeit)\s*:?\s*)"
    r"([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß /&+.-]{2,60})$"
)
ROLE_VALUE_RE = re.compile(
    r"(?i)^(?:architekt(?:in)?|ingenieur(?:in)?|projektleiter(?:in)?|"
    r"bauleiter(?:in)?|geschäftsführer(?:in)?|inhaber(?:in)?|"
    r"sachbearbeiter(?:in)?|assistenz|vorstand|prokurist(?:in)?)$"
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

FIRST_NAMES_PATH = Path(__file__).resolve().parent.parent / "resources" / "vornamen.txt"
CUSTOMER_NAME_RULES = {
    "persönliche Empfängeranrede",
    "kundenseitige Vertragsrolle",
    "Kundenname aus Dokumentfeld",
    "Name im frühen Empfängeradressblock",
}
NAME_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


@lru_cache(maxsize=1)
def load_first_names() -> frozenset[str]:
    """Load the bundled name list once; recognition still works if it is absent."""
    try:
        return frozenset(
            line.strip().casefold()
            for line in FIRST_NAMES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    except (OSError, UnicodeError):
        return frozenset()


def normalize_name_part(value: str) -> str:
    return re.sub(r"[^a-zäöüß'-]", "", value.casefold())


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
        primary_documents = preferred or eligible
        for document in primary_documents:
            found, document_contacts = self._extract_document_evidence(
                document, bool(preferred), folder_path.name
            )
            evidence.extend(found)
            contacts.extend(document_contacts)
        if preferred:
            preview = self._mark_automatic_evidence(evidence)
            resolved_fields = {
                item.field_name for item in preview if item.automatic
            }
            fallback_fields = {
                "contact_name", "email", "phone", "street", "postal_code", "city",
            } - resolved_fields
            for document in eligible:
                if document in preferred:
                    continue
                found, document_contacts = self._extract_document_evidence(
                    document, False, folder_path.name
                )
                evidence.extend(
                    item for item in found if item.field_name in fallback_fields
                )
                if "contact_name" in fallback_fields:
                    contacts.extend(document_contacts)
        suggestion.evidence = self._mark_automatic_evidence(evidence)
        self._apply_resolved_evidence(suggestion)
        suggestion.company = suggestion.display_name
        (
            suggestion.entity_type,
            entity_type_confidence,
            entity_type_rule,
        ) = self._infer_entity_type_with_confidence(
            suggestion.display_name,
            folder_path.name,
            "\n".join(str(item.get("content") or "")[:4_000] for item in eligible),
            suggestion.evidence,
        )
        if entity_type_confidence >= 0.9 and not any(
            item.field_name == "contact_name" and item.automatic
            for item in suggestion.evidence
        ):
            fallback_name = self._path_contact_fallback(
                folder_path, suggestion.display_name, suggestion.entity_type
            )
            if fallback_name:
                fallback = self._evidence(
                    "contact_name", fallback_name, str(folder_path), folder_path.name,
                    0, "Personenname aus Projektordner", 0.90,
                )
                fallback.automatic = True
                suggestion.evidence.append(fallback)
                contacts.append(Contact(name=fallback_name))
        suggestion.contacts = self._deduplicate_contacts(contacts, suggestion.evidence)
        if suggestion.contacts:
            personal_emails = {
                contact.email.casefold() for contact in suggestion.contacts
                if contact.email
            }
            personal_phones = {
                "".join(character for character in contact.phone if character.isdigit())
                for contact in suggestion.contacts if contact.phone
            }
            if suggestion.email.casefold() in personal_emails:
                suggestion.email = ""
            suggestion_phone = "".join(
                character for character in suggestion.phone if character.isdigit()
            )
            if suggestion_phone and suggestion_phone in personal_phones:
                suggestion.phone = ""
        identity_source = str(
            (preferred or eligible or [{"path": str(folder_path)}])[0].get("path")
        )
        suggestion.evidence.extend([
            self._evidence(
                "entity_type", suggestion.entity_type, identity_source,
                folder_path.name, 0, entity_type_rule,
                entity_type_confidence,
            ),
            self._evidence(
                "company", suggestion.company, str(folder_path),
                folder_path.name, 0, "Projektordner", 0.95,
            ),
        ])
        suggestion.evidence[-2].automatic = entity_type_confidence >= 0.9
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
        self,
        document: dict,
        preferred: bool,
        expected_folder_name: str = "",
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
            name_rule = "Anrede/Ansprechpartner"
            name_confidence = 0.94 if strong_context else (0.80 if preferred else 0.72)
            salutation = SALUTATION_RE.search(line)
            name = self._clean_name_candidate(salutation.group(1)) if salutation else ""
            if name:
                name_rule = "persönliche Empfängeranrede"
                name_confidence = 0.96
            excluded_name_context = bool(
                EXCLUDED_ROLE_RE.search(" ".join(lines[max(0, index - 2):index + 1]))
            )
            if not name and not excluded_name_context:
                name_match = PERSON_HINT_RE.search(line)
                name = (
                    self._clean_name_candidate(name_match.group(1))
                    if name_match else ""
                )
            if not name:
                labeled_name = CONTACT_LABEL_RE.match(line)
                if labeled_name:
                    name = self._clean_name_candidate(labeled_name.group(1))
            role_match = CUSTOMER_ROLE_RE.match(line)
            if not name and role_match:
                role_value = role_match.group(1).strip()
                role_person = PERSON_HINT_RE.search(role_value)
                name = self._clean_name_candidate(
                    role_person.group(1) if role_person else role_value
                )
                if not name:
                    for candidate_line in lines[index + 1:index + 3]:
                        if EXCLUDED_ROLE_RE.search(candidate_line):
                            break
                        role_person = PERSON_HINT_RE.search(candidate_line)
                        name = self._clean_name_candidate(
                            role_person.group(1) if role_person else candidate_line
                        )
                        if name:
                            break
                if name:
                    name_rule = "kundenseitige Vertragsrolle"
                    name_confidence = 0.94
            if not name:
                hinted = NAME_HINT_RE.match(line)
                if hinted:
                    name = self._clean_name_candidate(hinted.group(1))
                    if name:
                        name_rule = "Kundenname aus Dokumentfeld"
                        name_confidence = 0.95
            if not name and RECIPIENT_CONTEXT_RE.search(line) and index + 1 < len(lines):
                name = self._clean_name_candidate(lines[index + 1])
                if name:
                    name_rule = "Kundenname aus Dokumentfeld"
                    name_confidence = 0.94
            if excluded_name_context and not salutation and not role_match:
                name = ""
            email_match = EMAIL_RE.search(line)
            phone = ""
            phone_match = PHONE_RE.search(line)
            if phone_match:
                phone = self._clean_phone_candidate(phone_match.group(0), line)
            confidence = 0.94 if strong_context else (0.80 if preferred else 0.72)
            if name:
                evidence.append(self._evidence(
                    "contact_name", name, source_path, context, index,
                    name_rule, name_confidence,
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
                nearby_lines = lines[index:min(len(lines), index + 5)]
                nearby = " ".join(nearby_lines)
                nearby_email = EMAIL_RE.search(nearby)
                nearby_phone_match = PHONE_RE.search(nearby)
                nearby_phone = (
                    self._clean_phone_candidate(nearby_phone_match.group(0), nearby)
                    if nearby_phone_match else ""
                )
                role = ""
                for role_line in nearby_lines[1:3]:
                    labeled_role = ROLE_LABEL_RE.match(role_line)
                    candidate_role = (
                        labeled_role.group(1).strip()
                        if labeled_role else role_line.strip()
                    )
                    if ROLE_VALUE_RE.fullmatch(candidate_role):
                        role = candidate_role
                        break
                contacts.append(Contact(
                    name=name,
                    role=role,
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
            if index < 40:
                address_name = self._name_before_address(lines, index)
                if address_name:
                    folder_name = expected_folder_name.split(",", 1)[0].strip()
                    folder_surname = normalize_name_part(folder_name)
                    detected_surname = normalize_name_part(
                        address_name.split()[-1]
                    )
                    name_confidence = (
                        0.94
                        if strong or (
                            folder_surname
                            and detected_surname
                            and folder_surname == detected_surname
                        )
                        else 0.88
                    )
                    name_evidence = self._evidence(
                        "contact_name", address_name, source_path,
                        " ".join(lines[max(0, index - 3):index + 4]),
                        index, "Name im frühen Empfängeradressblock",
                        name_confidence,
                    )
                    evidence.append(name_evidence)
                    contacts.append(Contact(name=address_name))
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
        for field_name in ("email", "phone"):
            item = self._best_evidence(suggestion.evidence, field_name)
            if item is not None:
                setattr(suggestion, field_name, item.value)
        address = self._best_complete_address(suggestion.evidence)
        if address is not None:
            suggestion.street = address["street"].value
            suggestion.postal_code = address["postal_code"].value
            suggestion.city = address["city"].value

    @staticmethod
    def _best_complete_address(
        evidence: list[ExtractionEvidence],
    ) -> dict[str, ExtractionEvidence] | None:
        address_fields = {"street", "postal_code", "city"}
        grouped: dict[
            tuple[str, int, str],
            dict[str, ExtractionEvidence],
        ] = {}
        for item in evidence:
            if not item.automatic or item.field_name not in address_fields:
                continue
            key = (item.source_path, item.position, item.rule)
            grouped.setdefault(key, {})[item.field_name] = item
        complete = [
            values
            for values in grouped.values()
            if address_fields.issubset(values)
        ]
        if not complete:
            return None
        return max(
            complete,
            key=lambda values: (
                min(item.confidence for item in values.values()),
                sum(item.confidence for item in values.values()),
                -min(item.position for item in values.values()),
            ),
        )

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
            existing.role = existing.role or contact.role
            existing.email = existing.email or contact.email
            existing.phone = existing.phone or contact.phone
        return list(unique.values())

    def _path_contact_fallback(
        self,
        folder_path: Path,
        display_name: str,
        entity_type: str,
    ) -> str:
        if entity_type != "Privatperson":
            return ""
        candidate = display_name.strip()
        if not candidate or len(candidate) > 60:
            return ""
        lowered = candidate.casefold()
        has_company_form = self._contains_company_form(lowered)
        if has_company_form or any(marker in lowered for marker in ORGANIZATION_MARKERS):
            return ""
        if any(character.isdigit() for character in candidate):
            return ""
        words = candidate.split()
        if not 1 <= len(words) <= 4:
            return ""
        if not all(re.fullmatch(r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+", word) for word in words):
            return ""
        return candidate

    def _name_before_address(self, lines: list[str], street_index: int) -> str:
        preceding = lines[max(0, street_index - 3):street_index]
        if EXCLUDED_ROLE_RE.search(" ".join(preceding)):
            return ""
        for candidate_line in reversed(preceding):
            if (
                STREET_RE.search(candidate_line)
                or POSTAL_CITY_RE.search(candidate_line)
                or EMAIL_RE.search(candidate_line)
                or PHONE_RE.search(candidate_line)
                or EXCLUDED_ROLE_RE.search(candidate_line)
                or NOISE_LINE_RE.search(candidate_line)
            ):
                continue
            person = PERSON_HINT_RE.search(candidate_line)
            candidate = self._clean_name_candidate(
                person.group(1) if person else candidate_line
            )
            if candidate:
                return candidate
        return ""

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
        return self._infer_entity_type_with_confidence(
            display_name,
            project_label,
            document_text,
            [],
        )[0]

    def _infer_entity_type_with_confidence(
        self,
        display_name: str,
        project_label: str = "",
        document_text: str = "",
        name_evidence: list[ExtractionEvidence] | None = None,
    ) -> tuple[str, float, str]:
        label_name = project_label.split(",", 1)[0].strip() if project_label else ""
        name_scope = label_name or display_name
        lowered = name_scope.casefold()
        if self._contains_company_form(lowered):
            return "Unternehmen", 0.98, "Kundentyp-Regeln"
        if any(marker in lowered for marker in ORGANIZATION_MARKERS):
            return "Organisation", 0.96, "Kundentyp-Regeln"
        if self._firstname_in_customer_name_evidence(name_evidence or []):
            return "Privatperson", 0.97, "Vornamenliste"
        hinted_name = NAME_HINT_RE.search(document_text)
        if PERSON_HINT_RE.search(document_text) or (
            hinted_name is not None
            and bool(self._clean_name_candidate(hinted_name.group(1)))
        ):
            return "Privatperson", 0.95, "Kundentyp-Regeln"
        return "Privatperson", 0.55, "Kundentyp-Regeln"

    @staticmethod
    def _firstname_in_customer_name_evidence(
        evidence: list[ExtractionEvidence],
    ) -> bool:
        first_names = load_first_names()
        if not first_names:
            return False
        return any(
            item.field_name == "contact_name"
            and item.rule in CUSTOMER_NAME_RULES
            and any(
                word.casefold() in first_names
                for word in NAME_WORD_RE.findall(item.value)
            )
            for item in evidence
        )

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
        if self._contains_company_form(lowered) or any(
            marker in lowered for marker in ORGANIZATION_MARKERS
        ):
            return ""
        alpha_count = sum(character.isalpha() for character in candidate)
        if alpha_count < 5:
            return ""
        return candidate

    @staticmethod
    def _contains_company_form(value: str) -> bool:
        lowered = value.casefold()
        return any(
            re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", lowered)
            for marker in COMPANY_MARKERS
        )

    @staticmethod
    def _is_generic_email(email: str) -> bool:
        local = email.split("@", 1)[0].casefold()
        normalized = (
            local.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
        )
        return normalized in GENERIC_EMAIL_NAMES
