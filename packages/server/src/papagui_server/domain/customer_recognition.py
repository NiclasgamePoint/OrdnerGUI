"""Conservative party-aware recognition from local, structured document text."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import hashlib
import json
import re
from typing import Any

import phonenumbers

from papagui_server.domain.folder_structure import normalize_identity
from papagui_server.domain.recognition_values import (
    DATE_LIKE, LEGAL_FORM, normalize_candidate_value, valid_contact_name,
)

RECOGNITION_VERSION = "party-blocks-v3"
_EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[\w-]+", re.UNICODE)
_CUSTOMER = re.compile(r"\b(?:auftraggeber|bauherr(?:in)?|kunde|kundin|rechnung\s+an)\b", re.I)
_OTHER = re.compile(r"^\s*(?:(?P<own>auftragnehmer|eigenes\s+büro)|(?P<supplier>lieferant|nachunternehmer)|(?P<authority>behörde|bauamt)|(?P<project>baustelle|bauvorhaben|projekt(?:adresse)?|objektanschrift))(?=\s*:|\s*$)", re.I | re.M)
_DOC_ROLE = re.compile(r"^\s*(absender|empfänger|von|an)\s*:", re.I)
_PARTY_START = re.compile(r"^\s*(?:auftraggeber|auftragnehmer|bauherr(?:in)?|kunde|kundin|rechnung\s+an|lieferant|nachunternehmer|behörde|bauamt|baustelle|bauvorhaben|projekt(?:adresse)?|objektanschrift|absender|empfänger)\s*:", re.I)
_CONTACT_START = re.compile(r"^\s*(?:ansprechpartner(?:in)?|kontaktperson|kontakt|herr|frau)\b", re.I)
_POSITIVE = re.compile(r"\b(?:telefon|tel\.?|mobil|mobile|phone|fon)\s*:?[ \t]*", re.I)
_NEGATIVE = re.compile(r"\b(?:fax|telefax|rechnungs?(?:nummer|nr\.?)|rechnung|iban|bic|steuer(?:nummer|nr\.?)|ust[ .-]*id|datum|date|stand|erstellt|geändert|gedruckt|geburtstag|geburtsdatum|termin|gültig\s+(?:ab|bis)|flurstück|position|pos\.?|maße|menge|preis|betrag|kundennummer|konto(?:nummer)?|plz)\b", re.I)
_POSTAL = re.compile(r"^(?:(?:D|DE)-)?(?P<postal>\d{5})\s+(?P<city>[^\W\d_][\wäöüÄÖÜß .()/-]{1,70})$", re.UNICODE)
_STREET = re.compile(r"^(?P<street>[^\W\d_][\wäöüÄÖÜß .'-]{1,75}\s+\d{1,5}\s*[a-zA-Z]?(?:\s*[-/]\s*\d{1,5}[a-zA-Z]?)?)$", re.UNICODE)
_CELL_LABELS = {"telefon": "Telefon", "tel": "Telefon", "mobil": "Mobil", "email": "E-Mail", "e mail": "E-Mail", "fax": "Fax", "ansprechpartner": "Ansprechpartner", "rolle": "Rolle", "funktion": "Rolle", "kunde": "Kunde", "auftraggeber": "Auftraggeber", "straße": "Straße", "strasse": "Straße", "plz": "PLZ", "ort": "Ort"}


def _contact_line(text: str) -> bool:
    return bool(_POSITIVE.search(text) or _EMAIL.search(text) or _CONTACT_START.match(text) or _POSTAL.fullmatch(text.strip()) or _STREET.fullmatch(text.strip()) or re.match(r"\s*(?:Fax|Rolle|Funktion|E-?Mail)\s*:", text, re.I))


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    # Preserve the old constructor while explicit quality replaces probabilities.
    field_name: str
    value: str
    confidence: float
    rule: str
    excerpt: str
    normalized_value: str = ""
    party_key: str = "customer"
    party_role: str = "unknown"
    quality: str = "review"
    suggestion_type: str = "field"
    payload: dict[str, str] = field(default_factory=dict)
    source_locator: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


class CustomerIdentityPolicy:
    def __init__(self, given_names: frozenset[str]) -> None:
        self._given_names = frozenset(normalize_identity(item) for item in given_names)

    def entity_type(self, name: str) -> str:
        if LEGAL_FORM.search(name):
            return "Unternehmen"
        tokens = {normalize_identity(token) for token in name.split()}
        return "Privatperson" if tokens & self._given_names else "Unternehmen"

    @staticmethod
    def similar(left: str, right: str, *, threshold: float = 0.86) -> bool:
        first, second = normalize_identity(left), normalize_identity(right)
        if not first or not second:
            return False
        if first == second:
            return True
        # Near-identical prefixes with different legal forms are separate identities.
        first_forms = {normalize_identity(item.group()) for item in LEGAL_FORM.finditer(left)}
        second_forms = {normalize_identity(item.group()) for item in LEGAL_FORM.finditer(right)}
        if first_forms != second_forms:
            return False
        first_tokens, second_tokens = first.split(), second.split()
        if len(first_tokens) != len(second_tokens) or min(len(first), len(second)) < 6:
            return False
        # Do not merge private people solely because a long family name dominates.
        if not first_forms and (first_tokens[0] != second_tokens[0] or first_tokens[-1] != second_tokens[-1]):
            return False
        return SequenceMatcher(None, first, second).ratio() >= threshold


def _contains_identity(text: str, identities: Iterable[str]) -> bool:
    normalized = f" {normalize_identity(text)} "
    return any(f" {normalize_identity(item)} " in normalized for item in identities if normalize_identity(item))


def _blocks(content: str, blocks: Iterable[Any]) -> Iterable[tuple[str, dict[str, Any], str]]:
    supplied = list(blocks)
    if not supplied:
        supplied = [{"text": value, "kind": "paragraph"} for value in re.split(r"\n\s*\n|\f", content) if value.strip()]
    prepared: list[dict[str, Any]] = []
    for block in supplied:
        if isinstance(block, Mapping):
            data = dict(block)
        elif hasattr(block, "to_dict"):
            data = dict(block.to_dict())
        else:
            data = {key: getattr(block, key) for key in ("text", "kind", "page", "bbox", "section", "sheet", "cell", "confidence", "block_id", "order", "extraction_method", "method") if hasattr(block, key)}
        if data.get("method") and not data.get("extraction_method"):
            data["extraction_method"] = data["method"]
        prepared.append(data)
    # Spreadsheet cells are related only within one sheet row. Preserve their
    # cell range instead of borrowing an address or contact from a nearby row.
    grouped: list[dict[str, Any]] = []
    headers: dict[tuple[str, str], str] = {}
    for data in prepared:
        cell = re.fullmatch(r"([A-Z]+)(\d+)", str(data.get("cell", "")))
        row_key = (data.get("sheet"), cell.group(2)) if cell and data.get("sheet") else None
        if row_key:
            column_key = (str(data["sheet"]), cell.group(1))
            label = _CELL_LABELS.get(normalize_identity(str(data.get("text", ""))))
            if label:
                headers[column_key] = label
            elif column_key in headers:
                data = {**data, "text": headers[column_key] + ": " + str(data.get("text", ""))}
        previous = grouped[-1] if grouped else {}
        if row_key and previous.get("_row_key") == row_key:
            previous["text"] += "\n" + str(data.get("text", ""))
            previous["cell"] = str(previous["cell"]).split(":")[0] + ":" + str(data["cell"])
        elif (
            not row_key and data.get("section") and previous.get("section") == data.get("section")
            and data.get("page") == previous.get("page")
            and not _PARTY_START.match(str(data.get("text", "")))
            and len(str(previous.get("text", ""))) < 1800
            and (
                (str(data["section"]).startswith(("header", "footer")) and str(data.get("text", "")).strip())
                or (_PARTY_START.match(str(previous.get("text", ""))) and (
                    _contact_line(str(data.get("text", "")))
                    or str(previous.get("text", "")).rstrip().endswith(":")))
            )
        ):
            previous["text"] += "\n" + str(data.get("text", ""))
            previous.setdefault("block_ids", []).append(data.get("block_id", data.get("order", len(grouped))))
        else:
            grouped.append({**data, "_row_key": row_key})
    for number, data in enumerate(grouped):
        text = str(data.get("text", ""))
        locator = {key: data[key] for key in ("page", "bbox", "section", "sheet", "cell", "block_id", "block_ids", "order", "extraction_method", "confidence") if data.get(key) is not None}
        locator.setdefault("block_id", str(number))
        kind = str(data.get("kind", data.get("block_type", "paragraph")))
        group: list[str] = []
        segment = 0
        for line in text.splitlines():
            if _PARTY_START.match(line) and group:
                yield "\n".join(group), {**locator, "segment": segment}, kind
                group, segment = [], segment + 1
            group.append(line)
        if group:
            yield "\n".join(group), {**locator, "segment": segment}, kind


def _party(text: str, identities: tuple[str, ...], own: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    if _contains_identity(text, own):
        return "own_office", ("own-identity",)
    heading = "\n".join(text.splitlines()[:2])
    other = _OTHER.search(heading)
    if other:
        role = {"own": "own_office", "supplier": "supplier", "authority": "authority", "project": "project"}[str(other.lastgroup)]
        return role, ("other-party-role",)
    matched = _contains_identity(text, identities)
    explicit = bool(_CUSTOMER.search(heading))
    if matched:
        doc_role = next((_DOC_ROLE.match(line) for line in text.splitlines() if _DOC_ROLE.match(line)), None)
        return "customer", ("customer-identity-in-block",) + ((f"document-role:{doc_role.group(1).casefold()}",) if doc_role else ())
    if explicit:
        # A role label is not enough when its named party contradicts the project owner.
        label_tail = _CUSTOMER.split(heading, maxsplit=1)[-1].lstrip(" :\t")
        named_tail = re.split(r"\n|[|;]", label_tail, maxsplit=1)[0].strip()
        if identities and named_tail and not _POSITIVE.match(named_tail) and "@" not in named_tail:
            return "unknown", ("customer-role-identity-unresolved",)
        return "customer", ("explicit-customer-role",)
    return "unknown", ("party-unresolved",)


def _phone_values(line: str, kind: str) -> Iterable[tuple[str, str, tuple[str, ...]]]:
    for match in phonenumbers.PhoneNumberMatcher(line, "DE", leniency=phonenumbers.Leniency.VALID):
        start, end = match.start, match.end
        if any(date.start() <= start and date.end() >= end for date in DATE_LIKE.finditer(line)):
            continue
        # Keep label scope within a cell/semicolon; the nearest label wins.
        prefix = re.split(r"[;|]", line[:start])[-1]
        positives, negatives = list(_POSITIVE.finditer(prefix)), list(_NEGATIVE.finditer(prefix))
        positive = positives[-1].start() if positives else -1
        negative = negatives[-1].start() if negatives else -1
        if negative >= positive and negatives:
            continue
        if re.match(r"\s*(?:EUR|€|mm|cm|m²|kg|Stück)\b", line[end:], re.I):
            continue
        if kind.startswith("table") and not positives:
            continue
        if not positives and re.search(r"(?:\b[A-Z]{2}\d{2}|\d[ \t]{2,}\d)", line):
            continue
        normalized = normalize_candidate_value("phone", match.raw_string)
        if normalized:
            yield match.raw_string, normalized, ("valid-phone-plan", "contact-label" if positives else "unlabelled-phone")


def _channels(text: str, kind: str) -> Iterable[tuple[str, str, str, tuple[str, ...]]]:
    for line in text.splitlines():
        for match in _EMAIL.finditer(line):
            raw = match.group().rstrip(".")
            normalized = normalize_candidate_value("email", raw)
            if normalized:
                yield "email", raw, normalized, ("valid-email-syntax",)
        for raw, normalized, reasons in _phone_values(line, kind):
            yield "phone", raw, normalized, reasons


def _contact_segments(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    ordinary: list[str] = []
    contacts: list[tuple[str, str, str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if _CONTACT_START.match(line):
            if current:
                contacts.append(_parse_contact(current))
            current = [line]
        elif current:
            current.append(line)
        else:
            ordinary.append(line)
    if current:
        contacts.append(_parse_contact(current))
    return "\n".join(ordinary), contacts


def _parse_contact(lines: list[str]) -> tuple[str, str, str]:
    heading = re.sub(r"^\s*(?:ansprechpartner(?:in)?|kontaktperson|kontakt)\s*:?\s*", "", lines[0], flags=re.I)
    name = re.split(r"[|;,(]|\b(?:Tel\.?|Telefon|Mobil|E-?Mail)\s*:", heading, maxsplit=1, flags=re.I)[0].strip(" :")
    without_salutation = re.sub(r"^(?:herr|frau)\s+", "", name, flags=re.I)
    if valid_contact_name(without_salutation):
        name = without_salutation
    role = ""
    role_match = re.search(r"\(([^)]+)\)|\b(?:rolle|funktion)\s*:\s*([^\n;|]+)", "\n".join(lines), re.I)
    if role_match:
        role = (role_match.group(1) or role_match.group(2)).strip()
    if not valid_contact_name(name):
        name = ""
    return name, role, "\n".join(lines)


def _address(text: str) -> dict[str, str] | None:
    lines = [re.sub(r"^\s*(?:anschrift|adresse)\s*:\s*", "", line, flags=re.I).strip() for line in text.splitlines() if line.strip()]
    labelled: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"(straße|strasse|plz|ort)\s*:\s*(.+)", line, re.I)
        if match:
            labelled[match.group(1).casefold().replace("ß", "ss")] = match.group(2).strip()
    if set(labelled) >= {"strasse", "plz", "ort"} and _STREET.fullmatch(labelled["strasse"]) and re.fullmatch(r"\d{5}", labelled["plz"]) and _POSTAL.fullmatch(labelled["plz"] + " " + labelled["ort"]):
        return {"street": labelled["strasse"], "postal_code": labelled["plz"], "city": labelled["ort"]}
    for index, line in enumerate(lines):
        postal = _POSTAL.fullmatch(line)
        if postal and index:
            street = lines[index - 1]
            if _STREET.fullmatch(street) and not _NEGATIVE.search(street) and not re.match(r"postfach\b", street, re.I):
                return {"street": street, "postal_code": postal.group("postal"), "city": postal.group("city").strip()}
    return None


def _review_address(text: str) -> tuple[dict[str, str], str] | None:
    """Preserve unsupported/partial address evidence without inventing a country."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        postbox = re.fullmatch(r"Postfach\s+[\d ]+", line, re.I)
        street = bool(_STREET.fullmatch(line) and re.search(r"(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|ufer|road|street|lane|avenue)\b", line, re.I))
        leading_street = re.fullmatch(r"\d{1,5}\s+[\w .'-]+(?:Road|Street|Lane|Avenue)", line, re.I)
        if not (postbox or street or leading_street):
            continue
        payload = {"street": line, "postal_code": "", "city": ""}
        reason = "incomplete-address-block"
        if index + 1 < len(lines):
            postal = re.fullmatch(r"((?:[A-Z]{1,3}-)?\d{4,6}|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\s+([^\W\d_][\w .()/-]+)", lines[index + 1], re.I)
            if postal:
                payload.update(postal_code=postal.group(1), city=postal.group(2))
                reason = "postbox-address" if postbox else "non-domestic-address-format"
        payload["address_kind"] = "postbox" if postbox else ("incomplete" if not payload["postal_code"] else "foreign_format")
        return payload, reason
    return None


