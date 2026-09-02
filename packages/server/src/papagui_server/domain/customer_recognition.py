"""Pure policies for conservative customer and document recognition."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from papagui_server.domain.folder_structure import normalize_identity


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s/.-]*)?(?:\d[\s/().-]*){7,15}(?!\w)")


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    field_name: str
    value: str
    confidence: float
    rule: str
    excerpt: str


class CustomerIdentityPolicy:
    def __init__(self, given_names: frozenset[str]) -> None:
        self._given_names = given_names

    def entity_type(self, name: str) -> str:
        tokens = {normalize_identity(token) for token in name.split()}
        return "Privatperson" if tokens & self._given_names else "Unternehmen"

    @staticmethod
    def similar(left: str, right: str, *, threshold: float = 0.86) -> bool:
        first, second = normalize_identity(left), normalize_identity(right)
        return bool(first and second) and SequenceMatcher(None, first, second).ratio() >= threshold


def document_candidates(content: str) -> tuple[DocumentCandidate, ...]:
    """Extract conservative candidates while retaining human-readable evidence."""
    result: list[DocumentCandidate] = []
    seen: set[tuple[str, str]] = set()
    for field_name, values, confidence, rule in (
        ("email", _EMAIL.findall(content), 0.90, "email-pattern"),
        ("phone", _PHONE.findall(content), 0.75, "phone-pattern"),
    ):
        for raw in values:
            value = raw.strip()
            if field_name == "phone" and len(re.sub(r"\D", "", value)) < 7:
                continue
            key = (field_name, value.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append(
                DocumentCandidate(
                    field_name=field_name,
                    value=value,
                    confidence=confidence,
                    rule=rule,
                    excerpt=_excerpt(content, value),
                )
            )
    return tuple(result)


def _excerpt(content: str, value: str, *, radius: int = 80) -> str:
    position = content.casefold().find(value.casefold())
    if position < 0:
        return content[: radius * 2].strip()
    return content[max(0, position - radius) : position + len(value) + radius].strip()
