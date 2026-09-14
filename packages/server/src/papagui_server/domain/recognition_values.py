"""Offline validation and stable comparison keys for recognition values."""
from __future__ import annotations

import json
import re
import unicodedata

import phonenumbers
from email_validator import EmailNotValidError, validate_email


# Spaces around date separators commonly come from PDF/OCR extraction. Some of
# these strings are valid German phone numbers after punctuation is removed.
# Keep this guard shared between extraction and persisted-value validation.
_DATE_SEPARATOR = r"(?:[ \t]*[./\-‐-―][ \t]*|[ \t]+)"
_DATE_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
_DATE_MONTH = r"(?:0?[1-9]|1[0-2])"
DATE_LIKE = re.compile(
    rf"(?<!\d)(?:{_DATE_DAY}{_DATE_SEPARATOR}{_DATE_MONTH}{_DATE_SEPARATOR}\d{{2,4}}|"
    rf"\d{{4}}{_DATE_SEPARATOR}{_DATE_MONTH}{_DATE_SEPARATOR}{_DATE_DAY})(?!\d)",
)

_PHONE_EXTENSION = re.compile(
    r"(?:[ \t]*(?:ext(?:ension)?\.?|durchwahl|x|#)[ \t:]*|;ext=)[0-9]{1,10}$", re.I,
)
_PHONE_BODY = re.compile(r"\+?[0-9() /\t\-‐-―]+")
_PHONE_PREFIX = re.compile(r"(?:\+[1-9]|00[1-9]|0[1-9]|\(0[1-9])")


def phone_format_allowed(value: str, *, labelled: bool = False) -> bool:
    """Conservative document syntax, separate from stored-value normalization.

    Number-plan validation alone accepts decimal measurement sequences after
    dropping punctuation. Require an explicit national/international prefix and
    ordinary separators. Compact national digits need a nearby phone label.
    A dot in an extension label (``ext.``) is not a numeric separator.
    """
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    main = _PHONE_EXTENSION.sub("", raw).strip()
    if not _PHONE_BODY.fullmatch(main) or not _PHONE_PREFIX.match(main):
        return False
    if DATE_LIKE.fullmatch(main.strip(" ()")):
        return False
    return labelled or main.startswith(("+", "00")) or bool(re.search(r"[ ()/\t\-‐-―]", main))

_NAME_TITLES = re.compile(
    r"^(?:(?:herrn?|frau|mr|mrs|ms|miss|dr(?:[.-]ing)?|prof|med|ing|"
    r"dipl[.-]?(?:ing|kfm|kffr))\.?(?:\s+|$))+", re.I,
)
_NAME_PARTICLES = frozenset({
    "al", "bin", "binti", "da", "das", "de", "del", "della", "den", "der", "di", "dos",
    "du", "el", "ibn", "la", "le", "ten", "ter", "van", "von", "zu", "zum", "zur",
})
_NON_PERSON_WORDS = frozenset({
    "abteilung", "adresse", "angebot", "angebote", "anfrage", "anlage", "anlagen", "anschrift", "ansprechpartner",
    "ansprechpartnerin", "auftrag", "auftraggeber", "auftragnehmer", "bauantrag", "baubeschreibung",
    "bauherr", "bauherrin", "bauherrn", "bauleitung", "baustelle", "bauvorhaben", "bewehrungsplan",
    "bearbeitet", "bearbeitung", "berechnung", "bericht", "betreff", "bezeichnung", "büro", "buero", "contract",
    "datum", "date", "dokument", "dokumentation", "email", "entwurf", "erstellt", "fax", "firma",
    "für", "genehmigung", "info", "information", "ingenieurbüro", "ingenieurbuero", "inhalt",
    "inhaltsverzeichnis", "keine", "keinen", "kontakt", "kontaktperson", "kunde", "kundin",
    "landesbetrieb", "leistung", "leistungen", "leistungsbeschreibung", "leistungsverzeichnis", "mail", "mobil",
    "mobile", "name", "nachname", "nachtrag", "nachweis", "phone", "plan", "planung", "plz", "projekt",
    "projektleitung", "projektname", "protokoll", "prüfbericht", "quotation", "quote", "rechnung",
    "schalplan", "service", "statische", "statischer",
    "statisches", "statik", "team", "tel", "telefon", "telefax", "unbenannt", "unbekannt",
    "und", "unknown", "unnamed", "unterlagen", "unterschrift", "vertrag", "verwaltung", "vorname",
    "zeichnung", "zentrale", "zusammenstellung", "übersicht",
})
_DOCUMENT_NAME_WORD = re.compile(
    r"(?:(?:projekt|bau|tragwerks|ausführungs|entwurfs|genehmigungs|vor|detail|brandschutz|"
    r"schall|entwässerungs)(?:planung|plan|nachweis|berechnung|unterlagen|beschreibung)|"
    r"(?:leistungs|honorar|kosten|bau|werk|wartungs|dienstleistungs|rahmen)"
    r"(?:angebot|vertrag|beschreibung|aufstellung|verzeichnis|nachweis|berechnung|übersicht)|"
    r"(?:angebots|vertrags|auftrags|rechnungs|leistungs)"
    r"(?:anfrage|bestätigung|nummer|datum|übersicht|beschreibung|bedingungen|gegenstand|nachweis)|"
    r"(?:schluss|teil|abschlags)rechnung)", re.I,
)
_NON_PERSON_LABELS = frozenset({
    "nicht angegeben", "nicht bekannt", "nicht vorhanden", "noch offen", "not available",
    "not known", "no name", "to be determined",
})


