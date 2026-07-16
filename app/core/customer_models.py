from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Contact:
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class Customer:
    id: int | None = None
    folder_path: str = ""
    folder_paths: list[str] = field(default_factory=list)
    display_name: str = ""
    entity_type: str = "Unternehmen"
    service_types: list[str] = field(default_factory=list)
    company: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    contacts: list[Contact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