def _company(text: str, identities: tuple[str, ...]) -> str:
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*(?:auftraggeber|bauherr(?:in)?|kund(?:e|in)|rechnung\s+an|firma|absender|empfänger|von|an)\s*:\s*", "", line, flags=re.I).strip()
        if LEGAL_FORM.search(cleaned) and not re.search(r"@|\b(?:Tel|Telefon|Fax)\b", cleaned, re.I):
            if not identities or _contains_identity(cleaned, identities):
                return cleaned
    return ""


def document_candidates(
    content: str, *, customer_name: str = "", customer_aliases: Iterable[str] = (),
    own_identities: Iterable[str] = (), blocks: Iterable[Any] = (),
) -> tuple[DocumentCandidate, ...]:
    """Extract validated information with explicit party and source provenance.

    Unknown parties remain review cases. Other parties and project
    addresses never produce customer fields. Numeric confidence is retained only
    for legacy construction; quality and reasons are the assessment contract.
    """
    identities = tuple(item for item in (customer_name, *customer_aliases) if item.strip())
    own = tuple(own_identities)
    result: list[DocumentCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for text, locator, kind in _blocks(content, blocks):
        party_role, party_reasons = _party(text, identities, own)
        if party_role not in {"customer", "unknown"}:
            continue
        quality = "strong" if party_role == "customer" else "review"
        party_key = "customer" if quality == "strong" else "unresolved:" + hashlib.sha256(normalize_identity(text).encode()).hexdigest()[:20]
        source_review_reasons: tuple[str, ...] = ()
        extraction_method = str(locator.get("extraction_method", "")).casefold()
        if "ocr" in extraction_method or "tesseract" in extraction_method:
            confidence = locator.get("confidence")
            # OCR confidence is a source-quality diagnostic, not a calibrated
            # correctness probability. Missing coordinates leave block proximity
            # unproven; neither condition may promote a party or contact.
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or confidence < 0.6:
                source_review_reasons += ("ocr-quality-unverified",)
            if not locator.get("bbox") or locator.get("page") is None:
                source_review_reasons += ("ocr-layout-unverified",)

        def add(field_name: str, raw: str, normalized: str, reasons: tuple[str, ...], *, suggestion_type: str = "field", payload: dict[str, str] | None = None, target_party: str = party_key, excerpt: str = text, quality_override: str = "") -> None:
            candidate_quality = quality_override or ("review" if source_review_reasons else quality)
            key = field_name, normalized, target_party, json.dumps(locator, sort_keys=True)
            if key in seen:
                return
            seen.add(key)
            result.append(DocumentCandidate(field_name, raw, 0.0, RECOGNITION_VERSION, excerpt[:600], normalized, target_party, party_role, candidate_quality, suggestion_type, payload or {}, dict(locator), party_reasons + reasons + source_review_reasons))

        ordinary, contacts = _contact_segments(text)
        for field_name, raw, normalized, reasons in _channels(ordinary, kind):
            add(field_name, raw, normalized, reasons)
        for name, role, contact_text in contacts:
            channels = list(_channels(contact_text, kind))
            if not name:
                for field_name, raw, normalized, reasons in channels:
                    add(field_name, raw, normalized, reasons, excerpt=contact_text)
                continue
            payload = {"name": name, "role": role}
            # A block with multiple alternatives is retained as separate contact
            # variants rather than silently replacing its first email or number.
            emails = [(raw, normalized) for field_name, raw, normalized, _ in channels if field_name == "email"] or [("", "")]
            phones = [(raw, normalized) for field_name, raw, normalized, _ in channels if field_name == "phone"] or [("", "")]
            if not channels:
                continue
            for email, _ in emails:
                for phone, _ in phones:
                    contact = {**payload, "email": email, "phone": phone}
                    raw = json.dumps(contact, ensure_ascii=False, sort_keys=True)
                    target = party_key + ":contact:" + hashlib.sha256(normalize_identity(name).encode()).hexdigest()[:20]
                    add("contact", raw, normalize_candidate_value("contact", raw), ("named-contact-in-same-block",), suggestion_type="contact", payload=contact, target_party=target, excerpt=contact_text)
        if quality == "strong":
            address = _address(text)
            if address:
                raw = json.dumps(address, ensure_ascii=False, sort_keys=True)
                add("address", raw, normalize_candidate_value("address", raw), ("complete-address-in-same-block",), suggestion_type="address", payload=address)
            else:
                uncertain_address = _review_address(text)
                if uncertain_address:
                    address, reason = uncertain_address
                    raw = json.dumps(address, ensure_ascii=False, sort_keys=True)
                    add("address", raw, normalize_candidate_value("address", raw), (reason,), suggestion_type="address", payload=address, quality_override="review")
            company = _company(text, identities)
            if company:
                add("company", company, normalize_candidate_value("company", company), ("legal-form-in-customer-block",))
    return tuple(result)