def valid_contact_name(value: object) -> bool:
    """Check that a contact label plausibly names a person, without a name list.

    Unicode letters, initials, surname particles, apostrophes and hyphens are
    supported. Document labels and placeholders cannot supply a missing name.
    This is deliberately a syntax/evidence check, not an identity assertion.
    """
    if not isinstance(value, str):
        return False
    raw = unicodedata.normalize("NFKC", value).strip()
    if not raw or len(raw) > 120 or any(unicodedata.category(char).startswith("C") for char in raw):
        return False
    title = _NAME_TITLES.match(raw)
    name = raw[title.end():].strip() if title else raw
    if not name or LEGAL_FORM.search(name) or " ".join(name.casefold().split()) in _NON_PERSON_LABELS:
        return False
    # Commas are common in "surname, given name" but other field punctuation
    # signals that an entire document line was mistaken for a name.
    if name.count(",") > 1:
        return False
    if any(not (char.isalpha() or unicodedata.category(char).startswith("M") or char.isspace()
                or char in "'’ʼ.,-‐‑‒–—―") for char in name):
        return False
    parts = re.split(r"[\s,]+", name)
    if not 1 <= len(parts) <= 9 or any(not part for part in parts):
        return False
    words = [re.sub(r"[^\w]", "", part, flags=re.UNICODE).casefold() for part in parts]
    if any(not word or word in _NON_PERSON_WORDS or _DOCUMENT_NAME_WORD.fullmatch(word) for word in words):
        return False
    meaningful = [word for word in words if word not in _NAME_PARTICLES]
    if not meaningful or not any(len(word) > 1 for word in meaningful):
        return False
    if len(meaningful) >= 2 or title:
        return True
    # Chinese, Japanese and Korean names need not contain a separating space.
    return len(parts) == 1 and len(name) >= 2 and all(
        "CJK" in unicodedata.name(char, "") or "HANGUL" in unicodedata.name(char, "")
        or "HIRAGANA" in unicodedata.name(char, "") or "KATAKANA" in unicodedata.name(char, "")
        for char in name
    )


def normalize_candidate_value(field_name: str, value: str) -> str:
    """Return a validated comparison value, or an empty key for invalid contact data.

    Phone extensions stay distinct. Email local parts and plus tags retain their
    meaning; only the validator's documented normalization is applied. No DNS or
    network requests are performed.
    """
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not raw:
        return ""
    if field_name == "email":
        try:
            return validate_email(raw, check_deliverability=False).normalized
        except EmailNotValidError:
            return ""
    if field_name == "phone":
        if DATE_LIKE.fullmatch(raw.strip(" .;()")):
            return ""
        try:
            number = phonenumbers.parse(raw, "DE")
        except phonenumbers.NumberParseException:
            return ""
        if not phonenumbers.is_valid_number(number):
            return ""
        normalized = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        return normalized + (f";ext={number.extension}" if number.extension else "")
    if field_name in {"address", "contact"}:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return " ".join(raw.casefold().split())
        if isinstance(payload, dict):
            return json.dumps(
                {str(key): normalize_candidate_value(str(key), str(item))
                 for key, item in sorted(payload.items()) if item is not None and str(item).strip()},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
    return " ".join(raw.casefold().split())


LEGAL_FORM = re.compile(
    r"\b(?:gmbh(?:\s*&\s*co\.?\s*(?:kg|ohg))?|ug\s*\(haftungsbeschränkt\)|"
    r"ag|kg|ohg|gbr|se|eg|e\.?\s*v\.?|ltd\.?|llc|inc\.?|sarl|s\.a\.)(?!\w)", re.I,
)
