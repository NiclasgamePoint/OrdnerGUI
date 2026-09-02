"""Resource-backed adapters used by the recognition application service."""

from __future__ import annotations

from importlib.resources import files

from papagui_server.domain.customer_recognition import CustomerIdentityPolicy
from papagui_server.domain.folder_structure import normalize_identity


def load_identity_policy() -> CustomerIdentityPolicy:
    return CustomerIdentityPolicy(_load_given_names())


def _load_given_names() -> frozenset[str]:
    resource = files("papagui_server.resources").joinpath("vornamen.txt")
    try:
        values = resource.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(
        normalized
        for raw in values
        if raw.strip() and not raw.lstrip().startswith("#")
        if (normalized := normalize_identity(raw.strip()))
    )
