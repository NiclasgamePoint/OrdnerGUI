from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import re

from app.core.config import CustomerRecognitionOptions
from app.core.customer_models import Contact, Customer
from app.core.customer_recognition_models import RecognitionCandidate, RecognitionStats
from app.core.customer_repository import CustomerRepository
from app.core.folder_structure import normalize_identity
from app.core.index_manager import IndexManager
from app.services.customer_suggestion import CustomerSuggestion, CustomerSuggestionService


class RecognitionBlacklist:
    """Normalize and filter only automatically extracted customer fields."""

    def __init__(self, options: CustomerRecognitionOptions):
        self.emails = {value.casefold() for value in options.emails}
        self.phones = {self.normalize_phone(value) for value in options.phones}
        self.names = {normalize_identity(value) for value in options.names}
        self.addresses = {normalize_identity(value) for value in options.addresses}
        self.fragments = {
            normalize_identity(value) for value in options.text_values if value.strip()
        }

    @staticmethod
    def normalize_phone(value: str) -> str:
        prefix = "+" if value.strip().startswith("+") else ""
        return prefix + "".join(character for character in value if character.isdigit())

    def contains_fragment(self, value: str) -> bool:
        normalized = normalize_identity(value)
        return bool(normalized) and any(
            fragment in normalized for fragment in self.fragments
        )

    def email_blocked(self, value: str) -> bool:
        return bool(value) and (
            value.casefold() in self.emails or self.contains_fragment(value)
        )

    def phone_blocked(self, value: str) -> bool:
        return bool(value) and (
            self.normalize_phone(value) in self.phones or self.contains_fragment(value)
        )

    def name_blocked(self, value: str) -> bool:
        return bool(value) and (
            normalize_identity(value) in self.names or self.contains_fragment(value)
        )

    def address_blocked(self, *values: str) -> bool:
        normalized = normalize_identity(" ".join(value for value in values if value))
        if not normalized:
            return False
        return self.contains_fragment(normalized) or any(
            blocked in normalized or normalized in blocked
            for blocked in self.addresses
            if blocked
        )

    def filter_suggestion(self, suggestion: CustomerSuggestion) -> CustomerSuggestion:
        if self.email_blocked(suggestion.email):
            suggestion.email = ""
        if self.phone_blocked(suggestion.phone):
            suggestion.phone = ""
        if self.address_blocked(
            suggestion.street, suggestion.postal_code, suggestion.city
        ):
            suggestion.street = ""
            suggestion.postal_code = ""

        filtered_contacts: list[Contact] = []
        for contact in suggestion.contacts:
            if self.name_blocked(contact.name):
                continue
            email = "" if self.email_blocked(contact.email) else contact.email
            phone = "" if self.phone_blocked(contact.phone) else contact.phone
            if email or phone:
                filtered_contacts.append(Contact(
                    name=contact.name,
                    role=contact.role,
                    email=email,
                    phone=phone,
                ))
        suggestion.contacts = filtered_contacts
        return suggestion


