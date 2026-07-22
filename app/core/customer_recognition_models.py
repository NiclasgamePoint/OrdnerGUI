from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json

from app.core.customer_models import Contact


@dataclass
class ExtractionEvidence:
    field_name: str
    value: str
    normalized_value: str
    source_path: str
    excerpt: str
    position: int
    rule: str
    confidence: float
    automatic: bool = False


@dataclass
class RecognitionCandidate:
    recognition_key: str
    display_name: str
    city: str
    folder_paths: list[str]
    service_types: list[str]
    years: list[int]
    entity_type: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    postal_code: str = ""
    contacts: list[Contact] = field(default_factory=list)
    reason: str = ""
    suggested_customer_ids: list[int] = field(default_factory=list)
    evidence: list[ExtractionEvidence] = field(default_factory=list)

    @property
    def signature(self) -> str:
        payload = self.to_dict()
        payload.pop("reason", None)
        payload.pop("suggested_customer_ids", None)
        payload.pop("evidence", None)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "recognition_key": self.recognition_key,
            "display_name": self.display_name,
            "city": self.city,
            "folder_paths": list(self.folder_paths),
            "service_types": list(self.service_types),
            "years": list(self.years),
            "entity_type": self.entity_type,
            "email": self.email,
            "phone": self.phone,
            "street": self.street,
            "postal_code": self.postal_code,
            "contacts": [asdict(contact) for contact in self.contacts],
            "reason": self.reason,
            "suggested_customer_ids": list(self.suggested_customer_ids),
            "evidence": [asdict(item) for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, values: dict) -> "RecognitionCandidate":
        return cls(
            recognition_key=str(values.get("recognition_key") or ""),
            display_name=str(values.get("display_name") or ""),
            city=str(values.get("city") or ""),
            folder_paths=[str(value) for value in values.get("folder_paths") or []],
            service_types=[str(value) for value in values.get("service_types") or []],
            years=[int(value) for value in values.get("years") or []],
            entity_type=str(values.get("entity_type") or ""),
            email=str(values.get("email") or ""),
            phone=str(values.get("phone") or ""),
            street=str(values.get("street") or ""),
            postal_code=str(values.get("postal_code") or ""),
            contacts=[Contact(**item) for item in values.get("contacts") or []],
            reason=str(values.get("reason") or ""),
            suggested_customer_ids=[
                int(value) for value in values.get("suggested_customer_ids") or []
            ],
            evidence=[
                ExtractionEvidence(**item) for item in values.get("evidence") or []
            ],
        )


@dataclass
class RecognitionStats:
    detected: int = 0
    created: int = 0
    assigned: int = 0
    skipped: int = 0
    pending: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ContactScanStats:
    scanned_projects: int = 0
    found_fields: int = 0
    applied_fields: int = 0
    pending_fields: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
