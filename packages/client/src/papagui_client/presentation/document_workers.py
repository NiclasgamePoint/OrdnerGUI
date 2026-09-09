"""Shared aggregate document statistics for the two server administration views."""

from collections.abc import Mapping


def document_worker_summary(workers: Mapping[str, object]) -> tuple[str, str]:
    def count(name: str) -> int:
        value = workers.get(name, 0)
        return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0

    summary = (
        f"Worker aktiv: {count('active_workers')} / {count('worker_limit')}"
        f" · Warteschlange: {count('queued_documents')}"
    )
    details = (
        f"Verarbeitet: {count('processed_documents')}"
        f" · Gefunden: {count('discovered_documents')}"
        f" · Wiederverwendet: {count('reused_documents')}"
        f" · Extrahiert: {count('extracted_documents')}"
        f" · Fehler: {count('failed_documents')}"
    )
    return summary, details