class CustomerRecognitionService:
    """Reconcile staged project-root candidates with persistent customer data."""

    def __init__(
        self,
        index_path: Path,
        customer_database_path: Path,
        options: CustomerRecognitionOptions,
    ):
        self.index_path = index_path
        self.customer_database_path = customer_database_path
        self.options = options
        self._suggestions = CustomerSuggestionService()
        self._blacklist = RecognitionBlacklist(options)

    def synchronize(self) -> RecognitionStats:
        stats = RecognitionStats()
        if not self.options.enabled:
            return stats

        manager = IndexManager(self.index_path, initialize=False)
        repository = CustomerRepository(self.customer_database_path)
        try:
            candidates = self._load_candidates(manager)
            stats.detected = sum(len(item.folder_paths) for item in candidates)
            pending: list[RecognitionCandidate] = []
            for candidate in candidates:
                decision = repository.get_recognition_decision(candidate.signature)
                if decision is not None:
                    self._apply_stored_decision(repository, candidate, decision, stats)
                    continue
                if repository.has_previous_recognition_decision(
                    candidate.recognition_key, candidate.signature
                ):
                    candidate.reason = (
                        "Die erkannten Daten haben sich seit der letzten Entscheidung geändert."
                    )
                    candidate.suggested_customer_ids = sorted({
                        owner_id
                        for folder_path in candidate.folder_paths
                        for owner_id in self._owner_ids_for_folder(
                            repository, folder_path
                        )
                    })
                    pending.append(candidate)
                    continue
                if len(candidate.folder_paths) > 1:
                    owners_per_folder = [
                        self._owner_ids_for_folder(repository, folder_path)
                        for folder_path in candidate.folder_paths
                    ]
                    owner_ids = sorted({
                        owner_id
                        for owners in owners_per_folder
                        for owner_id in owners
                    })
                    if len(owner_ids) == 1 and all(owners_per_folder):
                        repository.apply_recognition_candidate(candidate, owner_ids[0])
                        stats.assigned += len(candidate.folder_paths)
                        continue
                    candidate.reason = (
                        "Projektordner besitzen denselben Kundennamen und Ort, sind "
                        "aber noch nicht eindeutig demselben Kunden zugeordnet."
                    )
                    candidate.suggested_customer_ids = sorted(set(
                        owner_ids + [
                            int(customer.id)
                            for customer in repository.find_by_identity(
                                candidate.display_name, candidate.city
                            )
                            if customer.id is not None
                        ]
                    ))
                    pending.append(candidate)
                    continue
                if self._process_single(repository, candidate, stats, pending):
                    continue
            repository.replace_pending_recognition_cases(pending)
            stats.pending = len(pending)
            repository.record_recognition_run(stats)
            return stats
        except Exception as error:
            stats.error = str(error)
            repository.record_recognition_run(stats)
            raise
        finally:
            manager.close()
            repository.close()

    def resolve_case(
        self,
        candidate: RecognitionCandidate,
        action: str,
        customer_id: int | None = None,
    ) -> list[int]:
        repository = CustomerRepository(self.customer_database_path)
        affected_ids: list[int] = []
        try:
            if action == "ignore":
                repository.save_recognition_decision(candidate.signature, action)
                return []
            if action == "assign":
                if customer_id is None:
                    raise ValueError("Bitte einen Bestandskunden auswählen.")
                updated = repository.apply_recognition_candidate(candidate, customer_id)
                affected_ids.append(int(updated.id))
                repository.save_recognition_decision(
                    candidate.signature, action, int(updated.id)
                )
                return affected_ids
            if action == "together":
                updated = repository.apply_recognition_candidate(candidate)
                affected_ids.append(int(updated.id))
                repository.save_recognition_decision(
                    candidate.signature, action, int(updated.id)
                )
                return affected_ids
            if action == "separate":
                for folder_path in candidate.folder_paths:
                    individual = RecognitionCandidate(
                        recognition_key=candidate.recognition_key,
                        display_name=candidate.display_name,
                        city=candidate.city,
                        folder_paths=[folder_path],
                        service_types=self._services_for_path(
                            folder_path, candidate.service_types
                        ),
                        years=self._years_for_path(folder_path, candidate.years),
                        email=candidate.email,
                        phone=candidate.phone,
                        street=candidate.street,
                        postal_code=candidate.postal_code,
                        contacts=list(candidate.contacts),
                    )
                    owner = repository.get_by_folder(folder_path)
                    updated = (
                        repository.apply_recognition_candidate(individual, owner.id)
                        if owner is not None and owner.id is not None
                        else repository.apply_recognition_candidate(individual)
                    )
                    affected_ids.append(int(updated.id))
                repository.save_recognition_decision(candidate.signature, action)
                return affected_ids
            raise ValueError("Unbekannte Prüfentscheidung.")
        finally:
            repository.close()

    def _load_candidates(self, manager: IndexManager) -> list[RecognitionCandidate]:
        individual: list[RecognitionCandidate] = []
        for root in manager.list_project_roots():
            indexed_text = manager.indexed_text_for_folder(str(root["path"]))
            suggestion = self._suggestions.suggest_from_text(
                Path(str(root["path"])),
                str(root["customer_name"]),
                indexed_text,
            )
            suggestion = self._blacklist.filter_suggestion(suggestion)
            individual.append(RecognitionCandidate(
                recognition_key=str(root["recognition_key"]),
                display_name=str(root["customer_name"]),
                city=str(root["city"]),
                folder_paths=[str(root["path"])],
                service_types=[str(root["service_type"])],
                years=[int(root["year"])],
                email=suggestion.email,
                phone=suggestion.phone,
                street=suggestion.street,
                postal_code=suggestion.postal_code,
                contacts=suggestion.contacts,
            ))

        grouped: dict[str, list[RecognitionCandidate]] = defaultdict(list)
        for candidate in individual:
            grouped[candidate.recognition_key].append(candidate)
        return [self._merge_candidates(group) for group in grouped.values()]

    def _merge_candidates(
        self, candidates: list[RecognitionCandidate]
    ) -> RecognitionCandidate:
        first = candidates[0]
        contacts: dict[tuple[str, str, str], Contact] = {}
        for candidate in candidates:
            for contact in candidate.contacts:
                key = (
                    normalize_identity(contact.name),
                    contact.email.casefold(),
                    RecognitionBlacklist.normalize_phone(contact.phone),
                )
                contacts.setdefault(key, contact)
        return RecognitionCandidate(
            recognition_key=first.recognition_key,
            display_name=first.display_name,
            city=first.city,
            folder_paths=sorted({
                path for candidate in candidates for path in candidate.folder_paths
            }),
            service_types=sorted({
                value for candidate in candidates for value in candidate.service_types
            }, key=str.casefold),
            years=sorted({value for candidate in candidates for value in candidate.years}),
            email=next((item.email for item in candidates if item.email), ""),
            phone=next((item.phone for item in candidates if item.phone), ""),
            street=next((item.street for item in candidates if item.street), ""),
            postal_code=next((item.postal_code for item in candidates if item.postal_code), ""),
            contacts=list(contacts.values()),
        )

    def _process_single(
        self,
        repository: CustomerRepository,
        candidate: RecognitionCandidate,
        stats: RecognitionStats,
        pending: list[RecognitionCandidate],
    ) -> bool:
        folder_path = candidate.folder_paths[0]
        owner_ids = self._owner_ids_for_folder(repository, folder_path)
        if len(owner_ids) > 1:
            candidate.reason = "Unterordner sind bereits verschiedenen Kunden zugeordnet."
            candidate.suggested_customer_ids = owner_ids
            pending.append(candidate)
            return False
        if len(owner_ids) == 1:
            repository.apply_recognition_candidate(candidate, owner_ids[0])
            stats.assigned += 1
            return True

        exact = repository.find_by_identity(candidate.display_name, candidate.city)
        if len(exact) == 1 and exact[0].id is not None:
            repository.apply_recognition_candidate(candidate, exact[0].id)
            stats.assigned += 1
            return True
        if len(exact) > 1:
            candidate.reason = "Mehrere Bestandskunden besitzen denselben Namen und Ort."
            candidate.suggested_customer_ids = [
                int(customer.id) for customer in exact if customer.id is not None
            ]
            pending.append(candidate)
            return False

        similar = self._similar_customers(repository, candidate)
        if similar:
            candidate.reason = "Es wurden ähnlich benannte Bestandskunden gefunden."
            candidate.suggested_customer_ids = [
                int(customer.id) for customer in similar if customer.id is not None
            ]
            pending.append(candidate)
            return False

        repository.apply_recognition_candidate(candidate)
        stats.created += 1
        return True

    def _similar_customers(
        self,
        repository: CustomerRepository,
        candidate: RecognitionCandidate,
    ) -> list[Customer]:
        candidate_name = normalize_identity(candidate.display_name)
        candidate_city = normalize_identity(candidate.city)
        matches = []
        for customer in repository.list_customers():
            customer_name = normalize_identity(customer.display_name)
            customer_city = normalize_identity(customer.city)
            cities_compatible = (
                not candidate_city or not customer_city or candidate_city == customer_city
            )
            if cities_compatible and SequenceMatcher(
                None, candidate_name, customer_name
            ).ratio() >= 0.9:
                matches.append(customer)
        return matches

    @staticmethod
    def _owner_ids_for_folder(
        repository: CustomerRepository,
        folder_path: str,
    ) -> list[int]:
        owner_ids = repository.customer_ids_within_folder(folder_path)
        inherited_owner = repository.get_by_folder(folder_path)
        if inherited_owner is not None and inherited_owner.id is not None:
            owner_ids.append(inherited_owner.id)
        return sorted(set(owner_ids))

    def _apply_stored_decision(
        self,
        repository: CustomerRepository,
        candidate: RecognitionCandidate,
        decision: dict,
        stats: RecognitionStats,
    ):
        action = str(decision.get("action") or "")
        if action == "ignore":
            stats.skipped += 1
            return
        customer_id = decision.get("customer_id")
        if action in {"assign", "together"} and customer_id:
            repository.apply_recognition_candidate(candidate, int(customer_id))
            stats.assigned += 1
            return
        if action == "separate":
            for folder_path in candidate.folder_paths:
                owner = repository.get_by_folder(folder_path)
                if owner is None:
                    individual = RecognitionCandidate(
                        recognition_key=candidate.recognition_key,
                        display_name=candidate.display_name,
                        city=candidate.city,
                        folder_paths=[folder_path],
                        service_types=self._services_for_path(folder_path, candidate.service_types),
                        years=self._years_for_path(folder_path, candidate.years),
                    )
                    repository.apply_recognition_candidate(individual)
            stats.assigned += 1
            return
        stats.skipped += 1

    @staticmethod
    def _services_for_path(path: str, fallback: list[str]) -> list[str]:
        parts = Path(path).parts
        if len(parts) >= 3 and re.fullmatch(r"\d{4}", parts[-2]):
            return [parts[-3]]
        return list(fallback)

    @staticmethod
    def _years_for_path(path: str, fallback: list[int]) -> list[int]:
        parts = Path(path).parts
        if len(parts) >= 2 and re.fullmatch(r"\d{4}", parts[-2]):
            return [int(parts[-2])]
        return list(fallback)
