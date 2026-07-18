from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Contact:
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class ServiceType:
    id: int | None = None
    name: str = ""
    normalized_name: str = ""


@dataclass
class CustomerProject:
    id: int | None = None
    customer_id: int | None = None
    service_type_id: int | None = None
    service_type: str = ""
    folder_path: str = ""
    folder_key: str = ""
    project_label: str = ""
    project_city: str = ""
    year: int | None = None
    source: str = "folder"


@dataclass
class CustomerDataSuggestion:
    id: int | None = None
    customer_id: int | None = None
    project_id: int | None = None
    field_name: str = ""
    suggested_value: str = ""
    source_path: str = ""
    status: str = "pending"


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
